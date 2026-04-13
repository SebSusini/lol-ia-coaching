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
         ↓
   HTML minimap visualization (optional)
         ↓
   Progression tracking across games
```

### 12 Detectors
Death (zone: dive/gank/river/overextend), DeathPositionClassifier, CS, Roam, Teamfight, Objective, PositionTracker, JunglerTracker, WardTracker, ItemSpikeDetector, LaneStateDetector, ComebackDetector, DamageEfficiencyDetector

### Commandes principales
```bash
# Commandes haut-niveau (bin/)
bin/setup                      # Installation automatique (Ruby, Python, deps, .env)
bin/quick-review               # Review interactive (montre les games, tu choisis)
bin/quick-review --html        # Idem + genere HTML minimap visualization
bin/quick-review --progression # Idem + affiche progression
bin/learn-champion Sylas mid   # Auto-fill champion knowledge via LLM
bin/progress                   # Suivi progression across games
bin/progress --last 10         # Derniers N
bin/progress --champion Sylas  # Filtrer par champion
bin/progress --import FILE     # Importer un review JSON
bin/review <file.rofl>         # Pipeline .rofl complet

# Scripts bas-niveau (extractor/bin/)
ruby extractor/bin/fetch_timeline --match-id EUW1_XXX --output output/timeline.json
ruby extractor/bin/extract --timeline output/timeline.json --summoner "Name"
ruby extractor/bin/record_replay output/live.json        # Live recording (x8)
ruby extractor/bin/review-auto --timeline X --provider claude  # API LLM auto
ruby extractor/bin/review-auto --timeline X --provider claude --html  # + HTML minimap
ruby extractor/bin/batch_analyze --count 5 --summoner "Name"   # Batch
python3 tools/frida_scan.py output/positions.json              # Frida positions
```

### Structure complete
```
bin/
  setup                        # Auto-install (Ruby, Python, deps, .env)
  quick-review                 # Review interactive (--html, --progression)
  review                       # Pipeline .rofl complet
  learn-champion               # Auto-fill champion knowledge via LLM
  progress                     # Progression tracking CLI (--last, --champion, --json, --import)

extractor/lib/
  extractor.rb                 # Pipeline principal (orchestre les 12 detectors)
  riot_api_client.rb           # Client Riot API v5
  game_context.rb              # Contexte de game (summoner, timeline, items)
  item_resolver.rb             # Resolution d'items via Data Dragon
  visualizer.rb                # Minimap HTML visualization (genere HTML standalone)
  progression_tracker.rb       # Progression tracking across games (KDA, CS, vision, deaths)
  champion_knowledge_generator.rb  # Genere connaissances champion/role via LLM API
  detectors/                   # 12 detectors Ruby
    death_detector.rb
    death_position_classifier.rb
    cs_state_detector.rb
    roam_detector.rb
    teamfight_detector.rb
    objective_detector.rb
    position_tracker.rb
    jungler_tracker.rb
    ward_tracker.rb
    item_spike_detector.rb
    lane_state_detector.rb
    comeback_detector.rb
    damage_efficiency_detector.rb
  formatters/
    review_formatter.rb        # Formatage de la review pour le LLM
  enrichers/
  filters/

extractor/bin/
  extract                      # Genere review JSON depuis timeline
  fetch_timeline               # Fetch Riot API timeline
  record_replay                # Mode Live (localhost:2999, replay x8)
  record_positions             # Mode Frida (wrapper Ruby)
  batch_analyze                # Mode Multi-game batch
  review-auto                  # Review auto via API LLM (--html flag)

tools/
  frida_scan.py                # Scanner memoire Frida

decoder/                       # Parser ROFL2, SM4, ARM64 analysis

prompts/
  review.md                    # Prompt coaching (304 lignes, tous roles)
  champions/                   # Connaissances par champion
    sylas_mid.md
    gwen_jungle.md
  roles/                       # Connaissances par role
    jungle.md
    mid.md
    top.md

docs/                          # Specs, briefings, plans
  NEXT_SESSION_BRIEFING.md
  WINDOWS_SESSION_PLAN.md

output/                        # JSON reviews, HTML visualizations (gitignored)
replays/                       # Fichiers .rofl (gitignored)
.env                           # RIOT_API_KEY + LLM keys (gitignored)
.env.example                   # Template de config
```

### Visualizer (visualizer.rb)
- Genere un fichier HTML standalone avec la minimap de Summoner's Rift
- Affiche positions du joueur, morts (avec zone), objectifs, events
- Utilise a travers le flag --html sur bin/quick-review ou extractor/bin/review-auto
- Prend un review JSON en entree, genere un HTML en sortie

### Progression Tracker (progression_tracker.rb)
- Stocke les stats de chaque game analysee (KDA, CS/min, vision, deaths, winrate)
- Calcule les tendances (amelioration/degradation)
- Accessible via bin/progress ou le flag --progression de bin/quick-review
- Supports: --last N, --champion NAME, --json, --import FILE
- Enregistrement automatique lors des bin/quick-review

### Champion Knowledge Generator (champion_knowledge_generator.rb)
- Genere les connaissances champion+role via API LLM (Claude, OpenAI, Gemini, Mistral)
- Fichiers stockes dans prompts/champions/ (ex: sylas_mid.md)
- Inclut: power spikes, combos, matchups, erreurs communes, tips
- Fallback: template vide avec [TODO] si pas de cle API
- Accessible via bin/learn-champion

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
