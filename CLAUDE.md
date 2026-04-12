# LoL Replay Analyzer

## Project Overview
CLI tool that analyzes League of Legends replays to generate gameplay reviews for mid lane players.
Target: Emerald 2 mid laner, personal use, open source later.

## Architecture
```
.rofl → [Rust Decoder (Mowokuma fork)] → positions.json (positions + wards)
                                              ↓
Match ID → [Ruby script + Riot API] → timeline.json (events, kills, items)
                                              ↓
                                    [Ruby Extractor] → review.json (compact, ~50-100KB)
                                              ↓
                                    [Claude Code / API] → Gameplay review
```

## Tech Stack
- **Decoder**: Rust (fork of github.com/Mowokuma/ROFL) — extracts player positions (1/sec) + wards from .rofl
- **Riot API client**: Ruby — fetches Match Timeline v5 (kills, items, objectives, CS, gold)
- **Extractor**: Ruby — merges both data sources, detects gameplay patterns, outputs compact JSON
- **Reviewer**: Claude Code (manual V1) → Claude API (automated V2)

## Key Files
- `decoder/` — Mowokuma ROFL parser (Rust, submodule or fork)
- `extractor/lib/` — Ruby extraction logic
- `extractor/lib/detectors/` — Pattern detectors (death, roam, teamfight, recall, etc.)
- `prompts/review.md` — Claude prompt template for reviews
- `bin/review` — Main orchestrator script
- `docs/superpowers/specs/` — Design spec

## Data Sources
1. **Mowokuma decoder** → positions (x,y every second for all 10 players) + wards (type, position, duration, owner)
2. **Riot API Match Timeline v5** → kill events, item purchases, objectives, ward events, participant frames (gold/CS/XP per minute)
3. **.rofl metadata** (unencrypted header) → post-game scoreboard (KDA, damage, items, vision score)

## Commands
```bash
# Decode a replay (positions + wards)
cd decoder && cargo run -- file -r ../replays/game.rofl -o ../output/game_positions.json

# Fetch Riot API timeline (needs RIOT_API_KEY in .env)
ruby extractor/bin/fetch_timeline --match-id EUW1_1234567890

# Extract review JSON
ruby extractor/bin/extract --positions output/game_positions.json --timeline output/game_timeline.json --summoner "TonPseudo"

# Full pipeline
bin/review replays/game.rofl
```

## Conventions
- Ruby: standard Ruby style, no framework, plain classes
- Tests: RSpec in extractor/spec/
- Detectors are independent classes in extractor/lib/detectors/
- Each detector takes structured game data, returns an array of timeline events
- JSON output follows the schema in the design spec

## Scope V1
- Mid lane only (all champions, tested on Sylas)
- Detectors: DEATH, CS_STATE, ROAM, RECALL, TEAMFIGHT, OBJECTIVE, ITEM_BUY
- No trades detection (requires packet-level data, planned for V2)
- Manual review via Claude Code
- Single LoL patch support

## Environment
- .env contains RIOT_API_KEY and summoner info
- Decoder needs a .patch file per LoL version in decoder/patch/
- Decoder binary built with `cargo build --release` in decoder/
