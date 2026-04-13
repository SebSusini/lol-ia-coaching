# LoL IA Coaching

GitHub: https://github.com/SebSusini/lol-ia-coaching

## Qui est l'utilisateur
- Dev Ruby on Rails senior, pseudo LoL: Banditacciu#EUW, Emeraude 2, mid Sylas
- Travaille sur Mac (Apple Silicon), joue a LoL sur Mac et Windows
- Pragmatique, veut avancer vite, ne pas proposer d'arreter
- Un pote joue Gwen jungle

## Etat du projet (V5 — 2026-04-13)

### Pipeline complet
```
Riot API Timeline → positions/min, kills, items, objectives
Live Client API (localhost:2999) → items, KDA, levels toutes les 2s (replay x8)
Frida memory scan → positions temps reel (~57 entites mobiles)
.rofl metadata → scoreboard complet
         ↓
   Ruby Extractor (12 detectors)
         ↓
   review.json (36 KB, 75 events)
         ↓
   LLM (Claude/OpenAI/Gemini/Mistral/Ollama)
         ↓
   Review de coaching
```

### 12 Detectors
Death (zone: dive/gank/river/overextend), DeathPositionClassifier, CS, Roam, Teamfight, Objective, PositionTracker, JunglerTracker, WardTracker, ItemSpikeDetector, LaneStateDetector, ComebackDetector, DamageEfficiencyDetector

### Commandes principales
```bash
bin/setup              # Installation automatique
bin/quick-review       # Review interactive (montre les games, tu choisis)
bin/full-review        # Review avec replay live

# Scripts bas-niveau
ruby extractor/bin/fetch_timeline --match-id EUW1_XXX --output output/timeline.json
ruby extractor/bin/extract --timeline output/timeline.json --summoner "Name"
ruby extractor/bin/record_replay output/live.json        # Live recording (x8)
ruby extractor/bin/review-auto --timeline X --provider claude  # API LLM auto
ruby extractor/bin/batch_analyze --count 5 --summoner "Name"   # Batch
python3 tools/frida_scan.py output/positions.json              # Frida positions
```

### Structure
```
bin/setup, quick-review          # Onboarding
extractor/lib/detectors/         # 12 detectors Ruby
extractor/lib/riot_api_client.rb # Client Riot API v5
extractor/bin/                   # CLIs (extract, fetch_timeline, record_replay, review-auto, batch_analyze)
tools/frida_scan.py              # Scanner memoire Frida
decoder/                         # Parser ROFL2, SM4, ARM64 analysis
prompts/review.md                # Prompt coaching 304 lignes
prompts/champions/               # Connaissances par champion (gwen_jungle.md)
prompts/roles/                   # Connaissances par role (jungle.md)
docs/                            # Specs, briefings, plans
.env                             # RIOT_API_KEY + LLM keys (gitignored)
```

### Reverse engineering ROFL2
- Parser ROFL2 complet (Python): header, chunks zstd, blocs, 259 types de paquets
- Paquets de mouvement (0x001c): 57K par game, obfusques par le binaire du jeu
- Crypto dans le binaire: SM4-CTR + HMAC-SM3 + SHA-224
- Movement handler vtable ARM64: 0x1022bf318
- Lookup tables: 0x101f37a00, 0x101f37b00
- Frida PEUT s'attacher au replay viewer Mac (contourne Vanguard)
- Positions trouvees en memoire comme float32 pairs (x, y)
- Documentation: docs/NEXT_SESSION_BRIEFING.md, docs/WINDOWS_SESSION_PLAN.md

### LLM Configuration (.env)
```
LLM_PROVIDER=claude              # claude, openai, gemini, mistral, ollama
ANTHROPIC_API_KEY=               # Pour Claude
OPENAI_API_KEY=                  # Pour OpenAI
GEMINI_API_KEY=                  # Pour Gemini
MISTRAL_API_KEY=                 # Pour Mistral
```

### Conventions
- Ruby standard, classes independantes, pas de framework
- Chaque detector: prend GameContext, retourne array de timeline events
- JSON output: meta, final_stats, timeline, patterns, gold_curve, position_map
- Python pour le decoder (ROFL2, crypto, Frida)
- Prompt en francais, termes LoL en anglais
