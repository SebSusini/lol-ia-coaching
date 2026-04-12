# Briefing pour la prochaine session

## Contexte rapide

On construit un outil d'analyse de replays LoL (github.com/SebSusini/lol-ia-coaching).
Le pipeline V4 fonctionne (Riot API + Live Recording + 12 detectors).
Il manque : les positions des joueurs chaque seconde (actuellement on a 1/minute via Riot API).

## Le blocage : dechiffrement des paquets de mouvement ROFL2

Les paquets de mouvement dans le fichier .rofl sont chiffres avec SM4-CTR.
La cle est PER-GAME, pas per-patch.

### Ce qu'on sait

1. **Le client LoL ne peut PAS lancer un replay sans internet**
2. Avant de lancer, le LeagueClient fetch des metadata depuis GAMHS (Game History Service de Riot)
3. La reponse GAMHS contient probablement la cle de chiffrement
4. Le LeagueClient passe la cle au jeu (PAS via les arguments en ligne de commande)
5. Une fois le replay lance, couper internet ne l'arrete pas → la cle est deja sur le disque

### Ce qu'il faut chercher

**La cle est TEMPORAIREMENT sur le disque** entre le fetch GAMHS et le lancement du jeu.
Endroits a chercher :

1. **Webcache du client LoL** :
   - Windows: `C:\Riot Games\League of Legends\Saved\webcache\`
   - Mac: `/Applications/League of Legends.app/Contents/LoL/Saved/webcache/`
   - Surtout le Cache_Data, Session Storage, Local Storage

2. **Logs du LeagueClient** :
   - Windows: `C:\Riot Games\League of Legends\Logs\LeagueClient Logs\`
   - Mac: `/Applications/League of Legends.app/Contents/LoL/Logs/LeagueClient Logs/`
   - Chercher : `encryptionKey`, `observerEncryptionKey`, `gameKey`, toute string Base64

3. **Logs du game client** :
   - Windows: `%APPDATA%\com.riotgames.LeagueofLegends.GameClient\logs\`
   - Mac: `~/Library/Application Support/com.riotgames.LeagueofLegends.GameClient/logs/`

4. **Fichiers temporaires** :
   - Windows: `%TEMP%\` — chercher des fichiers crees au moment du lancement du replay
   - Trier par date de modification, chercher des fichiers de quelques bytes/KB crees juste avant le lancement

5. **Capture reseau** :
   - Utiliser Wireshark/Fiddler pour capturer la requete GAMHS
   - L'URL ressemble a : `https://acs.leagueoflegends.com/...` ou un endpoint league-edge
   - La reponse JSON devrait contenir la cle

### Methode de test sur Windows

```
1. Ouvrir Wireshark (ou Fiddler) et commencer la capture
2. Dans le client LoL, cliquer pour lancer un replay
3. Observer la requete GAMHS dans Wireshark
4. Chercher "encryptionKey" dans la reponse
5. Copier la valeur Base64
6. Decoder : echo "LA_CLE_BASE64" | base64 -d | xxd
```

Alternative sans Wireshark :
```
1. Lancer le replay
2. Immediatement chercher dans les logs LeagueClient le plus recent
3. grep -i "encryptionKey" dans le log
4. Ou chercher dans le webcache
```

### Si tu trouves la cle

La cle devrait etre 16 bytes (SM4-128). Pour tester :

```python
# Dans le repo lol-ia-coaching
python3 decoder/packet_decryptor.py replays/EUW1-7816865419.rofl --sm4-key "LA_CLE_EN_HEX"
```

Ou manuellement :
```python
from decoder.packet_decryptor import sm4_ctr_decrypt
key = bytes.fromhex("TA_CLE_16_BYTES_EN_HEX")
# Decrypter un paquet de mouvement et verifier si ca donne des positions valides
```

### Format des positions decodees (apres dechiffrement)

```
u16: parsing_type
u32: entity_id (0x400000ae-0x400000b7 = 10 joueurs)
f32: speed (100-600)
waypoints: delta-compressed u16 pairs
x = sign_extend(raw, 16) * 2.0 + 7358.0
y = sign_extend(raw, 16) * 2.0 + 7412.0
Positions valides : 0-15000
```

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
