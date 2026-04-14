#!/usr/bin/env python3
"""
Batch Movement Packet Parser for League of Legends ROFL2 Replays

ARCHITECTURE NOTE:
Movement packets (pkt_id=0x001c) in ROFL2 are encrypted using a proprietary
cipher embedded in the game binary. They are NOT simply XOR-encoded with 0xFA.
The 0xFA byte is just the fill/padding byte in the raw packet data.

Decryption requires emulating the game binary's decryption function using
Unicorn Engine, exactly as Mowokuma's Rust decoder does:
  1. Load game binary sections (text, data, rdata) into Unicorn
  2. Set up packet payload as input argument
  3. Call the decryption function
  4. Read back the decrypted payload from emulator memory
  5. Parse the decrypted payload with PathPacket.parse()

This script:
  - Extracts all movement packets from the replay
  - For individual packets (param != 0, 10 at t=0): associates them with player IDs
  - For batch packets (param = 0, 57k+): attempts emulation-based decryption
  - Outputs player positions as JSON

Requirements:
  - Game binary sections at /tmp/lol_text.bin, /tmp/lol_data.bin, /tmp/lol_rdata.bin
  - Movement decryption function RVA (needs reverse engineering per patch)
  - Unicorn Engine: pip install unicorn

Usage:
  python batch_movement_parser.py [replay.rofl] [--find-decrypt-func] [--output path]
"""

import argparse
import json
import os
import struct
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sm4_decoder import ROFL2Parser, Block

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MOVEMENT_PACKET_ID = 0x001C
FILL_BYTE = 0xFA

# Mac x86_64 binary layout (from Mach-O headers)
TEXT_VMADDR = 0x100000000
TEXT_SIZE = 0x240d000
DATA_CONST_VMADDR = 0x10240d000
DATA_CONST_SIZE = 0x185000
DATA_VMADDR = 0x102592000
DATA_SIZE = 0xfa000

# Binary section files
TEXT_BIN = "/tmp/lol_text.bin"
DATA_BIN = "/tmp/lol_data.bin"
RDATA_BIN = "/tmp/lol_rdata.bin"  # DATA_CONST in Mach-O terms

# Known cipher function offsets in x86_64 binary (from overnight analysis)
# These are file offsets = vmaddr - TEXT_VMADDR
CIPHER_WRAPPER_OFFSET = 0x01f79f69
CIPHER_PRIMITIVE_OFFSET = 0x01f7a080
DECRYPT_BSWAP_OFFSET = 0x01f7c7f0

# Emulator layout
STACK_BASE = 0x7FFFFFFF0000
STACK_SIZE = 0x4000
HEAP_BASE = 0x7FFFFFFF8000
HEAP_SIZE = 0x20000
SC_BASE = 0x7FFFFFFD0000


# ---------------------------------------------------------------------------
# PathPacket parser (from Mowokuma's Rust - for decrypted payloads)
# ---------------------------------------------------------------------------

def sign_extend_16(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


@dataclass
class PathPacket:
    timestamp: float
    entity_id: int
    speed: float
    waypoints: list

    @staticmethod
    def parse(timestamp: float, payload: bytes,
              entity_id_override: int = 0) -> Optional["PathPacket"]:
        """Parse a DECRYPTED movement packet payload into waypoints.

        The payload must be DECRYPTED first. Raw/encrypted payloads will
        produce garbage coordinates.
        """
        if len(payload) < 10:
            return None
        pos = 0
        parsing_type = struct.unpack_from("<H", payload, pos)[0]; pos += 2
        entity_id = struct.unpack_from("<I", payload, pos)[0]; pos += 4
        speed = struct.unpack_from("<f", payload, pos)[0]; pos += 4

        if entity_id_override and not (0x40000000 <= entity_id <= 0x40000200):
            entity_id = entity_id_override

        if parsing_type & 1:
            pos += 1

        temp_arr = payload[pos:]
        unk = (parsing_type & 0xFF) >> 1
        if unk == 0:
            return None
        if unk > 1:
            pos += ((unk - 2) >> 2) + 1

        encoded_coords = []
        v10 = v13 = 0
        x_coord = y_coord = 0

        while v10 < unk:
            v14 = v15 = 2
            if v10 != 0:
                v16 = v13; v17 = v13 & 7
                if v13 < 0: v16 = v13 + 7; v17 = (v13 & 7) - 8
                byte_idx = v16 >> 3
                if byte_idx >= len(temp_arr): break
                v18 = temp_arr[byte_idx]
                v19 = v13 + 1
                v14 = 2 - (1 if ((v18 >> (v17 & 7)) & 1) else 0)
                v21 = v19 & 7
                if v19 < 0: v21 = ((v13 + 8) & 7) - 8
                byte_idx2 = v19 >> 3
                bit2 = (temp_arr[byte_idx2] >> (v21 & 7)) & 1 if byte_idx2 < len(temp_arr) else 0
                v15 = 2 - (1 if bit2 else 0)
                v13 += 2

            if pos >= len(payload): break
            if v14 == 1:
                if pos >= len(payload): break
                x_coord = (x_coord + payload[pos]) & 0xFFFF; pos += 1
            else:
                if pos + 2 > len(payload): break
                x_coord = struct.unpack_from("<H", payload, pos)[0]; pos += 2
            if v15 == 1:
                if pos >= len(payload): break
                y_coord = (y_coord + payload[pos]) & 0xFFFF; pos += 1
            else:
                if pos + 2 > len(payload): break
                y_coord = struct.unpack_from("<H", payload, pos)[0]; pos += 2

            encoded_coords.append(x_coord)
            encoded_coords.append(y_coord)
            v10 += 1

        waypoints = []
        for i in range(0, len(encoded_coords), 2):
            x = sign_extend_16(encoded_coords[i]) * 2.0 + 7358.0
            y = sign_extend_16(encoded_coords[i + 1]) * 2.0 + 7412.0
            waypoints.append((x, y))

        return PathPacket(timestamp=timestamp, entity_id=entity_id,
                          speed=speed, waypoints=waypoints)


def is_valid_entity_id(eid): return 0x40000000 <= eid <= 0x40000200
def is_valid_speed(s): return 0.0 <= s <= 1500.0 and s == s
def is_valid_position(x, y): return -500 <= x <= 15500 and -500 <= y <= 15500


# ---------------------------------------------------------------------------
# Emulation-based decryption
# ---------------------------------------------------------------------------

class MovementDecryptor:
    """Emulates the game binary's movement packet decryption function.

    This requires:
    1. The game binary sections (text.bin, data.bin, rdata.bin)
    2. The RVA of the decryption function entry and exit points
    3. The output struct layout (where decrypted payload ptr/size are stored)

    These values are patch-specific and must be reverse-engineered per version.
    """

    def __init__(self, decrypt_rva: int = 0, decrypt_end_rva: int = 0,
                 payload_offset: int = 0, payload_size_offset: int = 0):
        self.decrypt_rva = decrypt_rva
        self.decrypt_end_rva = decrypt_end_rva
        self.payload_offset = payload_offset
        self.payload_size_offset = payload_size_offset
        self.uc = None
        self.text_data = None
        self.data_data = None
        self.rdata_data = None

    def load_binaries(self):
        """Load game binary sections from disk."""
        for path in [TEXT_BIN, DATA_BIN, RDATA_BIN]:
            if not os.path.exists(path):
                raise FileNotFoundError(f"Binary section not found: {path}")

        self.text_data = open(TEXT_BIN, "rb").read()
        self.data_data = open(DATA_BIN, "rb").read()
        self.rdata_data = open(RDATA_BIN, "rb").read()
        print(f"  Loaded: TEXT={len(self.text_data):,} "
              f"DATA={len(self.data_data):,} RDATA={len(self.rdata_data):,}")

    def setup(self):
        """Set up Unicorn emulator with binary sections mapped."""
        try:
            from unicorn import Uc, UC_ARCH_X86, UC_MODE_64, UC_PROT_ALL, UC_PROT_READ, UC_PROT_WRITE
            from unicorn.x86_const import UC_X86_REG_RSP
        except ImportError:
            raise ImportError("unicorn-engine not installed. Run: pip install unicorn")

        self.uc = Uc(UC_ARCH_X86, UC_MODE_64)

        # Stack
        self.uc.mem_map(STACK_BASE, STACK_SIZE, UC_PROT_READ | UC_PROT_WRITE)
        self.uc.reg_write(UC_X86_REG_RSP, STACK_BASE + STACK_SIZE - 0x100)

        # Heap
        self.uc.mem_map(HEAP_BASE, HEAP_SIZE, UC_PROT_READ | UC_PROT_WRITE)

        # Shellcode area
        self.uc.mem_map(SC_BASE, 0x2000, UC_PROT_ALL)

        # Map TEXT segment
        text_size = ((len(self.text_data) + 0xFFF) & ~0xFFF)
        self.uc.mem_map(TEXT_VMADDR, text_size, UC_PROT_ALL)
        self.uc.mem_write(TEXT_VMADDR, self.text_data)

        # Map DATA_CONST (rdata)
        rdata_start = DATA_CONST_VMADDR & ~0xFFF
        rdata_size = ((len(self.rdata_data) + (DATA_CONST_VMADDR - rdata_start) + 0xFFF) & ~0xFFF)
        if rdata_start >= TEXT_VMADDR + text_size:
            self.uc.mem_map(rdata_start, rdata_size, UC_PROT_ALL)
        self.uc.mem_write(DATA_CONST_VMADDR, self.rdata_data)

        # Map DATA
        data_start = DATA_VMADDR & ~0xFFF
        data_size = ((len(self.data_data) + (DATA_VMADDR - data_start) + 0xFFF) & ~0xFFF)
        if data_start >= rdata_start + rdata_size:
            self.uc.mem_map(data_start, data_size, UC_PROT_ALL)
        self.uc.mem_write(DATA_VMADDR, self.data_data)

        # Patch memcpy/memmove stubs with simple implementations
        memcpy_sc = bytes([
            0x48, 0x89, 0xf8,  # mov rax, rdi
            0x48, 0x85, 0xd2,  # test rdx, rdx
            0x74, 0x0a,        # jz done
            0x8a, 0x0e,        # mov cl, [rsi]
            0x88, 0x0f,        # mov [rdi], cl
            0x48, 0xff, 0xc6,  # inc rsi
            0x48, 0xff, 0xc7,  # inc rdi
            0x48, 0xff, 0xca,  # dec rdx
            0x75, 0xf4,        # jnz loop
            0xc3               # ret
        ])
        self.uc.mem_write(SC_BASE, memcpy_sc)

        self.heap_cursor = 0

    def decrypt_payload(self, raw_payload: bytes) -> Optional[bytes]:
        """Decrypt a movement packet payload using emulation.

        Returns the decrypted payload, or None if decryption fails.
        Requires decrypt_rva and decrypt_end_rva to be set.
        """
        if not self.decrypt_rva or not self.uc:
            return None

        from unicorn.x86_const import (UC_X86_REG_RCX, UC_X86_REG_RDX,
                                       UC_X86_REG_R8, UC_X86_REG_RSP)

        # Reset heap
        self.heap_cursor = 0
        self.uc.reg_write(UC_X86_REG_RSP, STACK_BASE + STACK_SIZE - 0x100)
        self.uc.mem_write(HEAP_BASE, b'\x00' * HEAP_SIZE)

        # Allocate packet output struct (0x90 bytes)
        packet_addr = self._alloc(0x90)

        # Allocate and store payload
        payload_addr = self._alloc_write(raw_payload)
        payload_ptr_addr = self._alloc_write(struct.pack('<Q', payload_addr))
        payload_end = payload_addr + len(raw_payload)

        # Set up function arguments (Windows x64 calling convention)
        # RCX = packet output struct, RDX = payload ptr ptr, R8 = payload end
        self.uc.reg_write(UC_X86_REG_RCX, packet_addr)
        self.uc.reg_write(UC_X86_REG_RDX, payload_ptr_addr)
        self.uc.reg_write(UC_X86_REG_R8, payload_end)

        # Push return address
        ret_addr = SC_BASE + 0x1000
        self.uc.mem_write(ret_addr, b'\xc3')
        rsp = self.uc.reg_read(UC_X86_REG_RSP)
        self.uc.mem_write(rsp, struct.pack('<Q', ret_addr))

        # Run emulation
        try:
            self.uc.emu_start(
                TEXT_VMADDR + self.decrypt_rva,
                TEXT_VMADDR + self.decrypt_end_rva,
                timeout=10_000_000
            )
        except Exception as e:
            return None

        # Read back decrypted payload
        try:
            size = struct.unpack_from(
                "<I",
                bytes(self.uc.mem_read(
                    packet_addr + self.payload_size_offset, 4
                ))
            )[0]
            ptr = struct.unpack_from(
                "<Q",
                bytes(self.uc.mem_read(
                    packet_addr + self.payload_offset, 8
                ))
            )[0]
            decrypted = bytes(self.uc.mem_read(ptr, size))
            return decrypted
        except Exception:
            return None

    def _alloc(self, size):
        ptr = HEAP_BASE + self.heap_cursor
        self.heap_cursor += (size + 15) & ~15
        return ptr

    def _alloc_write(self, data):
        ptr = self._alloc(len(data))
        self.uc.mem_write(ptr, data)
        return ptr


# ---------------------------------------------------------------------------
# Binary analysis: find decrypt function
# ---------------------------------------------------------------------------

def find_decrypt_function():
    """Search the x86_64 binary for the movement packet decryption function.

    Strategy:
    1. Find xrefs to the cipher wrapper (0x01f79f69)
    2. Among those callers, find functions that:
       a. Take a payload buffer as input
       b. Write to an output struct
       c. Are called from the packet dispatch handler

    The function should match the pattern seen in Mowokuma's emulator:
    - Input: RCX=packet_struct, RDX=payload_ptr_ptr, R8=payload_end
    - Output: packet_struct has payload ptr and size at known offsets
    """
    if not os.path.exists(TEXT_BIN):
        print("ERROR: Binary sections not found. Cannot search for decrypt function.")
        return

    print("\n--- Searching for movement decrypt function ---")
    print(f"  Binary: {TEXT_BIN}")

    with open(TEXT_BIN, "rb") as f:
        text = f.read()

    print(f"  Text section size: {len(text):,} bytes")

    # Find CALL instructions that target the cipher wrapper
    cipher_addr = TEXT_VMADDR + CIPHER_WRAPPER_OFFSET
    print(f"  Cipher wrapper at: 0x{cipher_addr:x}")

    # In x86_64, CALL rel32 is E8 xx xx xx xx
    # The target = current_addr + 5 + rel32
    # So rel32 = target - (current_addr + 5)
    callers = []
    for off in range(len(text) - 5):
        if text[off] == 0xE8:
            rel32 = struct.unpack_from("<i", text, off + 1)[0]
            target = TEXT_VMADDR + off + 5 + rel32
            if target == cipher_addr:
                caller_addr = TEXT_VMADDR + off
                callers.append(caller_addr)

    print(f"  Found {len(callers)} direct CALL references to cipher wrapper")
    for addr in callers[:20]:
        offset = addr - TEXT_VMADDR
        print(f"    0x{addr:x} (file offset 0x{offset:x})")

    # Also search for calls to cipher primitive and decrypt_bswap
    for name, func_off in [("cipher_primitive", CIPHER_PRIMITIVE_OFFSET),
                           ("decrypt_bswap", DECRYPT_BSWAP_OFFSET)]:
        func_addr = TEXT_VMADDR + func_off
        count = 0
        for off in range(len(text) - 5):
            if text[off] == 0xE8:
                rel32 = struct.unpack_from("<i", text, off + 1)[0]
                target = TEXT_VMADDR + off + 5 + rel32
                if target == func_addr:
                    count += 1
        print(f"  {name} at 0x{func_addr:x}: {count} callers")

    # Look for the packet net ID 0x001c in the binary
    # It might be used in a dispatch table
    netid_bytes = struct.pack("<H", MOVEMENT_PACKET_ID)
    netid_locs = []
    for off in range(len(text) - 2):
        if text[off:off+2] == netid_bytes:
            # Check if preceded by a CMP instruction
            if off >= 3 and (text[off-3] in [0x3D, 0x66, 0x81]):
                netid_locs.append(TEXT_VMADDR + off)
    print(f"  Movement packet ID (0x001c) referenced at {len(netid_locs)} locations")
    for addr in netid_locs[:10]:
        print(f"    0x{addr:x}")

    print("\n  To complete the setup, you need to:")
    print("  1. Disassemble the callers of cipher_wrapper")
    print("  2. Find the one that matches the decrypt pattern:")
    print("     - Takes payload buffer + size as input")
    print("     - Calls cipher_wrapper to decrypt")
    print("     - Stores result in output struct")
    print("  3. Set decrypt_rva, decrypt_end_rva in this script")
    print("  4. Set payload_offset, payload_size_offset for output struct")


# ---------------------------------------------------------------------------
# Extract and output positions
# ---------------------------------------------------------------------------

def extract_positions(parser: ROFL2Parser, decryptor: Optional[MovementDecryptor],
                      output_path: str, timeline_path: Optional[str] = None):
    """Extract all movement data from the replay.

    If a decryptor is provided, uses emulation to decrypt batch packets.
    Otherwise, reports packet statistics without coordinate extraction.
    """
    print(f"\n{'='*60}")
    print("Extracting movement data")
    print(f"{'='*60}")

    t_start = time.time()

    # Phase 1: Extract all movement packets
    individual_packets = []  # (timestamp, param, raw_payload)
    batch_packets = []       # (timestamp, raw_payload)
    batch_count = 0

    for block in parser.iter_blocks():
        if block.packet_id != MOVEMENT_PACKET_ID:
            continue
        if block.param != 0:
            individual_packets.append((block.timestamp, block.param, block.payload))
        else:
            batch_packets.append((block.timestamp, block.payload))
            batch_count += 1

    print(f"\n  Individual packets (spawn): {len(individual_packets)}")
    print(f"  Batch packets (movement): {len(batch_packets)}")

    # Phase 2: Map individual packets to player entity IDs
    player_entities = {}  # param -> entity_info
    for ts, param, payload in individual_packets:
        decoded = bytes(b ^ FILL_BYTE for b in payload)
        if len(decoded) >= 6:
            eid = struct.unpack_from("<I", decoded, 2)[0]
            player_entities[param] = {
                "param": f"0x{param:08x}",
                "batch_entity_id": f"0x{eid:08x}",
                "timestamp": ts,
            }
            print(f"    param=0x{param:08x} -> batch_eid=0x{eid:08x} at t={ts:.3f}")

    # Phase 3: Attempt decryption of batch packets
    positions = []
    decrypted_count = 0
    failed_count = 0

    if decryptor and decryptor.decrypt_rva:
        print(f"\n  Decrypting {len(batch_packets)} batch packets via emulation...")

        for i, (ts, raw_payload) in enumerate(batch_packets):
            if i % 5000 == 0 and i > 0:
                print(f"    {i}/{len(batch_packets)} processed "
                      f"({decrypted_count} OK, {failed_count} failed)")

            decrypted = decryptor.decrypt_payload(raw_payload)
            if decrypted is None:
                failed_count += 1
                continue

            pkt = PathPacket.parse(ts, decrypted)
            if pkt is None:
                failed_count += 1
                continue

            if pkt.waypoints and is_valid_position(*pkt.waypoints[0]):
                decrypted_count += 1
                # First waypoint = current position
                x, y = pkt.waypoints[0]
                positions.append({
                    "timestamp": round(ts, 3),
                    "entity_id": f"0x{pkt.entity_id:08x}",
                    "x": round(x, 1),
                    "y": round(y, 1),
                    "speed": round(pkt.speed, 1),
                })

        print(f"\n  Decrypted: {decrypted_count}/{len(batch_packets)} "
              f"({failed_count} failed)")
    else:
        print(f"\n  WARNING: No decryptor available.")
        print(f"  Movement packets are ENCRYPTED and require the game binary's")
        print(f"  decryption function to be emulated via Unicorn Engine.")
        print(f"")
        print(f"  To enable decryption:")
        print(f"  1. Run with --find-decrypt-func to search the binary")
        print(f"  2. Reverse-engineer the movement decrypt function")
        print(f"  3. Set the function RVA in this script or pass via --decrypt-rva")
        print(f"")
        print(f"  Alternatively, use Mowokuma's Rust decoder with a patch file")
        print(f"  for game version {parser.header.game_version}")

    # Phase 4: Output results
    elapsed = time.time() - t_start

    output = {
        "format": "lol-movement-v2",
        "game_version": parser.header.game_version,
        "game_id": parser.game_id,
        "game_length_ms": parser.metadata.game_length_ms,
        "players": parser.metadata.players,
        "player_entities": player_entities,
        "extraction": {
            "individual_packets": len(individual_packets),
            "batch_packets": len(batch_packets),
            "decrypted_count": decrypted_count,
            "failed_count": failed_count,
            "elapsed_seconds": round(elapsed, 1),
            "decryption_method": "emulation" if decryptor and decryptor.decrypt_rva else "none",
        },
        "positions": positions,
    }

    # Add packet size distribution for analysis
    size_dist = defaultdict(int)
    for ts, payload in batch_packets:
        size_dist[len(payload)] += 1
    output["packet_sizes"] = dict(sorted(size_dist.items())[:20])

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    fsize = os.path.getsize(output_path)
    print(f"\n  Output: {output_path} ({fsize:,} bytes)")
    print(f"  Positions: {len(positions)}")
    print(f"  Time: {elapsed:.1f}s")

    # Phase 5: Validate against timeline if available
    if timeline_path and positions:
        validate_against_timeline(positions, player_entities, timeline_path,
                                  parser.metadata.players)

    return output


def validate_against_timeline(positions, player_entities, timeline_path, players):
    """Compare extracted positions with Riot API per-minute positions."""
    print(f"\n{'='*60}")
    print("Validation against Riot API timeline")
    print(f"{'='*60}")

    try:
        with open(timeline_path) as f:
            timeline = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"  ERROR: Cannot load timeline: {e}")
        return

    frames = timeline.get("timeline", {}).get("info", {}).get("frames", [])
    participants = timeline["match_info"]["info"]["participants"]

    if not frames:
        print("  No timeline frames found")
        return

    # Build per-minute position map
    api_positions = {}
    for frame in frames:
        ts_s = frame["timestamp"] / 1000.0
        minute = int(ts_s / 60)
        api_positions[minute] = {}
        for pid_str, pdata in frame.get("participantFrames", {}).items():
            pos = pdata.get("position", {})
            api_positions[minute][int(pid_str)] = (pos.get("x", 0), pos.get("y", 0))

    # For each entity, find nearest replay position to each minute mark
    pos_by_entity = defaultdict(list)
    for p in positions:
        pos_by_entity[p["entity_id"]].append(p)

    print(f"\n  Entities with positions: {len(pos_by_entity)}")
    for eid, eid_positions in sorted(pos_by_entity.items()):
        print(f"\n  Entity {eid} ({len(eid_positions)} positions):")
        for target_minute in [1, 5, 10, 15, 20]:
            target_ts = target_minute * 60.0
            nearest = min(eid_positions, key=lambda p: abs(p["timestamp"] - target_ts))
            if abs(nearest["timestamp"] - target_ts) > 30:
                continue

            rx, ry = nearest["x"], nearest["y"]
            api_min = api_positions.get(target_minute, {})

            best_match = None
            best_dist = float("inf")
            for pid, (ax, ay) in api_min.items():
                dist = ((rx - ax)**2 + (ry - ay)**2)**0.5
                if dist < best_dist:
                    best_dist = dist
                    champ = participants[pid-1].get("championName", "?")
                    best_match = f"P{pid} {champ} ({ax},{ay})"

            status = "OK" if best_dist < 500 else "FAR" if best_dist < 2000 else "BAD"
            print(f"    min {target_minute:2d}: replay=({rx:.0f},{ry:.0f}) "
                  f"nearest={best_match} dist={best_dist:.0f} [{status}]")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Batch movement packet parser for ROFL2 replays")
    ap.add_argument("rofl_file", nargs="?",
                    default="/Users/sommesi/projects/lol-replay-analyzer/replays/EUW1-7816865419.rofl")
    ap.add_argument("--output", "-o",
                    default="/Users/sommesi/projects/lol-replay-analyzer/output/movement_positions.json")
    ap.add_argument("--timeline",
                    default="/Users/sommesi/projects/lol-replay-analyzer/output/EUW1_7816865419_timeline.json")
    ap.add_argument("--find-decrypt-func", action="store_true",
                    help="Search the binary for the movement decryption function")
    ap.add_argument("--decrypt-rva", type=lambda x: int(x, 0), default=0,
                    help="RVA of the decryption function (hex)")
    ap.add_argument("--decrypt-end-rva", type=lambda x: int(x, 0), default=0,
                    help="RVA of the decryption function end (hex)")
    args = ap.parse_args()

    print("Batch Movement Parser for ROFL2 Replays")
    print(f"Replay: {args.rofl_file}")

    # Parse replay
    rp = ROFL2Parser(args.rofl_file)
    print(f"Game version: {rp.header.game_version}")
    print(f"Game length: {rp.metadata.game_length_ms / 1000:.0f}s")
    print(f"Players: {len(rp.metadata.players)}")
    for p in rp.metadata.players:
        print(f"  {p['team']:4s} {p['position']:7s} {p['skin']}")

    # Search for decrypt function if requested
    if args.find_decrypt_func:
        find_decrypt_function()
        return

    # Set up decryptor if RVA provided
    decryptor = None
    if args.decrypt_rva:
        try:
            decryptor = MovementDecryptor(
                decrypt_rva=args.decrypt_rva,
                decrypt_end_rva=args.decrypt_end_rva,
            )
            decryptor.load_binaries()
            decryptor.setup()
            print(f"\nDecryptor initialized: RVA=0x{args.decrypt_rva:x}")
        except Exception as e:
            print(f"\nWARNING: Failed to initialize decryptor: {e}")
            decryptor = None

    # Extract positions
    timeline = args.timeline if os.path.exists(args.timeline) else None
    extract_positions(rp, decryptor, args.output, timeline)


if __name__ == "__main__":
    main()
