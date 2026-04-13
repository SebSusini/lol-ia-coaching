#!/usr/bin/env python3
"""Scan LoL replay memory for champion positions via Frida — Windows version.

Usage (PowerShell admin):
  python tools/frida_scan_win.py output/positions_frida.json

Same approach as frida_scan.py (Mac) but adapted for Windows:
  - Process name: "League of Legends.exe" instead of "LeagueofLegends"
  - Uses tasklist for PID detection
  - Compatible with Vanguard (needs admin)
"""
import frida, json, os, subprocess, sys, time


def get_pid():
    """Find League of Legends.exe PID on Windows."""
    try:
        output = subprocess.check_output(
            ['tasklist', '/FI', 'IMAGENAME eq League of Legends.exe', '/FO', 'CSV', '/NH'],
            text=True, stderr=subprocess.DEVNULL
        )
        for line in output.strip().split('\n'):
            if 'League of Legends' in line:
                parts = line.strip('"').split('","')
                if len(parts) >= 2:
                    return int(parts[1].strip('"'))
    except Exception:
        pass

    # Fallback: try frida device enumeration
    try:
        device = frida.get_local_device()
        for proc in device.enumerate_processes():
            if 'League of Legends' in proc.name:
                return proc.pid
    except Exception:
        pass

    return None


def scan_once(pid):
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
    try: script.unload()
    except: pass
    session.detach()
    return result


def find_moving(scan1, scan2):
    """Find addresses where position changed between two scans."""
    moving = []
    for addr in scan1:
        if addr in scan2:
            dx = abs(scan1[addr]['x'] - scan2[addr]['x'])
            dy = abs(scan1[addr]['y'] - scan2[addr]['y'])
            if dx > 5 or dy > 5:
                moving.append(addr)
    return moving


def main():
    output = sys.argv[1] if len(sys.argv) > 1 else "output/positions_frida.json"
    interval = 2

    pid = get_pid()
    if not pid:
        print("League of Legends.exe not running!")
        print("Launch a replay from the LoL client first.")
        sys.exit(1)

    print(f"Attached to PID {pid}")
    print("Calibrating (finding moving entities)...")

    scan1 = scan_once(pid)
    time.sleep(interval + 1)
    scan2 = scan_once(pid)

    moving = find_moving(scan1, scan2)
    print(f"Found {len(moving)} moving positions")

    snapshots = []
    print(f"Recording to {output}... (Ctrl+C to stop)")

    try:
        while True:
            pid = get_pid()
            if not pid:
                print("\nGame ended.")
                break

            try:
                scan = scan_once(pid)
            except Exception as e:
                print(f"\nFrida error: {e}")
                break

            positions = []
            for addr in moving:
                if addr in scan:
                    positions.append({
                        "x": round(scan[addr]['x'], 1),
                        "y": round(scan[addr]['y'], 1),
                        "addr": addr
                    })

            snapshots.append({
                "time": time.time(),
                "count": len(positions),
                "positions": positions
            })

            sys.stdout.write(f"\r[{len(snapshots)}] {len(positions)} positions")
            sys.stdout.flush()

            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped.")

    os.makedirs(os.path.dirname(output) or '.', exist_ok=True)
    result = {
        "snapshots_count": len(snapshots),
        "moving_addresses": len(moving),
        "snapshots": snapshots
    }
    with open(output, "w") as f:
        json.dump(result, f, indent=2)
    print(f"Saved {len(snapshots)} snapshots to {output}")


if __name__ == "__main__":
    main()
