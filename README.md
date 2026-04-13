# LoL IA Coaching

Outil d'analyse de replays League of Legends avec coaching IA.
Analyse tes games, detecte tes erreurs recurrentes, progresse.

**3 modes d'utilisation :**
- **Mode Rapide** : Riot API seulement, pas besoin de replay (positions par minute)
- **Mode Live** : lance le replay en x8 + capture live data (items, KDA, events)
- **Mode Frida** : lance le replay + capture positions en memoire (positions en temps reel)
- **Mode Multi-game** : analyse croisee de plusieurs games pour trouver tes patterns d'erreurs

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

```bash
# Cloner le repo
git clone https://github.com/SebSusini/lol-ia-coaching.git
cd lol-ia-coaching

# Installer les dependances Ruby
cd extractor && bundle install && cd ..

# Installer les dependances Python (pour les modes avances)
pip install frida frida-tools zstandard

# Configurer
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
```

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
# Analyse automatique de tes 5 dernieres ranked
ruby extractor/bin/batch_analyze --count 5 --summoner "TonPseudo"
```

**Ce que tu obtiens :** patterns d'erreurs recurrentes (zones de mort, vision, timing, damage).

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
├── extractor/              # Pipeline Ruby (12 detectors)
│   ├── bin/
│   │   ├── extract         # Genere la review JSON
│   │   ├── fetch_timeline  # Fetch Riot API
│   │   ├── record_replay   # Mode Live (localhost:2999)
│   │   ├── record_positions # Mode Frida (wrapper Ruby)
│   │   └── batch_analyze   # Mode Multi-game
│   └── lib/
│       ├── riot_api_client.rb
│       ├── extractor.rb
│       ├── game_context.rb
│       ├── item_resolver.rb
│       ├── detectors/      # 12 detectors
│       └── formatters/
├── tools/
│   └── frida_scan.py       # Scanner memoire Frida
├── decoder/                # Parsers ROFL2 + crypto research
├── prompts/
│   └── review.md           # Prompt template pour Claude
├── .env                    # Config (gitignored)
├── .env.example            # Template de config
└── CLAUDE.md               # Contexte IA
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
- [ ] Interface web pour les reviews
- [ ] API Claude automatisee (plus de copier/coller)
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
