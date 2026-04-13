# LoL IA Coaching

Outil d'analyse de replays League of Legends avec coaching IA.
Analyse tes games, detecte tes erreurs recurrentes, progresse.

**5 providers LLM** : Claude, OpenAI, Gemini, Mistral, Ollama
**12 detectors** : death zones, CS, roam, teamfight, objectives, jungler, vision, items, comeback, damage...
**4 modes d'utilisation :**
- **Mode Rapide** : Riot API seulement, pas besoin de replay (positions par minute)
- **Mode Live** : lance le replay en x8 + capture live data (items, KDA, events)
- **Mode Frida** : lance le replay + capture positions en memoire (positions en temps reel)
- **Mode Multi-game** : analyse croisee de plusieurs games pour trouver tes patterns d'erreurs

## Quick Start

```bash
git clone https://github.com/SebSusini/lol-ia-coaching.git
cd lol-ia-coaching
bin/setup           # Installe tout (Ruby, Python, dependances)
bin/quick-review    # Lance ta premiere review interactive
```

## Setup

### Prerequis (Mac et Windows)

| Outil | Mac | Windows |
|---|---|---|
| **Ruby 3.x** | `brew install ruby` | [rubyinstaller.org](https://rubyinstaller.org/) |
| **Python 3.10+** | Pre-installe | [python.org](https://www.python.org/downloads/) |
| **Git** | Pre-installe | [git-scm.com](https://git-scm.com/) |
| **Client LoL** | Installe normalement | Installe normalement |
| **Cle API Riot** | [developer.riotgames.com](https://developer.riotgames.com/) | Idem |

### Prerequis supplementaires par mode

| Mode | Prerequis |
|---|---|
| **Rapide** | Aucun (juste Ruby + cle API Riot) |
| **Live** | Client LoL pour lancer les replays |
| **Frida** | `pip install frida frida-tools` |
| **Multi-game** | Idem que le mode choisi |

### Installation

`bin/setup` fait tout automatiquement :
- Verifie Ruby et Python
- Installe les dependances (bundle install, pip install)
- Cree `.env` a partir de `.env.example`
- Guide la configuration (cle API Riot, pseudo)

Ou manuellement :

```bash
git clone https://github.com/SebSusini/lol-ia-coaching.git
cd lol-ia-coaching

cd extractor && bundle install && cd ..
pip install frida frida-tools zstandard

cp .env.example .env
# Editer .env avec ta cle API Riot et ton pseudo
```

### Configuration (.env)

```bash
# Cle API Riot (expire toutes les 24h, renouveler sur developer.riotgames.com)
RIOT_API_KEY=RGAPI-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

# Ton pseudo LoL
RIOT_SUMMONER_NAME=TonPseudo
RIOT_TAG_LINE=EUW
RIOT_REGION=europe
RIOT_PLATFORM=euw1

# LLM Provider (claude, openai, gemini, mistral, ollama)
LLM_PROVIDER=claude

# API Keys (celle de ton provider)
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
GEMINI_API_KEY=
MISTRAL_API_KEY=
```

## Commandes

### Commandes principales

| Commande | Description |
|---|---|
| `bin/setup` | Installation automatique (Ruby, Python, dependances, .env) |
| `bin/quick-review` | Review interactive : montre tes games, tu choisis, review generee |
| `bin/quick-review --html` | Idem + genere la visualisation minimap HTML |
| `bin/quick-review --progression` | Idem + affiche ta progression |
| `bin/progress` | Suivi de progression across games |
| `bin/learn-champion Sylas mid` | Genere les connaissances champion/role via LLM |
| `bin/review <file.rofl>` | Pipeline complet : decode .rofl + Riot API + review |

### Commandes bas-niveau

| Commande | Description |
|---|---|
| `ruby extractor/bin/fetch_timeline --match-id EUW1_XXX --output output/timeline.json` | Fetch la timeline Riot API |
| `ruby extractor/bin/extract --timeline output/timeline.json --summoner "Name"` | Genere le review JSON |
| `ruby extractor/bin/review-auto --timeline X --provider claude` | Review automatique via API LLM |
| `ruby extractor/bin/review-auto --timeline X --provider claude --html` | Review auto + minimap HTML |
| `ruby extractor/bin/record_replay output/live.json` | Enregistre un replay live (x8) |
| `ruby extractor/bin/batch_analyze --count 5 --summoner "Name"` | Analyse multi-game batch |
| `python3 tools/frida_scan.py output/positions.json` | Scanner memoire Frida |

## Utilisation

### Mode 1 — Rapide (Riot API seul, pas de replay)

Le plus simple. Analyse une game a partir de son match ID.

```bash
# Voir tes dernieres games
ruby extractor/bin/fetch_timeline --match-id EUW1_XXXXXXXXXX --output output/timeline.json

# Generer la review
ruby extractor/bin/extract --timeline output/timeline.json --summoner "TonPseudo" --output output/review.json

# Lire la review dans Claude Code
# "Lis prompts/review.md puis analyse output/review.json"
```

**Ce que tu obtiens :** positions par minute, kills avec localisation (dive/gank/overextend), CS/gold/XP courbes, items timing, teamfights, objectifs, jungler tracking, vision, damage efficiency.

### Mode 2 — Live Recording (replay x8)

Lance le replay dans le client LoL en x8 et capture les donnees en temps reel.

```bash
# 1. Lance le replay dans le client LoL
# 2. Mets en x8
# 3. Lance le recorder :
ruby extractor/bin/record_replay output/live.json

# 4. Quand le replay est fini, genere la review :
ruby extractor/bin/extract --timeline output/timeline.json --live output/live.json --summoner "TonPseudo"
```

**Ce que tu obtiens en plus :** items a chaque mort (comparaison avec l'adversaire), events en temps reel (kills avec noms).

### Mode 3 — Frida (positions en memoire)

Capture les positions de tous les champions directement depuis la memoire du jeu.

```bash
# 1. Lance le replay dans le client LoL
# 2. Lance le scanner Frida :
python3 tools/frida_scan.py output/positions.json

# 3. Laisse tourner pendant le replay
# 4. Les positions de ~30-60 entites sont capturees toutes les 2 secondes
```

**Ce que tu obtiens en plus :** positions en temps reel de tous les champions et entites.

> **Attention anti-cheat (Vanguard)** : Frida lit la memoire en lecture seule d'un replay offline, pas d'une game en ligne. Le replay viewer est un processus separe, pas protege par Vanguard. Cependant : ne jamais utiliser Frida sur un processus de game live — Vanguard detecte l'injection de code et cela peut entrainer un ban permanent. Le mode Frida est concu uniquement pour les replays offline.

### Mode 4 — Multi-game Review

Analyse croisee de plusieurs games pour trouver tes erreurs recurrentes.

```bash
# Via quick-review (interactif)
bin/quick-review
# Choisis "all" pour analyser les 5 dernieres games

# Ou via batch (automatique)
ruby extractor/bin/batch_analyze --count 5 --summoner "TonPseudo"
```

**Ce que tu obtiens :** patterns d'erreurs recurrentes (zones de mort, vision, timing, damage).

## Minimap Visualization

Genere une visualisation HTML de la game sur la minimap : positions, morts, objectifs, wards.

```bash
# Via quick-review
bin/quick-review --html
# Genere output/EUW1_XXXX_visual.html

# Via review-auto
ruby extractor/bin/review-auto --timeline output/timeline.json --provider claude --html

# Ouvre le fichier HTML dans ton navigateur
open output/EUW1_XXXX_visual.html
```

La visualisation affiche :
- La minimap de Summoner's Rift en fond
- Les positions du joueur a chaque minute (trail)
- Les morts marquees avec leur zone (dive, gank, river, overextend)
- Les objectifs (dragons, barons, heralds)
- Les events de la timeline

## Progression Tracking

Suis ta progression game apres game : KDA, CS/min, vision, deaths, winrate.

```bash
# Voir ta progression
bin/progress

# Filtrer par champion
bin/progress --champion Sylas

# Les N dernieres games
bin/progress --last 10

# Importer une review existante
bin/progress --import output/EUW1_XXXX_review.json

# Export JSON
bin/progress --json
```

Exemple de rapport :

```
=== PROGRESSION (5 games) ===
Win rate: 60% (3W 2L)
KDA moyen: 6.2/4.1/7.8 (3.4)
CS/min: 7.2 → 7.8 (amelioration)
Deaths: 5.2 → 3.8 (amelioration)
Vision score: 18 → 24 (amelioration)
```

La progression est enregistree automatiquement lors des `bin/quick-review`.

## Auto-learning Champions

Genere automatiquement les connaissances champion+role via LLM pour des reviews plus precises.

```bash
# Generer les connaissances pour un champion
bin/learn-champion Gwen jungle
bin/learn-champion Sylas mid
bin/learn-champion "Bel'Veth" jungle

# Forcer le re-generation
bin/learn-champion Ahri mid --force
```

Si une cle API LLM est configuree dans `.env`, le fichier est rempli automatiquement avec :
- Power spikes et timing
- Combos et mecaniques
- Matchups cles
- Erreurs communes
- Tips specifiques au role

Sans cle API, un template vide est genere a remplir manuellement.

Les fichiers sont sauvegardes dans `prompts/champions/` (ex: `sylas_mid.md`, `gwen_jungle.md`) et sont automatiquement inclus dans les prochaines reviews.

## 12 Detectors

| Detector | Ce qu'il detecte |
|---|---|
| **Death** | Chaque mort avec zone (dive/gank/river/overextend), gold non depense |
| **Death Position** | Classification de la position de mort (sous ta tour, centre lane, etc.) |
| **CS State** | CS/gold/level diff vs adversaire toutes les 5 min |
| **Roam** | Roams detectes par position, destination, resultat |
| **Teamfight** | Cluster de kills, participation, resultat |
| **Objective** | Dragons, Barons, Heralds — present ou absent |
| **Position Tracker** | Position par minute de tous les joueurs (Riot API) |
| **Jungler Tracker** | Position du jungler ennemi + niveau de danger |
| **Ward Tracker** | Vision score, wards posees/tuees, avantage vision |
| **Item Spike** | Timing des items majeurs vs adversaire |
| **Lane State** | Gold/CS/level diff au moment de chaque mort |
| **Comeback** | Detection des momentum shifts (gold diff change de signe) |
| **Damage Efficiency** | Damage/gold ratio vs adversaire et equipe |

## Structure du projet

```
lol-ia-coaching/
├── bin/                       # Commandes principales
│   ├── setup                  # Installation automatique
│   ├── quick-review           # Review interactive (--html, --progression)
│   ├── review                 # Pipeline .rofl complet
│   ├── learn-champion         # Auto-fill champion knowledge via LLM
│   └── progress               # Suivi de progression CLI
├── extractor/                 # Pipeline Ruby (12 detectors)
│   ├── bin/
│   │   ├── extract            # Genere la review JSON
│   │   ├── fetch_timeline     # Fetch Riot API
│   │   ├── record_replay      # Mode Live (localhost:2999)
│   │   ├── record_positions   # Mode Frida (wrapper Ruby)
│   │   ├── batch_analyze      # Mode Multi-game
│   │   └── review-auto        # Review automatique via API LLM (--html)
│   └── lib/
│       ├── riot_api_client.rb
│       ├── extractor.rb
│       ├── game_context.rb
│       ├── item_resolver.rb
│       ├── visualizer.rb              # Minimap HTML visualization
│       ├── progression_tracker.rb     # Progression tracking across games
│       ├── champion_knowledge_generator.rb  # Auto-learn champion knowledge
│       ├── detectors/                 # 12 detectors
│       ├── formatters/
│       │   └── review_formatter.rb
│       ├── enrichers/
│       └── filters/
├── tools/
│   └── frida_scan.py          # Scanner memoire Frida
├── decoder/                   # Parsers ROFL2 + crypto research
├── prompts/
│   ├── review.md              # Prompt template pour Claude (304 lignes)
│   ├── champions/             # Connaissances par champion (sylas_mid.md, gwen_jungle.md)
│   └── roles/                 # Connaissances par role (jungle.md, mid.md, top.md)
├── docs/                      # Specs, briefings, plans
├── output/                    # JSON reviews, HTML visualizations (gitignored)
├── replays/                   # Fichiers .rofl (gitignored)
├── .env                       # Config (gitignored)
├── .env.example               # Template de config
└── CLAUDE.md                  # Contexte IA
```

## Exemple de review multi-game

```
=== ANALYSE CROSS-GAME — 5 DERNIERES RANKED ===

MORTS : 21 en 5 games (4.2/game)
  - 71% en RIVIERE (pas de vision)
  - 62% avec jungler ennemi implique
  - 67% avant 15 min (early game)

VISION : 5/5 games rated POOR

TOP 3 ERREURS RECURRENTES :
1. Morts en riviere sans vision — ward avant de bouger
2. Vision catastrophique — control ward a chaque back
3. Pas de damage quand behind — consequence des morts early
```

## Roadmap

- [x] Riot API integration (timeline, match info, positions par minute)
- [x] Live replay recording via localhost:2999
- [x] 12 detectors (death zones, CS, roam, teamfight, objectives, jungler, vision, items, comeback, damage)
- [x] Frida memory scanning pour positions en temps reel
- [x] Multi-game analysis (erreurs recurrentes)
- [x] Batch replay launcher via LCU API
- [x] Prompt template pour Claude (tous roles)
- [x] 5 LLM providers (Claude, OpenAI, Gemini, Mistral, Ollama)
- [x] Minimap HTML visualization (--html flag)
- [x] Progression tracking across games (bin/progress)
- [x] Auto-learning champion/role knowledge (bin/learn-champion)
- [x] Auto-install script (bin/setup)
- [x] Interactive quick-review (bin/quick-review)
- [ ] Interface web pour les reviews
- [ ] Decodage direct du .rofl (sans replay)

## Securite

- **Cles API** : toutes les cles (Riot, Anthropic, OpenAI, Gemini, Mistral) sont dans `.env` qui est git-ignored. Ne jamais hardcoder de cle dans le code. Les cles API Riot expirent toutes les 24h.
- **SSL** : les connexions aux APIs externes (Riot, LLM providers) utilisent HTTPS avec verification SSL standard. Les deux seules exceptions sont les APIs locales du client LoL (`localhost:2999` pour le Live Client et `127.0.0.1` pour le LCU) qui utilisent des certificats auto-signes — `VERIFY_NONE` est utilise uniquement pour ces connexions localhost.
- **Anti-cheat (Vanguard)** : le mode Frida ne doit etre utilise que sur des replays offline. Ne jamais attacher Frida a un processus de game en ligne — risque de ban permanent.
- **Donnees** : les fichiers `.rofl`, les JSON de sortie, et le cache d'items sont git-ignored. Aucune donnee personnelle n'est commitee.

## Contribuer

Le projet est open source. Fork, PR, issues bienvenues.

## Licence

MIT
