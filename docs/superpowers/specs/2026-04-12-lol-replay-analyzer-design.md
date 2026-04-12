# LoL Replay Analyzer — Design Spec

## Overview

CLI tool that analyzes League of Legends `.rofl` replay files to generate actionable gameplay reviews for mid lane players. The tool decodes encrypted replay packets, extracts structured game events, and produces a compact JSON that Claude (Code or API) transforms into a coaching-style review.

**Target user:** The author (Emerald 2 mid laner), personal use first, open source later.

## Architecture

```
.rofl → [Rust Decoder (Mowokuma)] → positions.json (positions + wards)
                                           ↓
Match ID → [Ruby + Riot API] → timeline.json (events, kills, items)
                                           ↓
                                 [Ruby Extractor] → review.json (50-100KB)
                                           ↓
                                 [Claude Code / API] → Review
```

### 3 Components

| Component | Language | Role | Source |
|---|---|---|---|
| **Decoder** | Rust | Decrypt `.rofl` payload → positions (1/sec) + wards | Fork of [Mowokuma/ROFL](https://github.com/Mowokuma/ROFL) |
| **Riot API Client** | Ruby | Fetch Match Timeline v5 → kills, items, objectives, CS/gold per minute | Custom code using Riot API |
| **Extractor** | Ruby | Merge both sources, detect patterns, compress → compact JSON | Custom code |
| **Reviewer** | Claude Code (manual) → Claude API (later) | Analyze gameplay → text review | Prompt engineering |

### Why this split

- **Decoder in Rust:** Mowokuma's code already works. Uses Unicorn Engine (Rust bindings) to emulate LoL binary decryption functions. Extracts player positions every second + ward data. No reason to rewrite.
- **Riot API:** Complements the decoder with event-level data (kills, items, objectives) that the decoder doesn't extract.
- **Extractor in Ruby:** This is where 80% of dev time goes (detector logic, pattern recognition). Author is a senior Ruby developer — fastest iteration in this language.

## Data Flow

### Step 1 — Decoder (Python)

**Input:** `.rofl` file

**Process:**
1. Parse header + metadata (unencrypted JSON — post-game scoreboard)
2. Decrypt payload chunks via Blowfish (key = match ID)
3. Decompress (zstd/zlib)
4. Decode individual packets by emulating LoL binary functions via Unicorn Engine
5. Requires a **patch file** per LoL version (dumped sections of `League of Legends.exe` + function RVAs)

**Output:** Raw JSON with 20 packet types:

| Packet | Data |
|---|---|
| `CastSpellAns` | Every ability cast (spell, position, target, cooldown, mana cost, slot) |
| `BasicAttackPos` | Every auto-attack (source, target, positions) |
| `WaypointGroup` | Player movements (x,z coordinates ~1/second) |
| `WaypointGroupWithSpeed` | Movements with speed data |
| `UnitApplyDamage` | Every damage instance |
| `HeroDie` | Champion deaths |
| `BuyItem` | Item purchases |
| `RemoveItem` | Item sells |
| `SwapItem` | Item slot changes |
| `UseItem` | Item activations |
| `EnterFog` | Entity entering fog of war |
| `LeaveFog` | Entity leaving fog of war |
| `ReplicationData` | Game state sync (HP, mana, stats) |
| `DoSetCooldown` | Ability cooldown updates |
| `CreateHero` | Champion spawn/init |
| `CreateNeutral` | Neutral monster creation |
| `CreateTurret` | Turret initialization |
| `BarrackSpawnUnit` | Minion spawning |
| `SpawnMinion` | General minion spawn |
| `NPCDieMapView` / `NPCDieMapViewBroadcast` | NPC deaths |

**Constraint:** Patch file must be regenerated every LoL patch (~every 2 weeks). Process:
1. Dump `.text`, `.data`, `.rdata` sections from `League of Legends.exe` (Windows)
2. Find RVAs for decryption functions
3. Package as patch file zip

### Step 2 — Extractor (Ruby)

**Input:** Raw JSON from decoder + summoner name + role

**Process:**
1. **LOAD** — Read raw JSON, identify summoner's champion
2. **FILTER** — Keep only events involving: your champion, enemy mid laner, junglers (for ganks)
3. **DETECT** — Recognize gameplay "moments" from raw packets
4. **ENRICH** — Add context (vision, wave state, gold, objectives)
5. **AGGREGATE** — Compute global patterns
6. **EXPORT** — Output compact JSON

**Output:** Compact JSON (~50-100KB) with 4 sections:

```json
{
  "meta": { "champion", "enemy_mid", "result", "elo", "game_version", "duration" },
  "final_stats": { "kda", "cs", "gold", "damage", "vision", "items" },
  "timeline": [ { "time", "type", "...contextual fields" } ],
  "patterns": { "cs_at_5/10/15", "death_timings", "trades_won/lost", "roams", "wards" }
}
```

**Timeline event types:**

| Type | Detected from | Key fields |
|---|---|---|
| `TRADE` | UnitApplyDamage + CastSpellAns within ~3s between midlaners | hp_before/after, abilities_used, result |
| `DEATH` | HeroDie on your champion | killed_by, position, gold_unspent, ward_coverage |
| `CS_STATE` | Periodic snapshot | your_cs, enemy_cs, cs_diff |
| `ROAM` | WaypointGroup showing champion leaving mid zone | destination, wave_state, result |
| `TEAMFIGHT` | 3+ HeroDie within ~2000 units in ~15s | targets_hit, entry_timing, ult_stolen |
| `RECALL` | EnterFog + no movement ~8s | objective_spawning_soon, gold_unspent |
| `ITEM_BUY` | BuyItem | items, gold_spent |
| `OBJECTIVE` | Dragon/Baron events | present/absent, your_position |

### Step 3 — Reviewer (Claude)

**V1:** Manual via Claude Code
- Prompt template stored in `prompts/review.md`
- User runs `bin/review game.rofl`, gets `output/game_review.json`
- User opens Claude Code: "Read prompts/review.md then analyze output/game_review.json"

**V2:** Automated via Claude API
- `bin/review` calls API directly, outputs review text

**Prompt design principles:**
- Role: Diamond+ mid lane coach
- Adapted to Emerald 2 level (actionable advice, not Challenger theory)
- Errors prioritized by game impact
- Direct, concrete, no generic filler
- French language

## Project Structure

```
lol-replay-analyzer/
├── decoder/                # Fork Python Mowokuma
│   ├── patches/            # Patch files per LoL version
│   └── ...
├── extractor/              # Ruby
│   ├── lib/
│   │   ├── extractor.rb
│   │   ├── game_context.rb
│   │   ├── filters/
│   │   │   └── mid_lane_filter.rb
│   │   ├── detectors/
│   │   │   ├── trade_detector.rb
│   │   │   ├── death_detector.rb
│   │   │   ├── roam_detector.rb
│   │   │   ├── teamfight_detector.rb
│   │   │   └── recall_detector.rb
│   │   ├── enrichers/
│   │   │   └── context_enricher.rb
│   │   └── formatters/
│   │       └── review_formatter.rb
│   ├── bin/
│   │   └── extract
│   └── spec/
├── prompts/
│   └── review.md
├── bin/
│   └── review              # Main orchestrator script
├── replays/                # .rofl files (gitignored)
├── output/                 # Generated JSONs (gitignored)
├── .gitignore
└── README.md
```

## Cross-Platform

- **Windows:** User plays LoL here. Source of `.rofl` files and `League of Legends.exe` for patch file generation.
- **macOS:** User develops here. Decoder runs via Unicorn Engine (emulates x86_64 on any host). Extractor runs natively (Ruby).
- **Transfer:** `.rofl` files moved from Windows to Mac (cloud sync, USB, etc.)

## Scope

### V1 — In scope
- Mid lane only
- All mid champions (tested primarily on Sylas)
- Detectors: DEATH, TRADE, CS_STATE (3 detectors to start)
- Manual review via Claude Code
- Single patch support (current patch only)
- Personal use

### V1 — Explicitly out of scope
- Other roles (top, jungle, ADC, support)
- Web UI
- Automated Claude API integration
- Multi-patch support
- Multi-user support
- Open source release

### V2 — Future
- Additional detectors (ROAM, TEAMFIGHT, RECALL, OBJECTIVE)
- Claude API automation
- Support for all mid champions with champion-specific insights
- Open source release with README, docs, contribution guide
- Potentially: screenshot annotations from replay for visual review

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Mowokuma stops maintaining patch files | Can't decode new patches | Learn the patch file generation process early |
| Vanguard blocks binary dumping | Can't generate patch files | Community may find workarounds; fallback to Riot API |
| `.rofl` format changes fundamentally | Decoder breaks | Unlikely; format has been stable for years |
| 135MB JSON too noisy for good detection | Bad reviews | Ruby extractor filters aggressively; iterate on thresholds |
| Claude hallucinations in reviews | Wrong advice | Structured JSON reduces hallucination; prompt engineering |
| Patch file generation is too complex | Blocks phase 1 | Use Henry Zhu's existing patch files if version matches |

## Implementation Roadmap

| Phase | Duration | Goal |
|---|---|---|
| 1 — PoC Decoder | 1-2 weeks | Fork Mowokuma, generate patch file, decode one .rofl |
| 2 — Extractor + 3 detectors | 2-3 days | DEATH, TRADE, CS_STATE detectors in Ruby |
| 3 — Prompt iteration | 1 evening | Test on known games, refine prompt |
| 4 — Advanced detectors | 2-3 days | ROAM, TEAMFIGHT, RECALL, OBJECTIVE |
| 5 — Polish & automation | 1-2 evenings | bin/review orchestrator, error handling, Claude API |

**Phase 1 is the critical gate.** If the decoder doesn't work, everything else falls. Validate this first before writing any Ruby.
