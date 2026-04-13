# Session 2026-04-13 — Résultats

## Ce qu'on a construit

### Parser ROFL2 (`decoder/rofl2_parser.py`)
- Parse un .rofl complet : header, chunks zstd, blocs (packets), metadata JSON
- Extrait 80 chunks, 1.5M blocs, 55K packets mouvement par game
- Extrait les stats des 10 joueurs (champion, KDA, CS, gold, damage, vision, role)
- Usage : `python3 decoder/rofl2_parser.py <file.rofl> --stats`

### Sections PE extraites (`decoder/`)
- `text.bin` (26MB), `rdata.bin` (4MB), `data.bin` (600KB)
- Extraites de `C:\Riot Games\League of Legends\Game\League of Legends.exe` (patch 16.7)
- `config.json` avec les RVAs des fonctions et tables trouvées
- Prêtes pour l'émulation Unicorn quand on trouvera les bons entry points

### Scripts Frida (`tools/`)
- `frida_positions_win.js` — Script Frida pour scanner les positions en mémoire
- `frida_extract_positions.py` — Wrapper Python pour l'extraction
- Bloqué par Vanguard (VirtualAllocEx refusé même en admin)

## Découvertes clés

### Le replay marche offline
- Sur Windows, couper le wifi AVANT de lancer le .rofl → ça marche
- Pas de clé serveur (GAMHS) contrairement à ce qu'on pensait
- Tout le nécessaire pour déchiffrer est dans le binaire du jeu

### Pas de SM4-CTR
- L'obfuscation utilise des **lookup tables + opérations arithmétiques**
- 65 lookup tables de 256 bytes trouvées dans .rdata
- Pattern de déchiffrement header : `xor 0x88 → table → xor 0xBD → table → sub 0x51 → ror 1`
- Mais ce pattern ne s'applique PAS aux payloads mouvement

### Structure des payloads mouvement
- Byte 0 = 0xb6 toujours
- Byte 2 = entity identifier (10 joueurs identifiables par fréquence)
- 0xfa = padding/sentinel
- L'obfuscation est multi-couche, per-patch

### Binaire packé par Packman
- Unique import : `stub.dll:packman`
- IAT chiffrée → les stubs d'init ne peuvent pas être émulés sans runtime
- Entry point dans la section `.stub`

## Ce qui est bloqué

| Approche | Statut | Blocage |
|----------|--------|---------|
| Émulation Unicorn | ❌ | IAT chiffrée par Packman |
| Frida (runtime) | ❌ | Vanguard bloque l'injection |
| Substitution simple | ❌ | Obfuscation multi-couche |
| Analyse statique | ❌ | Trop complexe per-patch |

## Pistes pour la suite

1. **Rendre le pipeline fonctionnel SANS positions** — le `extract` CLI requiert `--positions`, il faut le rendre optionnel pour review avec la timeline Riot API seule
2. **Contacter Henry Zhu (maknee)** — son decoder fonctionne, code non open source, pourrait être intéressé par une collab
3. **Loader natif Windows** — charger les sections PE en mémoire via ctypes, résoudre manuellement les relocations, appeler les fonctions de decrypt nativement (bypasse le problème IAT)
4. **Live Client API** — `record_replay` fonctionne déjà, c'est le path of least resistance

## Analyse Ghidra (nuit du 13-14 avril)

### Ghidra installé et fonctionnel
- JDK 21 + Ghidra 11.2.1 en headless sur WSL
- Binaire importé et analysé (525MB de données d'analyse)
- Script de décompilation automatique créé (`decoder/AnalyzeDecrypt.java`)
- Résultats dans `decoder/ghidra_decompiled.txt`

### Fonctions décompilées

| Fonction | RVA | Lignes | Description |
|----------|-----|--------|-------------|
| FUN_1414447f0 | 0x14447f0 | 3745 | Méga-fonction dispatch (19K bytes) - contient le switch pour tous les types de packets |
| FUN_1414495f0 | 0x14495f0 | 26 | Stack reader - lit (valeur, type) depuis la stack et applique type transform |
| FUN_1414425b0 | 0x14425b0 | 116 | Stack builder - lit les paires de la stack, convertit, stocke les résultats |
| FUN_14140b680 | 0x140b680 | 25 | Payload allocator - alloue un buffer pour le payload |

### Découvertes de l'analyse Ghidra

1. **Type transforms** (dans stack_reader) :
   - Type 0 : valeur brute
   - Type 1 : `(raw + (raw>>31) + 0x2000) >> 14` = conversion fixed-point -> int (divide by 16384 with rounding)
   - Type 2 : `raw << 16` = shift gauche 16 bits

2. **NetID construction** (dans stack_builder) :
   - `netid = global_entity_index * 0x10000 + stack_value`
   - L'index est lu depuis `[game_object+0x328]+0x224`

3. **Jump table pour packet dispatch** :
   - Table à RVA 0x1449510 (29 entries)
   - Packet 0x1c handler at 0x1446d4a
   - Handler fait: xorshift PRNG update + lecture de N valeurs depuis stack

4. **Le payload est lu depuis une stack pré-remplie** :
   - Les raw bytes du payload NE SONT PAS lus directement par le dispatch
   - Ils sont d'abord parsés et empilés par du code en amont
   - La stack contient des paires (int32 value, int32 type)
   - Le code de parsing/empilage est dans des sous-fonctions via vtable (IAT chiffrée)

### Conclusion de l'analyse

Le payload raw est **obfusqué** et parsé par des fonctions accessibles uniquement via l'IAT chiffrée (Packman). Le dispatch function lit depuis une stack pré-remplie, pas depuis le payload directement. L'obfuscation est donc dans le code de parsing initial qui est protégé par Packman.

**Prochaines étapes prioritaires :**
1. Contacter Henry Zhu (maknee) pour collaboration sur le decrypt
2. Explorer le loader natif Windows (charger les sections + résoudre IAT manuellement via GetProcAddress runtime)
3. Utiliser le pipeline existant (Live Client API + 12 detectors) pour le produit MVP

## Références

- Mowokuma/ROFL (GitHub, archivé) — émulateur Unicorn Rust
- maknee blog : https://maknee.github.io/blog/2025/League-Data-Scraping/
- Dataset HuggingFace : maknee/league-of-legends-decoded-replay-packets (700K+ replays)
- fraxiinus/roflxd — parser C# pour ROFL et ROFL2
