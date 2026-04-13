#!/usr/bin/env python3
"""
Extract player positions from a running LoL replay using Frida.

Usage:
  1. Launch a replay from the LoL client
  2. Run: python3 tools/frida_extract_positions.py --output output/positions.json

Prerequisites:
  pip install frida frida-tools
"""

import argparse
import json
import sys
import time
import os

try:
    import frida
except ImportError:
    print("Install frida: pip install frida frida-tools")
    sys.exit(1)


def on_message(message, data):
    """Handle messages from Frida script."""
    if message['type'] == 'send':
        payload = message['payload']
        print(json.dumps(payload))
        # Append to output
        if hasattr(on_message, 'outfile') and on_message.outfile:
            on_message.outfile.write(json.dumps(payload) + '\n')
            on_message.outfile.flush()
    elif message['type'] == 'error':
        print(f"[ERROR] {message['description']}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description='Extract positions from LoL replay via Frida')
    parser.add_argument('--output', '-o', default='output/frida_positions.jsonl',
                        help='Output file (JSONL format)')
    parser.add_argument('--pid', type=int, help='Process ID (auto-detect if not specified)')
    parser.add_argument('--duration', type=int, default=0,
                        help='Recording duration in seconds (0=until interrupted)')
    args = parser.parse_args()

    # Find LoL process
    if args.pid:
        pid = args.pid
    else:
        print("[*] Looking for League of Legends.exe process...")
        try:
            device = frida.get_local_device()
            processes = device.enumerate_processes()
            lol_procs = [p for p in processes if 'League of Legends' in p.name]
            if not lol_procs:
                print("[!] League of Legends.exe not found. Launch a replay first.")
                sys.exit(1)
            pid = lol_procs[0].pid
            print(f"[*] Found: {lol_procs[0].name} (PID {pid})")
        except Exception as e:
            print(f"[!] Error finding process: {e}")
            sys.exit(1)

    # Load Frida script
    script_path = os.path.join(os.path.dirname(__file__), 'frida_positions_win.js')
    with open(script_path) as f:
        script_source = f.read()

    # Attach
    print(f"[*] Attaching to PID {pid}...")
    try:
        session = frida.attach(pid)
    except Exception as e:
        print(f"[!] Failed to attach: {e}")
        print("    Make sure you run this as Administrator on Windows")
        sys.exit(1)

    # Setup output
    os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
    on_message.outfile = open(args.output, 'a')

    # Load script
    script = session.create_script(script_source)
    script.on('message', on_message)
    script.load()

    print(f"[*] Recording positions to {args.output}")
    print("[*] Press Ctrl+C to stop")

    try:
        if args.duration > 0:
            time.sleep(args.duration)
        else:
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        print("\n[*] Stopping...")
    finally:
        script.unload()
        session.detach()
        on_message.outfile.close()
        print(f"[*] Saved to {args.output}")


if __name__ == '__main__':
    main()
