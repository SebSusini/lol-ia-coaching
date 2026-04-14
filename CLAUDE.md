# LoL IA Coaching

GitHub: https://github.com/SebSusini/lol-ia-coaching
Branches: `main` (CLI), `feature/web-app` (Rails web app)

## Qui est l'utilisateur
- Dev Ruby on Rails senior, pseudo LoL: Banditacciu#EUW, Emeraude 2, mid Sylas
- Travaille sur Mac (Apple Silicon), joue a LoL sur Mac et Windows
- Pragmatique, veut avancer vite, ne pas proposer d'arreter
- Un pote joue Gwen jungle
- Veut transformer l'outil en SaaS (Free/Pro 15€/Coach 30€)

## BLOCAGE PRINCIPAL — Positions par seconde

Les positions des champions chaque seconde sont la KILLER FEATURE qui differencie ce produit de Mobalytics/Porofessor. Sans ca, l'outil n'a pas assez de valeur ajoutee.

### Ce qui a ete tente (et echoue)
1. **SM4/AES/Blowfish key derivation** — 4244 combinaisons testees. ECHEC. C'est PAS du SM4.
2. **GAMHS server key capture** — la cle n'est PAS fetchee du serveur. Le replay marche OFFLINE.
3. **mitmproxy/SSLKEYLOGFILE** — certificate pinning ou mauvais process. ECHEC.
4. **LCU API/WebSocket** — pas de cle exposee. ECHEC.
5. **Emulation ARM64 Unicorn** — fonctions trouvees mais PAS les bonnes (c'etait SM4/TLS, pas le packet decrypt).
6. **Frida memory scan** — MARCHE 1 fois (57 entites mobiles) mais INCONSISTANT. Bloque sur Windows par Vanguard.

### ERREURS A NE PAS REPETER
- C'est PAS du SM4-CTR. C'est des lookup tables 255 bytes + operations arithmetiques.
- C'est PAS chiffre avec une cle du serveur. Tout est dans le binaire.
- Le binaire Mac (ARM64) et Windows (x86_64) sont DIFFERENTS. Mowokuma marchait avec Windows.
- Les agents peuvent se tromper gravement (l'agent de nuit a dit "pas de chiffrement" alors que SI).

### Ce qu'il FAUT pour decoder
5 adresses (RVAs) dans le binaire Windows `League of Legends.exe` du patch courant:
- `alloc1_rva` — fonction d'allocation #1 (a patcher)
- `alloc2_rva` — fonction d'allocation #2 (a patcher)
- `skip_rva` — fonction a skipper (return 1)
- `mov_decrypt.rva_start` — debut de la fonction decrypt mouvement
- `mov_decrypt.rva_end` — fin de la fonction

Reference (ancien patch Mowokuma 5.5):
```json
{
    "alloc1_rva": "0xf60420",
    "alloc2_rva": "0x1de520",
    "skip_rva": "0xfca950",
    "mov_decrypt": { "rva_start": "0xe45710", "rva_end": "0xe45b35" }
}
```

### Session Windows findings
- Lookup table trouvee a RVA 0x1912450
- 858 handler stubs
- Candidats: 0x780980 (118KB, 196 table refs), 0x6ad510 (5KB, 36 refs)
- Probleme: stubs retournent des pointeurs runtime non initialises

### Frida pattern (marche parfois sur Mac)
- Vitesse replay NORMALE (pas x8)
- Attendre 5+ min que les champions soient en lane
- UN SEUL attach Frida (process frais)
- Gap de 10+ secondes entre 2 scans
- Filtre: x > 2000, x < 14000, fractionnels seulement
- BLOQUE sur Windows par Vanguard

### Prochaines pistes
1. Trouver les 5 RVAs dans le binaire Windows (RE specialise, Ghidra/IDA)
2. Contacter Mowokuma / communaute RE LoL
3. Payer un freelance RE (~100-200€)
4. Retenter Frida avec le pattern exact

## Ce qui MARCHE (V5)

### Pipeline
```
Riot API Timeline → positions/min, kills, items, objectives
Live Client API (localhost:2999) → items, KDA, levels toutes les 2s (replay x8)
.rofl metadata → scoreboard complet
         ↓
   Ruby Extractor (12 detectors)
         ↓
   review.json (36 KB, 75 events)
         ↓
   LLM (Claude/OpenAI/Gemini/Mistral/Ollama) + prompt 304 lignes
         ↓
   Review de coaching + minimap HTML + progression tracking
```

### 12 Detectors
Death (zone: dive/gank/river/overextend), DeathPositionClassifier, CS, Roam, Teamfight, Objective, PositionTracker, JunglerTracker, WardTracker, ItemSpikeDetector, LaneStateDetector, ComebackDetector, DamageEfficiencyDetector

### Commandes
```bash
# Haut-niveau
bin/setup                           # Installation auto
bin/quick-review                    # Review interactive
bin/quick-review --html             # + minimap HTML
bin/quick-review --progression      # + tendances
bin/learn-champion Gwen jungle      # Auto-fill connaissances champion
bin/progress                        # Progression across games

# Scripts
ruby extractor/bin/fetch_timeline --match-id EUW1_XXX --output output/timeline.json
ruby extractor/bin/extract --timeline output/timeline.json --summoner "Name"
ruby extractor/bin/record_replay output/live.json
ruby extractor/bin/review-auto --timeline X --provider claude --html
ruby extractor/bin/batch_analyze --count 5 --summoner "Name"
python3 tools/frida_scan.py output/positions.json
python3 tools/frida_champion_tracker.py EUW1_XXX output/positions.json
```

### Structure
```
bin/                              # setup, quick-review, review, learn-champion, progress
extractor/lib/                    # extractor.rb, riot_api_client.rb, game_context.rb,
                                  # item_resolver.rb, visualizer.rb, progression_tracker.rb,
                                  # champion_knowledge_generator.rb
extractor/lib/detectors/          # 12 detectors Ruby
extractor/bin/                    # extract, fetch_timeline, record_replay, review-auto, batch_analyze
tools/                            # frida_scan.py, frida_champion_tracker.py, capture_key.py
decoder/                          # sm4_decoder.py, packet_decryptor.py, movement_decoder.py,
                                  # arm64_decoder.py, batch_movement_parser.py, rofl2_decrypt.py
prompts/review.md                 # Prompt coaching 304 lignes
prompts/champions/                # sylas_mid.md, gwen_jungle.md
prompts/roles/                    # jungle.md, mid.md, top.md
web/                              # Rails 8 app (feature/web-app branch)
docs/                             # NEXT_SESSION_BRIEFING.md, WINDOWS_SESSION_PLAN.md
```

### Web App (feature/web-app)
- Rails 8 + PostgreSQL + Tailwind
- Models: User, Replay (status workflow), ReplayJob
- Controllers: Pages, Replays (CRUD + review + minimap + coaching), Auth
- Jobs: ReplayProcessJob (Riot API → 12 detectors), CoachingReviewJob (LLM)
- Dark theme LoL-inspired, champion icons Data Dragon
- Demo auth (Riot OAuth RSO a implementer pour prod)

### Business model
- FREE: 3 reviews/semaine, API only
- PRO (15€/mois): illimite + positions + minimap + progression
- COACH (30€/mois): tout PRO + export + equipe + API
- Pay-per-review: 3€ avec positions, 1€ sans

### Conventions
- Ruby standard, classes independantes, pas de framework
- Chaque detector: prend GameContext, retourne array de timeline events
- JSON output: meta, final_stats, timeline, patterns, gold_curve, position_map
- Python pour le decoder (ROFL2, crypto, Frida)
- Prompt en francais, termes LoL en anglais
