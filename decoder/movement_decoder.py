#!/usr/bin/env python3
"""
Movement packet decoder for ROFL2 League of Legends replays.

Extracts movement packets (pkt_id=0x001c) from ROFL2 replays and
attempts decryption via Unicorn Engine x86_64 emulation using the Mac binary.

Key findings from analysis:
- Movement payloads use 0xFA as fill byte (not 0x00)
- Entity IDs come from Block.param for individual packets (0x400000ae-0x400000b7)
- Batch packets (param=0) contain per-entity data but without explicit entity ID in payload
- The binary's cipher function at 0x01f7a080 is a block cipher primitive (8 x u32 state)
- The wrapper at 0x01f79f69 feeds payload data INTO the cipher state (hash-like operation)
- Actual decryption requires finding the higher-level function that XORs cipher output with data

Usage:
    python movement_decoder.py [replay.rofl] [--step N] [--output path]

Uses the existing ROFL2Parser from sm4_decoder.py.
"""

import argparse
import json
import os
import struct
import sys
import time
from collections import Counter
from dataclasses import dataclass
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sm4_decoder import ROFL2Parser, Block

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MOVEMENT_PACKET_ID = 0x001C
FILL_BYTE = 0xFA

# Mac binary layout
TEXT_VMADDR  = 0x100000000
DATA_VMADDR  = 0x102592000
RDATA_VMADDR = 0x10240d000
TEXT_BIN  = "/tmp/lol_text.bin"
DATA_BIN  = "/tmp/lol_data.bin"
RDATA_BIN = "/tmp/lol_rdata.bin"

# Emulator layout
STACK_BASE = 0x7FFFFFFF0000
STACK_SIZE = 0x4000
HEAP_BASE  = 0x7FFFFFFF8000
HEAP_SIZE  = 0x20000
SC_BASE    = 0x7FFFFFFD0000
PAGE_SIZE  = 0x1000

# Known offsets in __TEXT
CIPHER_WRAPPER_OFFSET   = 0x01f79f69  # feeds data into cipher state
CIPHER_PRIMITIVE_OFFSET = 0x01f7a080  # block cipher (8xu32 state transform)
DECRYPT_BSWAP_OFFSET    = 0x01f7c7f0  # decrypt + bswap wrapper
MEMCPY_STUB_OFFSET      = 0x020f1482  # GOT stub -> _memcpy
MEMMOVE_STUB_OFFSET     = 0x020f0da4  # GOT stub -> _memmove


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
def is_valid_speed(s): return 50.0 <= s <= 1500.0 and s == s
def is_valid_position(x, y): return -2000 <= x <= 17000 and -2000 <= y <= 17000


# ---------------------------------------------------------------------------
# Unicorn Engine emulation
# ---------------------------------------------------------------------------

def _build_memcpy_shellcode():
    return bytes([0x48,0x89,0xf8, 0x48,0x85,0xd2, 0x74,0x0a,
                  0x8a,0x0e, 0x88,0x0f, 0x48,0xff,0xc6,
                  0x48,0xff,0xc7, 0x48,0xff,0xca, 0x75,0xf4, 0xc3])


class MacEmulator:
    """Unicorn Engine x86_64 emulator for the Mac LoL binary."""

    def __init__(self):
        self.uc = None
        self.text_data = self.data_data = self.rdata_data = None
        self.heap_cursor = 0

    def load_binaries(self):
        self.text_data = open(TEXT_BIN, "rb").read()
        self.data_data = open(DATA_BIN, "rb").read()
        self.rdata_data = open(RDATA_BIN, "rb").read()

    def setup(self):
        from unicorn import Uc, UC_ARCH_X86, UC_MODE_64, UC_PROT_ALL, UC_PROT_READ, UC_PROT_WRITE
        from unicorn.x86_const import UC_X86_REG_RSP

        self.uc = Uc(UC_ARCH_X86, UC_MODE_64)
        self.uc.mem_map(STACK_BASE, STACK_SIZE, UC_PROT_READ | UC_PROT_WRITE)
        self.uc.reg_write(UC_X86_REG_RSP, STACK_BASE + STACK_SIZE - 0x100)
        self.uc.mem_map(HEAP_BASE, HEAP_SIZE, UC_PROT_READ | UC_PROT_WRITE)
        self.uc.mem_map(SC_BASE, 0x2000, UC_PROT_ALL)

        text_size = ((len(self.text_data) + 0xFFF) & ~0xFFF)
        self.uc.mem_map(TEXT_VMADDR, text_size, UC_PROT_ALL)
        self.uc.mem_write(TEXT_VMADDR, self.text_data)

        for seg_addr, seg_data in [(RDATA_VMADDR, self.rdata_data), (DATA_VMADDR, self.data_data)]:
            seg_start = seg_addr & ~0xFFF
            if seg_start >= TEXT_VMADDR + text_size:
                seg_size = ((len(seg_data) + (seg_addr - seg_start) + 0xFFF) & ~0xFFF)
                self.uc.mem_map(seg_start, seg_size, UC_PROT_ALL)
            self.uc.mem_write(seg_addr, seg_data)

        # Patch GOT stubs
        mc = _build_memcpy_shellcode()
        self.uc.mem_write(SC_BASE, mc)
        self.uc.mem_write(TEXT_VMADDR + MEMCPY_STUB_OFFSET,
                          b'\x48\xb8' + struct.pack('<Q', SC_BASE) + b'\xff\xe0')
        self.uc.mem_write(SC_BASE + 0x100, mc)
        self.uc.mem_write(TEXT_VMADDR + MEMMOVE_STUB_OFFSET,
                          b'\x48\xb8' + struct.pack('<Q', SC_BASE + 0x100) + b'\xff\xe0')

        self.heap_cursor = 0

    def reset(self):
        from unicorn.x86_const import UC_X86_REG_RSP
        self.heap_cursor = 0
        self.uc.reg_write(UC_X86_REG_RSP, STACK_BASE + STACK_SIZE - 0x100)
        self.uc.mem_write(HEAP_BASE, b'\x00' * HEAP_SIZE)

    def alloc(self, size):
        ptr = HEAP_BASE + self.heap_cursor
        self.heap_cursor += (size + 15) & ~15
        return ptr

    def alloc_write(self, data):
        ptr = self.alloc(len(data))
        self.uc.mem_write(ptr, data)
        return ptr

    def read(self, addr, size):
        return bytes(self.uc.mem_read(addr, size))

    def run_cipher_wrapper(self, state_bytes: bytes, payload: bytes) -> tuple:
        """
        Run the cipher wrapper. Returns (modified_state, payload_unchanged).
        The wrapper feeds payload into the cipher state (hash-like).
        """
        from unicorn import UC_HOOK_MEM_UNMAPPED, UC_HOOK_MEM_INVALID
        from unicorn.x86_const import (UC_X86_REG_RDI, UC_X86_REG_RSI,
                                       UC_X86_REG_RDX, UC_X86_REG_RSP)

        state_addr = self.alloc(0x80)
        self.uc.mem_write(state_addr, state_bytes.ljust(0x80, b'\x00'))

        payload_addr = self.alloc_write(payload)

        self.uc.reg_write(UC_X86_REG_RDI, state_addr)
        self.uc.reg_write(UC_X86_REG_RSI, payload_addr)
        self.uc.reg_write(UC_X86_REG_RDX, len(payload))

        ret_addr = SC_BASE + 0x1000
        self.uc.mem_write(ret_addr, b'\xc3')
        rsp = self.uc.reg_read(UC_X86_REG_RSP)
        self.uc.mem_write(rsp, struct.pack('<Q', ret_addr))

        err = [None]
        def hook_err(uc, access, address, size, value, ud):
            err[0] = f"0x{address:x}"; uc.emu_stop(); return False

        h = self.uc.hook_add(UC_HOOK_MEM_UNMAPPED | UC_HOOK_MEM_INVALID, hook_err)
        try:
            self.uc.emu_start(TEXT_VMADDR + CIPHER_WRAPPER_OFFSET, ret_addr,
                              timeout=10_000_000)
        except Exception as e:
            if "UC_ERR_INSN_INVALID" not in str(e):
                err[0] = str(e)
        self.uc.hook_del(h)

        state_out = self.read(state_addr, 0x80)
        payload_out = self.read(payload_addr, len(payload))
        return state_out, payload_out, err[0]


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def step1_extract(parser: ROFL2Parser, count: int = 20) -> list[Block]:
    print(f"\n{'='*60}")
    print(f"STEP 1: Extracting first {count} movement packets")
    print(f"{'='*60}")

    blocks = []
    total = 0
    for block in parser.iter_blocks():
        if block.packet_id == MOVEMENT_PACKET_ID:
            total += 1
            if len(blocks) < count:
                blocks.append(block)

    print(f"\nTotal movement packets in replay: {total}")
    print(f"Showing first {len(blocks)}:\n")
    for i, b in enumerate(blocks):
        nf = sum(1 for x in b.payload if x != FILL_BYTE)
        print(f"  [{i:3d}] t={b.timestamp:8.3f}s param=0x{b.param:08x} "
              f"len={len(b.payload):5d} non-FA={nf:4d} "
              f"{b.payload[:24].hex()}")
    return blocks


def step2_try_raw_parse(blocks: list[Block]) -> bool:
    print(f"\n{'='*60}")
    print("STEP 2: Trying to parse raw payloads (no decryption)")
    print(f"{'='*60}")

    valid = 0
    for i, b in enumerate(blocks):
        pkt = PathPacket.parse(b.timestamp, b.payload, b.param)
        if pkt is None:
            print(f"  [{i}] FAIL (parse error)")
            continue
        eid_ok = is_valid_entity_id(pkt.entity_id)
        spd_ok = is_valid_speed(pkt.speed)
        pos_ok = all(is_valid_position(*w) for w in pkt.waypoints) if pkt.waypoints else False
        ok = eid_ok and spd_ok and pos_ok
        if ok: valid += 1
        print(f"  [{i}] {'OK' if ok else '--'} eid=0x{pkt.entity_id:08x}({eid_ok}) "
              f"speed={pkt.speed:.1f}({spd_ok}) wps={len(pkt.waypoints)} pos({pos_ok})")

    # Also try XOR 0xFA
    print(f"\n  Trying with XOR 0xFA pre-processing...")
    valid_xor = 0
    for i, b in enumerate(blocks[:5]):
        xored = bytes(x ^ FILL_BYTE for x in b.payload)
        pkt = PathPacket.parse(b.timestamp, xored, b.param)
        if pkt is None: continue
        eid_ok = is_valid_entity_id(pkt.entity_id)
        spd_ok = is_valid_speed(pkt.speed)
        pos_ok = all(is_valid_position(*w) for w in pkt.waypoints) if pkt.waypoints else False
        ok = eid_ok and spd_ok and pos_ok
        if ok: valid_xor += 1
        print(f"  [{i}] XOR {'OK' if ok else '--'} eid=0x{pkt.entity_id:08x} "
              f"speed={pkt.speed:.1f} wps={len(pkt.waypoints)}")

    rate = max(valid, valid_xor) / len(blocks) if blocks else 0
    print(f"\nResult: {valid}/{len(blocks)} raw, {valid_xor}/5 XOR ({rate:.0%})")
    if rate > 0.5:
        print("  --> Parsing works!")
        return True
    print("  --> Parsing FAILED. Packets need decryption.")
    return False


def step3_emulation(blocks: list[Block]):
    """Emulation-based decryption exploration."""
    print(f"\n{'='*60}")
    print("STEP 3: Unicorn Engine emulation")
    print(f"{'='*60}")

    for path in [TEXT_BIN, DATA_BIN, RDATA_BIN]:
        if not os.path.exists(path):
            print(f"  ERROR: {path} not found"); return None

    emu = MacEmulator()
    emu.load_binaries()
    print(f"  Loaded: TEXT={len(emu.text_data):,} DATA={len(emu.data_data):,} "
          f"RDATA={len(emu.rdata_data):,}")
    emu.setup()

    test = blocks[0]
    print(f"\n  Test: t={test.timestamp:.3f} param=0x{test.param:08x} len={len(test.payload)}")

    # --- Run the cipher wrapper to understand its behavior ---
    print("\n  Running cipher wrapper (hash-mode: feeds data into state)...")
    test_keys = [
        ("zeros", bytes(32)),
        ("ones",  bytes([1,0,0,0]*8)),
        ("0xFF",  bytes([0xFF]*32)),
    ]

    for name, key in test_keys:
        emu.reset()
        state_out, payload_out, err = emu.run_cipher_wrapper(key, bytes(test.payload))
        if err:
            print(f"  [{name}] Error: {err}")
            continue
        changed = sum(1 for a, b in zip(test.payload, payload_out) if a != b)
        state_changed = sum(1 for a, b in zip(key.ljust(32, b'\x00'), state_out[:32]) if a != b)
        print(f"  [{name}] state: {state_changed}/32 bytes changed, "
              f"payload: {changed}/{len(test.payload)} bytes changed")
        print(f"    State out: {state_out[:32].hex()}")

    # --- Show analysis summary ---
    print(f"\n  === Analysis Summary ===")
    print(f"  The cipher wrapper at 0x01f79f69 is a STATE UPDATE function:")
    print(f"  - It feeds the payload INTO the cipher state (like a hash)")
    print(f"  - The payload is NOT modified (0 bytes changed)")
    print(f"  - The state IS modified (new cipher state)")
    print(f"")
    print(f"  The actual decryption function has NOT been identified yet.")
    print(f"  The payload uses 0xFA as fill byte (85-93% of bytes).")
    print(f"  Entity IDs: 0x400000ae-0x400000b7 (10 players)")
    print(f"")
    print(f"  Known binary functions:")
    print(f"    0x{CIPHER_PRIMITIVE_OFFSET:08x}: Block cipher primitive (8xu32 state)")
    print(f"    0x{CIPHER_WRAPPER_OFFSET:08x}: Cipher state update wrapper")
    print(f"    0x{DECRYPT_BSWAP_OFFSET:08x}: Decrypt + byte-swap wrapper")
    print(f"    0x{MEMCPY_STUB_OFFSET:08x}: memcpy GOT stub")
    print(f"    0x{MEMMOVE_STUB_OFFSET:08x}: memmove GOT stub")
    print(f"")
    print(f"  Next steps to identify the decrypt function:")
    print(f"    1. Find functions that call cipher_wrapper AND also XOR the result")
    print(f"    2. Search for callers via indirect call tables (vtables)")
    print(f"    3. Trace the packet dispatch in the game engine")

    return None


def step4_extract_raw(parser: ROFL2Parser, output_path: str):
    """Extract raw movement packet data for analysis."""
    print(f"\n{'='*60}")
    print("STEP 4: Extracting raw movement data")
    print(f"{'='*60}")

    packets = []
    total = 0
    t_start = time.time()

    entities = {}  # param -> list of timestamps

    for block in parser.iter_blocks():
        if block.packet_id != MOVEMENT_PACKET_ID:
            continue
        total += 1

        # Track entity_id from param
        if block.param != 0:
            if block.param not in entities:
                entities[block.param] = []
            entities[block.param].append(block.timestamp)

        # Extract non-fill bytes
        non_fill = [(i, block.payload[i]) for i in range(len(block.payload))
                    if block.payload[i] != FILL_BYTE]

        packets.append({
            "timestamp": round(block.timestamp, 3),
            "param": block.param,
            "payload_len": len(block.payload),
            "non_fill_count": len(non_fill),
            "header": block.payload[:6].hex(),
            "footer": block.payload[-3:].hex() if len(block.payload) >= 3 else "",
        })

        if total % 10000 == 0:
            print(f"  {total} packets processed...")

    elapsed = time.time() - t_start
    print(f"\n  Processed {total} packets in {elapsed:.1f}s")
    print(f"  Entities with param != 0: {len(entities)}")
    for eid in sorted(entities):
        ts = entities[eid]
        print(f"    0x{eid:08x}: {len(ts)} packets, t=[{min(ts):.3f}, {max(ts):.3f}]")

    # Timing analysis
    print(f"\n  Packet timing statistics:")
    timestamps = sorted(set(p["timestamp"] for p in packets))
    if len(timestamps) > 1:
        deltas = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps)-1)]
        avg_delta = sum(deltas) / len(deltas)
        print(f"    Unique timestamps: {len(timestamps)}")
        print(f"    Avg interval: {avg_delta:.3f}s")
        print(f"    Min interval: {min(deltas):.3f}s")
        print(f"    Max interval: {max(deltas):.3f}s")

    # Size distribution
    sizes = Counter(p["payload_len"] for p in packets)
    print(f"\n  Payload size distribution:")
    for sz, cnt in sorted(sizes.items(), key=lambda x: -x[1])[:10]:
        print(f"    {sz:6d} bytes: {cnt:6d} packets ({100*cnt/total:.1f}%)")

    # Save
    output = {
        "format": "lol-movement-raw-v1",
        "total_packets": total,
        "entities": {f"0x{eid:08x}": len(ts) for eid, ts in entities.items()},
        "packets": packets,
    }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, separators=(",", ":"))

    fsize = os.path.getsize(output_path)
    print(f"\n  Saved to {output_path} ({fsize:,} bytes)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Decode movement packets from ROFL2 replays")
    ap.add_argument("rofl_file", nargs="?",
                    default="/Users/sommesi/projects/lol-replay-analyzer/replays/EUW1-7816865419.rofl")
    ap.add_argument("--step", type=int, choices=[1, 2, 3, 4], default=0)
    ap.add_argument("--output", "-o",
                    default="/Users/sommesi/projects/lol-replay-analyzer/output/movement_data.json")
    ap.add_argument("--count", "-n", type=int, default=20)
    args = ap.parse_args()

    print("Movement Decoder for ROFL2 Replays")
    print(f"Replay: {args.rofl_file}")

    rp = ROFL2Parser(args.rofl_file)
    print(f"Game version: {rp.header.game_version}")
    print(f"Game length: {rp.metadata.game_length_ms / 1000:.0f}s")

    blocks = []
    if args.step in (0, 1, 2, 3):
        blocks = step1_extract(rp, args.count)

    if args.step in (0, 2):
        step2_try_raw_parse(blocks)

    if args.step == 3 or args.step == 0:
        step3_emulation(blocks)

    if args.step == 4:
        step4_extract_raw(rp, args.output)


if __name__ == "__main__":
    main()
