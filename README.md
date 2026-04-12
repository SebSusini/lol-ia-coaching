# LoL Replay Analyzer

Outil d'analyse de replays League of Legends qui genere des reviews de gameplay detaillees pour les mid laners.

Tu joues ta game, tu lances le replay en x8, et tu obtiens une review complete avec :
- Analyse de chaque mort (dive sous tour ? gank ? overextend ?)
- Progression des items comparee a ton adversaire
- Courbe de gold/CS/XP minute par minute
- Detection des rotations, objectifs, teamfights
- Conseils adaptes a ton elo

## Comment ca marche

```
1. Tu joues ta game normalement
2. Tu lances le replay dans le client LoL (en x8)
3. Le recorder capture les donnees en temps reel via localhost:2999
4. Le script fetch la timeline Riot API pour les events detailles
5. L'extractor Ruby merge tout et genere un JSON compact
6. Tu donnes le JSON a Claude Code -> review de coaching
```

### Architecture

```
Replay LoL (client)
    |
    v
[Live Client API - localhost:2999]  --> record_replay --> live_data.json
    |                                                        |
    |                                                        v
[Riot API Match Timeline]  --> fetch_timeline --> timeline.json
    |                                                        |
    |                                                        v
[.rofl metadata]                              [Ruby Extractor] --> review.json
                                                                     |
                                                                     v
                                                              [Claude Code] --> Review
```

### Donnees capturees

| Source | Donnees | Frequence |
|---|---|---|
| **Live Client API** | Items, levels, KDA, isDead, summoner spells pour les 10 joueurs | Toutes les 2s |
| **Live Client API** | Events (kills, turrets, dragons, barons) avec noms | Temps reel |
| **Riot API Timeline** | Kill events avec positions x,y, achats d'items, CS/gold/XP par minute | Post-game |
| **.rofl metadata** | Scoreboard complet (damage, vision, CS) | Post-game |

### Ce que la review detecte

- **Position de chaque mort** : dive sous tour, centre de lane, jungle, riviere
- **Camping du jungler** : combien de fois le jungler adverse est implique dans tes morts
- **Gap d'items** : comparaison item par item a chaque mort
- **Avantage de level/gold** : courbe minute par minute vs ton adversaire
- **Kills et assists** : quand tu trouves des angles pour comeback
- **Objectifs** : dragons, barons, tourelles

## Setup

### Prerequis

- macOS ou Linux
- Ruby 3.x
- Un compte Riot Games avec une [API key](https://developer.riotgames.com/)
- Le client League of Legends (pour jouer les replays)

### Installation

```bash
git clone https://github.com/TON_USER/lol-replay-analyzer.git
cd lol-replay-analyzer

# Installer les dependances Ruby
cd extractor && bundle install && cd ..

# Configurer ton compte
cp .env.example .env
# Edite .env avec ta cle API Riot et ton pseudo
```

### Configuration (.env)

```bash
RIOT_API_KEY=RGAPI-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
RIOT_SUMMONER_NAME=TonPseudo
RIOT_TAG_LINE=EUW
RIOT_REGION=europe
RIOT_PLATFORM=euw1
```

## Utilisation

### 1. Trouver ta game

```bash
# Voir tes 10 dernieres games
ruby -r dotenv/load -e '
require_relative "extractor/lib/riot_api_client"
client = RiotApiClient.new
account = client.account_by_riot_id(ENV["RIOT_SUMMONER_NAME"], ENV["RIOT_TAG_LINE"])
matches = client.match_history(account["puuid"], count: 10)
matches.each do |id|
  info = client.match_info(id)
  you = info["info"]["participants"].find { |p| p["puuid"] == account["puuid"] }
  puts "#{id} | #{you["championName"]} #{you["teamPosition"]} | #{you["kills"]}/#{you["deaths"]}/#{you["assists"]} | #{you["win"] ? "WIN" : "LOSS"}"
  sleep 1.2
end'
```

### 2. Enregistrer le replay (live data)

```bash
# Lance le replay dans le client LoL, puis :
ruby extractor/bin/record_replay output/ma_game_live.json

# Mets le replay en x8 pour aller vite (~3-4 min)
# Le script s'arrete automatiquement a la fin de la game
```

### 3. Fetch la timeline Riot API

```bash
ruby extractor/bin/fetch_timeline --match-id EUW1_XXXXXXXXXX --output output/ma_game_timeline.json
```

### 4. Generer la review

```bash
# Ouvre Claude Code dans le projet
claude

# Puis demande :
# "Lis prompts/review.md puis analyse output/ma_game_review.json
#  et output/ma_game_live.json"
```

## Exemple de review

Voici un extrait d'une review generee pour une game Sylas vs Mel (Emerald 2) :

```
Mort #3 (10:47) - Mel te DIVE sous ta tour
- Toi : Hextech Alternator + composants (pas d'item complet)
- Mel : Luden's Echo complet + Boots
- Verdict : INEVITABLE - le gap d'items est trop gros, elle sait qu'elle a le burst

Mort #4 (12:03) - Picked en jungle ennemie 1v4
- Position : jungle top cote ennemi (x:3090, y:12817)
- Verdict : EVITABLE - ne traverse pas la jungle ennemie seul quand tu es 1/3
```

## Structure du projet

```
lol-replay-analyzer/
├── extractor/
│   ├── bin/
│   │   ├── extract          # CLI extraction
│   │   ├── fetch_timeline   # Fetch Riot API
│   │   └── record_replay    # Enregistre les donnees live du replay
│   └── lib/
│       ├── riot_api_client.rb
│       ├── extractor.rb
│       ├── game_context.rb
│       ├── filters/         # Filtrage mid lane
│       ├── detectors/       # Detection de patterns (death, cs, roam)
│       ├── enrichers/       # Enrichissement contextuel
│       └── formatters/      # Formatage du JSON de review
├── prompts/
│   └── review.md            # Prompt template pour Claude
├── bin/
│   └── review               # Orchestrateur principal
├── replays/                  # Fichiers .rofl (gitignored)
├── output/                   # JSON generes (gitignored)
├── .env.example
└── CLAUDE.md                 # Contexte IA
```

## Roadmap

- [x] Riot API integration (timeline, match info)
- [x] Live replay recording via localhost:2999
- [x] Death detection avec position (dive/gank/overextend)
- [x] CS/Gold/XP tracking par minute
- [x] Item progression tracking
- [x] Event timeline (kills, turrets, objectives)
- [x] Prompt template pour Claude Code
- [ ] Detection de roam automatique (avec positions live)
- [ ] Detection de teamfight
- [ ] Recall timing analysis
- [ ] Support multi-champion (pas que Sylas)
- [ ] API Claude automatisee (plus besoin de copier/coller)
- [ ] Parsing .rofl profond (positions, abilities, degats)
- [ ] Interface web

## Tech Stack

- **Ruby** — Extraction, detection de patterns, formatting
- **Riot API v5** — Match timeline, events, participant data
- **LoL Live Client Data API** — Donnees en temps reel pendant le replay
- **Claude** — Generation de la review de coaching

## Limitations actuelles

- Il faut jouer le replay dans le client LoL (en x8 ca prend ~3-4 min)
- Pas de positions exactes des joueurs sur la map (prevu en V2)
- Pas de detection de trades de lane (necessite les packets de degats)
- La cle API Riot dev expire toutes les 24h

## Contribuer

Le projet est en early stage. Si tu veux contribuer :
1. Fork le repo
2. Cree une branche (`git checkout -b feature/mon-truc`)
3. Commit (`git commit -m "Add mon truc"`)
4. Push (`git push origin feature/mon-truc`)
5. Ouvre une PR

## Licence

MIT
