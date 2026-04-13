# Briefing pour la prochaine session

## Contexte rapide

On construit un outil d'analyse de replays LoL (github.com/SebSusini/lol-ia-coaching).
Le pipeline V4 fonctionne (Riot API + Live Recording + 12 detectors).
Il manque : les positions des joueurs chaque seconde (actuellement on a 1/minute via Riot API).

## CORRECTION IMPORTANTE (session 2026-04-13)

**La session precedente avait TORT sur plusieurs points :**
1. ~~SM4-CTR encryption~~ → L'obfuscation utilise des **lookup tables 255 bytes + operations arithmetiques**, PAS SM4
2. ~~Cle per-game du serveur GAMHS~~ → **Le replay marche OFFLINE sur Windows**, la cle n'est PAS fetchee du serveur
3. ~~La cle est temporairement sur le disque~~ → Tout le necessaire est dans le **binaire du jeu** (lookup tables dans .rdata)

### Ce qu'on sait MAINTENANT

1. **Le replay fonctionne OFFLINE sur Windows** (couper le wifi avant de lancer = OK)
2. Le client lance juste: `League of Legends.exe "path.rofl" -Region=EUW -PlatformID=EUW1 -Locale=fr_FR`
3. **Pas de cle passee en argument**, pas de fetch serveur
4. L'obfuscation change **a chaque patch** (lookup tables + offsets de champs)
5. L'encryption est une combinaison de: lookup table initiale → operations arithmetiques (mul, add, sub) → lookups supplementaires

### Format ROFL2 decode (nouveau)

```
Fichier ROFL2:
  [0x00-0x03] "RIOT"
  [0x04-0x05] version = 2
  [0x06-0x07] per-patch field
  [0x08-0x0F] per-patch key (8 bytes, identique pour toutes les games d'un meme patch)
  [0x10-0x1D] game version string (null-terminated)
  [padding zeros]
  [sequential chunks...]
  [256-byte signature]
  [JSON metadata (110KB)]
  [u32 metadata_length]
```

### Structure des chunks

```
Chunk header (17 bytes):
  u32 chunk_id
  u8  chunk_type
  u32 chunk_id_2
  u32 uncompressed_length
  u32 compressed_length
  [zstd compressed data]
```

### Structure des blocs (packets) dans les chunks

Chaque chunk decompresse contient des blocs avec un **marker byte** (bitmask) :
- bit 7 (0x80): timestamp relatif u8 (ms delta) vs absolu float32
- bit 6 (0x40): reutilise le packet_id precedent
- bit 5 (0x20): param relatif u8 (delta) vs absolu u32
- bit 4 (0x10): longueur u8 vs u32

```
Block format:
  u8  marker (bitmask)
  [timestamp: u8 or f32]
  [length: u8 or u32]
  [packet_id: u16 (or reuse previous)]
  [param: u8 delta or u32 absolute]
  [payload: length bytes]
```

### Statistiques typiques d'un replay de 25 min

- ~80 chunks (mix keyframes et game chunks)
- ~1.5 million de blocs
- ~55,000 packets mouvement (0x001c)
- ~525,000 ReplicationData (0x01ea)

### Obfuscation des payloads

Les payloads sont obfusques avec un schema **per-patch** :
- Lookup table 255 bytes dans .rdata du binaire (trouvee a RVA 0x1912450 pour patch 16.7)
- Operations arithmetiques supplementaires (multiply, add, subtract)
- Les offsets des champs dans les packets changent aussi a chaque patch
- **Ce n'est PAS du SM4-CTR** comme suppose precedemment

### Format des positions decodees (apres desobfuscation)

```
PathPacket (packet_id 0x001c):
  u16: parsing_type
  u32: entity_id (0x400000ae-0x400000b7 = 10 joueurs)
  f32: speed (100-600)
  [optional byte if parsing_type & 1]
  waypoints: delta-compressed i16 pairs
    x = sign_extend(raw, 16) * 2.0 + 7358.0
    y = sign_extend(raw, 16) * 2.0 + 7412.0
  Positions valides : 0-15000
```

### Approche de desobfuscation

Deux projets de reference :
1. **Mowokuma/ROFL** (Rust) — emule les fonctions de dechiffrement avec Unicorn (x86_64)
   - Extrait .text, .data, .rdata du binaire
   - Charge dans Unicorn, appelle les fonctions de decrypt
   - Intercepte les ecritures memoire pour capturer les valeurs dechiffrees
   - Necessite un "patch file" avec les RVA des fonctions (change chaque patch)
   - Repo archive, derniere release: patch 5.5 (mars 2025)

2. **maknee (Henry Zhu)** — "exception emulator" natif
   - Meme principe mais plus rapide (hooks CPU natifs au lieu d'emulation)
   - A publie 700K+ replays decodes sur HuggingFace
   - Code source non publie

### Ce qu'il faut faire

**Option A (emulation Unicorn)** — En cours
- Sections PE extraites : text.bin (26MB), rdata.bin (4MB), data.bin (600KB)
- Lookup table trouvee a RVA 0x1912450
- 858 handler stubs trouves (pattern: sub rsp,28; TLS access)
- Probleme: les stubs retournent des pointeurs runtime non initialises
- Prochaine etape: trouver les RVA des fonctions decrypt directement
  - Candidats: 0x780980 (118KB, 196 table refs), 0x6ad510 (5KB, 36 refs)
  - Ou: analyser plus finement les call chains des stubs

**Option B (Frida runtime)** — Backup
- Frida peut s'attacher au processus du replay pendant qu'il tourne
- Intercepter les positions dechiffrees en memoire
- Avantage: marche sans reverse engineering de l'obfuscation
- Inconvenient: necessite de lancer le replay

**Option C (Dataset HuggingFace)**
- maknee/league-of-legends-decoded-replay-packets sur HuggingFace
- 700K+ replays deja decodes en JSONL
- Contient WaypointGroup, CastSpellAns, UnitApplyDamage, etc.
- Mais: pas NOS replays, uniquement ceux de son dataset

## Architecture du projet

```
lol-ia-coaching/
├── decoder/
│   ├── sm4_decoder.py          # Parser ROFL2 + SM4 implementation
│   ├── packet_decryptor.py     # HMAC-SM3-CTR + entropie analysis
│   ├── movement_decoder.py     # Extraction paquets mouvement
│   ├── arm64_decoder.py        # Analyse ARM64 + Unicorn
│   └── arm64_movement_crack.py # Tentatives de crack
├── extractor/                  # Ruby pipeline (12 detectors)
│   ├── lib/detectors/          # Death, CS, Roam, Teamfight, Objective,
│   │                           # Jungler, Ward, ItemSpike, LaneState,
│   │                           # Comeback, DamageEfficiency, PositionTracker
│   ├── bin/
│   │   ├── extract             # CLI extraction
│   │   ├── fetch_timeline      # Fetch Riot API
│   │   └── record_replay       # Enregistre live data pendant replay x8
│   └── lib/riot_api_client.rb  # Client Riot API
├── prompts/review.md           # Prompt Claude pour les reviews
├── bin/review                  # Orchestrateur
├── .env                        # RIOT_API_KEY + summoner info
└── docs/                       # Specs et ce briefing
```

## Ce qui marche aujourd'hui

1. `ruby extractor/bin/fetch_timeline --match-id EUW1_XXXXXXXXXX` → timeline Riot API
2. `ruby extractor/bin/record_replay output/live.json` → live data pendant replay x8
3. `ruby extractor/bin/extract --timeline output/timeline.json --summoner "Pseudo"` → review JSON
4. Donner le JSON a Claude Code → review de coaching complete

## Crypto du binaire (reference)

### Strings dans le binaire
- `"encryptionKey"` : VA 0x102070daa (x86_64) — le code qui LIT la cle
- `"observerEncryptionKey"` : VA 0x102072ded (x86_64)

### Fonctions ARM64
- SM4 key expansion: 0x101ccfc8c
- SM4-CTR encrypt: 0x101ccfdd8
- Streaming decrypt: 0x101ca3e20, 0x101ca402c, 0x101ca5354, 0x101ca8600
- Movement handler vtable: 0x1022bf318 (packet_id 0x1c confirme)
- Packet handler region: 0x101c89000-0x101c8d000

### Fonctions x86_64
- SM4_encrypt: 0x101f85a50
- SM4_decrypt: 0x101f86480
- SM3_compress: 0x101f83080
- Decrypt dispatchers (vtable): 0x101f78c60, 0x101f78cf0
- Streaming decrypt (XOR loop): 0x101f779c0, 0x101f78230
- Keystream generator (HMAC-CTR): 0x101ed1000

## Resume de la session marathon

- Spec du projet + design complet
- Pipeline V1 → V4 en une soiree
- Parser ROFL2 Python complet (57K paquets de mouvement extraits)
- Reverse engineering du binaire Mac (ARM64 + x86_64)
- Crypto identifiee : SM4-CTR + HMAC-SM3 + SHA-224
- Vtables, dispatch tables, handler functions trouves
- Emulateur Unicorn ARM64 fonctionnel
- Blocage : cle de session per-game pas dans le fichier .rofl
- Decouverte : la cle vient du serveur GAMHS, elle est temporairement sur le disque

**La prochaine etape : capturer la requete GAMHS (Wireshark) ou trouver la cle dans le cache/logs.**
