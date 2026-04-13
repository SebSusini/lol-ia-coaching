#!/usr/bin/env python3
"""
Tracks all 10 champion positions during a LoL replay using Frida + Riot API matching.

Flow:
1. Scans game memory via Frida to find moving float pairs (positions)
2. Gets game time from Live Client API (localhost:2999)
3. Matches Frida entities to champions using Riot API positions at same timestamp
4. Outputs JSON with identified champion positions every 2 seconds

Usage:
    python3 frida_champion_tracker.py <match_id> <output_file>
    python3 frida_champion_tracker.py EUW1_7816865419 output/champion_positions.json
"""

import frida
import json
import math
import os
import ssl
import sys
import time
import urllib.request

POLL_INTERVAL = 3  # seconds between scans
CALIBRATION_SCANS = 2  # number of scans to find moving entities


def get_pid():
    pid = os.popen("pgrep LeagueofLegends").read().strip()
    return int(pid) if pid else None


def get_game_time():
    """Get current game time from Live Client API."""
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request("https://127.0.0.1:2999/liveclientdata/allgamedata")
        with urllib.request.urlopen(req, context=ctx, timeout=3) as resp:
            data = json.loads(resp.read())
            return data.get("gameData", {}).get("gameTime", 0)
    except:
        return None


def frida_scan(pid):
    """Scan game memory for float position pairs."""
    session = frida.attach(pid)
    script = session.create_script("""
(function(){
    var r = {};
    var ranges = Process.enumerateRanges("rw-");
    for (var i = 0; i < ranges.length; i++) {
        if (ranges[i].size < 4096 || ranges[i].size > 20000000) continue;
        try {
            var sz = Math.min(ranges[i].size, 3000000);
            for (var o = 0; o < sz - 8; o += 4) {
                var x = ranges[i].base.add(o).readFloat();
                var y = ranges[i].base.add(o + 4).readFloat();
                if (x > 500 && x < 14500 && y > 500 && y < 14500 && x === x && y === y) {
                    r[ranges[i].base.add(o).toString()] = {x: x, y: y};
                    if (Object.keys(r).length >= 500) break;
                }
            }
        } catch(e) {}
        if (Object.keys(r).length >= 500) break;
    }
    send(r);
})();
""")
    result = {}

    def on_msg(msg, data):
        nonlocal result
        if msg['type'] == 'send' and isinstance(msg['payload'], dict):
            result = msg['payload']

    script.on('message', on_msg)
    script.load()
    time.sleep(1.5)
    try:
        script.unload()
    except:
        pass
    session.detach()
    return result


def find_moving_entities(pid):
    """Find memory addresses with positions that change between scans."""
    print("Calibrating: finding moving entities...")
    scan1 = frida_scan(pid)
    time.sleep(POLL_INTERVAL + 1)
    scan2 = frida_scan(pid)

    moving = []
    for addr in scan1:
        if addr in scan2:
            dx = abs(scan1[addr]['x'] - scan2[addr]['x'])
            dy = abs(scan1[addr]['y'] - scan2[addr]['y'])
            if dx > 5 or dy > 5:
                moving.append(addr)

    print(f"Found {len(moving)} moving entities")
    return moving, scan2


def load_riot_positions(match_id):
    """Load per-minute positions from cached Riot API timeline."""
    timeline_file = f"output/{match_id}_timeline.json"
    if not os.path.exists(timeline_file):
        print(f"ERROR: {timeline_file} not found. Run fetch_timeline first.")
        return None

    data = json.load(open(timeline_file))
    participants = data["match_info"]["info"]["participants"]
    frames = data["timeline"]["info"]["frames"]

    positions_by_minute = {}
    for frame in frames:
        minute = round(frame["timestamp"] / 60000)
        positions = {}
        for p in participants:
            pid = str(p["participantId"])
            pf = frame.get("participantFrames", {}).get(pid, {})
            if "position" in pf:
                positions[p["championName"]] = {
                    "x": pf["position"]["x"],
                    "y": pf["position"]["y"],
                    "team": "Blue" if p["teamId"] == 100 else "Red",
                    "role": p["teamPosition"],
                    "summoner": p.get("riotIdGameName", p.get("summonerName", ""))
                }
        positions_by_minute[minute] = positions

    return positions_by_minute


def match_to_champions(frida_positions, riot_positions):
    """Match Frida entity addresses to champion names by closest position."""
    pairs = []
    for addr, pos in frida_positions.items():
        for champ, rpos in riot_positions.items():
            dist = math.sqrt((pos["x"] - rpos["x"]) ** 2 + (pos["y"] - rpos["y"]) ** 2)
            pairs.append((dist, addr, champ))

    pairs.sort()
    matches = {}
    used_addrs = set()
    used_champs = set()

    for dist, addr, champ in pairs:
        if addr not in used_addrs and champ not in used_champs and dist < 3000:
            matches[addr] = {
                "champion": champ,
                "distance": round(dist),
                "team": riot_positions[champ]["team"],
                "role": riot_positions[champ]["role"],
                "summoner": riot_positions[champ]["summoner"]
            }
            used_addrs.add(addr)
            used_champs.add(champ)

    return matches


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 frida_champion_tracker.py <match_id> [output_file]")
        print("Example: python3 frida_champion_tracker.py EUW1_7816865419 output/positions.json")
        sys.exit(1)

    match_id = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else f"output/{match_id}_champion_positions.json"

    # Load Riot API positions
    riot_positions = load_riot_positions(match_id)
    if not riot_positions:
        sys.exit(1)

    # Wait for game
    pid = get_pid()
    if not pid:
        print("LeagueofLegends not running! Launch a replay first.")
        sys.exit(1)

    print(f"Attached to PID {pid}")

    # Find moving entities
    moving_addrs, initial_scan = find_moving_entities(pid)
    if len(moving_addrs) < 10:
        print(f"Warning: only {len(moving_addrs)} moving entities found (need 10 for champions)")

    # Get current game time for initial matching
    game_time = get_game_time()
    if game_time:
        current_minute = round(game_time / 60)
        print(f"Game time: {game_time:.0f}s ({current_minute} min)")
    else:
        current_minute = 5  # default
        print("Could not get game time, using minute 5 for matching")

    # Match entities to champions
    frida_moving = {addr: initial_scan[addr] for addr in moving_addrs if addr in initial_scan}
    riot_at_minute = riot_positions.get(current_minute, riot_positions.get(5, {}))

    entity_map = match_to_champions(frida_moving, riot_at_minute)

    print(f"\n=== CHAMPION MAPPING ({len(entity_map)}/10) ===")
    for addr, info in sorted(entity_map.items(), key=lambda x: x[1]["team"]):
        print(f"  {info['champion']:12s} ({info['team']} {info['role']:7s}) @ {addr} (dist: {info['distance']})")

    unmatched = len(moving_addrs) - len(entity_map)
    if unmatched > 0:
        print(f"  + {unmatched} unmatched entities (minions/camps/wards)")

    # Now poll and record
    print(f"\nRecording champion positions to {output_file}...")
    print("Press Ctrl+C to stop\n")

    snapshots = []
    champion_addrs = {addr: info["champion"] for addr, info in entity_map.items()}

    try:
        while True:
            pid = get_pid()
            if not pid:
                print("\nGame ended.")
                break

            game_time = get_game_time()
            try:
                scan = frida_scan(pid)
            except Exception as e:
                print(f"\nFrida error: {e}")
                break

            positions = {}
            for addr, champ in champion_addrs.items():
                if addr in scan:
                    positions[champ] = {
                        "x": round(scan[addr]["x"], 1),
                        "y": round(scan[addr]["y"], 1)
                    }

            snapshot = {
                "game_time": round(game_time, 1) if game_time else None,
                "timestamp": time.time(),
                "champions": positions
            }
            snapshots.append(snapshot)

            # Display
            gt = f"{int(game_time//60)}:{int(game_time%60):02d}" if game_time else "?"
            champs_str = " | ".join(f"{c}({p['x']:.0f},{p['y']:.0f})" for c, p in sorted(positions.items())[:5])
            sys.stdout.write(f"\r[{gt}] {len(snapshots)} snapshots | {len(positions)} champs | {champs_str}")
            sys.stdout.flush()

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\nStopped.")

    # Save
    result = {
        "match_id": match_id,
        "entity_map": entity_map,
        "snapshots_count": len(snapshots),
        "snapshots": snapshots
    }
    with open(output_file, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Saved {len(snapshots)} snapshots to {output_file}")


if __name__ == "__main__":
    main()
