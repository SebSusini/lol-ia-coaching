# LoL IA Coaching — lol-ia-coaching

GitHub: https://github.com/SebSusini/lol-ia-coaching

## Qui est l'utilisateur
- Dev Ruby on Rails senior, pseudo LoL: Banditacciu#EUW, Emeraude 2, mid Sylas
- Travaille sur Mac (Apple Silicon), joue a LoL sur Mac et Windows
- Pragmatique, veut avancer vite, ne pas proposer d'arreter

## Etat du projet (2026-04-13)

### Ce qui MARCHE (V4)
Pipeline complet avec 12 detectors :
1. Riot API Timeline → kills, items, CS, gold, XP, positions par minute, objectives
2. Live Client API (localhost:2999) → items, KDA, levels en temps reel pendant replay x8
3. .rofl metadata → scoreboard complet fin de game
4. 12 detectors Ruby : Death (avec zone classification), CS, Roam, Teamfight, Objective, Jungler tracking, Ward tracking, Item spike, Lane state, Comeback, Damage efficiency, Position tracker
5. Review de coaching via Claude Code avec prompt template

### Ce qui NE MARCHE PAS ENCORE
Dechiffrement des paquets de mouvement ROFL2 pour positions chaque seconde.
**Bloquer** : la cle SM4 est per-game, envoyee par le serveur GAMHS au client LoL avant le lancement du replay. Elle n'est PAS dans le fichier .rofl.
**Solution** : intercepter la cle via Wireshark/Fiddler (voir docs/WINDOWS_SESSION_PLAN.md)

## Architecture

```
.rofl (metadata)     → scoreboard, KDA, damage, items
Riot API Timeline    → events (kills, items, objectives), positions par minute
Live Client API      → snapshots toutes les 2s pendant replay x8
                           ↓
                    [Ruby Extractor + 12 detectors]
                           ↓
                    review.json (36 KB, 75 events)
                           ↓
                    [Claude Code] → review de coaching
```

## Commandes

```bash
# Voir les dernieres games
ruby -r dotenv/load -e '...' (voir README.md)

# Fetch timeline Riot API
ruby extractor/bin/fetch_timeline --match-id EUW1_XXXXXXXXXX --output output/timeline.json

# Enregistrer live data pendant replay x8
ruby extractor/bin/record_replay output/live.json

# Extraire la review
ruby extractor/bin/extract --timeline output/timeline.json --summoner "Banditacciu" --output output/review.json

# Parser un .rofl (metadata + blocs)
python3 decoder/sm4_decoder.py replays/game.rofl --stats
```

## Crypto ROFL2 — Resume

Le format ROFL2 (patch 14.11+) :
- Header: RIOT + version 02 00 + file_hash (8 bytes) + game_version string
- Chunks: header 17 bytes + payload zstd compressed (PAS de Blowfish, contrairement a ROFL1)
- Metadata: JSON a la fin du fichier (gameLength, lastGameChunkId, lastKeyFrameId, statsJson)
- Blocs dans les chunks: marker + timestamp + length + packet_id + param + payload

Les paquets de mouvement (0x001c, 57K par game) sont chiffres avec SM4-CTR.
La cle vient du serveur GAMHS (Game History Service), pas du fichier.

Le binaire LoL Mac (universal: x86_64 + ARM64, tourne en ARM64 natif) contient :
- SM4 encrypt/decrypt, SM3 hash, SHA-224
- Movement handler vtable a 0x1022bf318 (ARM64), packet_id 0x1c confirme
- Streaming decrypt functions dans la region 0x101c89000-0x101c8d000

Tout le reverse engineering est documente dans docs/NEXT_SESSION_BRIEFING.md

## Structure

```
decoder/
  sm4_decoder.py          # Parser ROFL2 complet + SM4 implementation
  packet_decryptor.py     # HMAC-SM3-CTR + analyse entropie
  movement_decoder.py     # Extraction paquets mouvement + Unicorn x86_64
  arm64_decoder.py        # Analyse ARM64 + Unicorn ARM64
  arm64_movement_crack.py # Brute-force handlers ARM64
  batch_movement_parser.py # Analyse format batch
extractor/
  lib/
    riot_api_client.rb    # Client Riot API v5
    extractor.rb          # Orchestrateur principal
    game_context.rb       # Context de game (participants, timeline)
    item_resolver.rb      # Resolution item IDs → noms via Data Dragon
    filters/              # MidLaneFilter (a generaliser)
    detectors/            # 12 detectors (death, cs, roam, teamfight, objective,
                          #   jungler, ward, item_spike, lane_state, comeback,
                          #   damage_efficiency, position_tracker)
    enrichers/            # Context enricher (placeholder)
    formatters/           # ReviewFormatter (JSON compact)
  bin/
    extract               # CLI extraction
    fetch_timeline        # CLI fetch Riot API
    record_replay         # Enregistrement live pendant replay x8
prompts/
  review.md              # Prompt Claude pour reviews (tous roles)
docs/
  NEXT_SESSION_BRIEFING.md    # Briefing technique complet
  WINDOWS_SESSION_PLAN.md     # Plan capture cle sur Windows
  superpowers/specs/          # Design spec originale
bin/
  review                 # Orchestrateur shell
.env                     # RIOT_API_KEY + summoner info (gitignored)
.env.example             # Template
```

## Conventions
- Ruby standard, pas de framework, classes independantes
- Chaque detector: prend un GameContext, retourne un array de timeline events
- JSON output suit le schema: meta, final_stats, timeline, patterns, gold_curve, position_map
- Tests: RSpec dans extractor/spec/ (a ecrire)
- Python pour le decoder (parser ROFL2, crypto)
- Pas de Rust necessaire (Mowokuma abandonne)
