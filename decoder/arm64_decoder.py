#!/usr/bin/env python3
"""
ARM64 Movement Packet Decoder for ROFL2 League of Legends replays.

Decodes movement packets (packet_id=0x001c) from ROFL2 replays using ARM64
binary emulation via Unicorn Engine, targeting Apple Silicon native code.

Architecture:
  1. Extract ARM64 slice from the universal LoL binary
  2. Dump __TEXT, __DATA_CONST, __DATA segments
  3. Find the SM4-CTR decrypt function (key expand at 0x101ccfc8c)
  4. Set up Unicorn ARM64 emulator with mapped segments
  5. Call the decrypt function on each movement packet payload
  6. Parse decrypted payloads using the PathPacket format

Binary analysis findings (ARM64 slice, vmaddr base 0x100000000):
  SM4 S-box table:     0x101fc0340  (256 bytes in __const)
  SM4 CK table:        0x101fc02c0  (128 bytes in __const)
  SM4 FK constants:    0x101fc02a0  (used as IV init, 32 bytes)
  SM4 key expansion:   0x101ccfc8c  (called from 0x101c92c4c)
  SM4 init (loads IV): 0x101ccfc70  (loads q0,q1 from 0x2a0, stores to [x0])
  Crypto dispatch:     0x101c92800 - 0x101c93000 (multiple cipher modes)
  SM4-CTR encrypt fn:  0x101ccfdd8  (used as function pointer in x5/x6)
  SM4-ECB encrypt fn:  0x101cd0754  (alternate cipher, also used as fn ptr)
  Streaming decrypt:   0x101ca3e20, 0x101ca402c, 0x101ca5354, 0x101ca8600

Usage:
    python arm64_decoder.py <replay.rofl> [options]

    --dump-sections     Dump ARM64 binary sections to /tmp/
    --find-decrypt      Search for decrypt function addresses
    --emulate           Attempt Unicorn ARM64 emulation
    --analyze           Run structural analysis on movement packets
    --output PATH       Export decoded positions to JSON
    --max-packets N     Limit packets processed
"""

import argparse
import json
import math
import os
import struct
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sm4_decoder import ROFL2Parser, Block


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MOVEMENT_PACKET_ID = 0x001C
FILL_BYTE = 0xFA

LOL_BINARY = (
    "/Applications/League of Legends.app/Contents/LoL/Game/"
    "LeagueofLegends.app/Contents/MacOS/LeagueofLegends"
)
ARM64_BINARY = "/tmp/lol_arm64"

# ARM64 binary layout (from lief analysis)
TEXT_VMADDR      = 0x100000000
TEXT_FILEOFF      = 0x000000000
TEXT_FILESIZE     = 0x002170000

DATA_CONST_VMADDR = 0x102170000
DATA_CONST_FILEOFF = 0x002170000
DATA_CONST_FILESIZE = 0x000188000

DATA_VMADDR       = 0x1022f8000
DATA_FILEOFF       = 0x0022f8000
DATA_FILESIZE      = 0x000020000

# Key ARM64 function addresses (vmaddr)
SM4_KEY_EXPAND   = 0x101ccfc8c
SM4_INIT_IV      = 0x101ccfc70
SM4_SBOX_TABLE   = 0x101fc0340
SM4_CK_TABLE     = 0x101fc02c0
SM4_CTR_ENCRYPT  = 0x101ccfdd8
SM4_ECB_ENCRYPT  = 0x101cd0754

# Crypto dispatch functions
CRYPTO_ENCRYPT_STREAM_A = 0x101ca3e20  # with SM4-CTR
CRYPTO_ENCRYPT_STREAM_B = 0x101ca402c  # with SM4-ECB
CRYPTO_ENCRYPT_STREAM_C = 0x101ca5354
CRYPTO_ENCRYPT_STREAM_D = 0x101ca8600

# Emulator memory layout
STACK_BASE  = 0x7FFFFF000000
STACK_SIZE  = 0x10000
HEAP_BASE   = 0x7FFFFF100000
HEAP_SIZE   = 0x100000

# Coordinate transform (from Mowokuma Rust source)
COORD_X_OFFSET = 7358.0
COORD_Y_OFFSET = 7412.0


# ---------------------------------------------------------------------------
# ARM64 Binary Extraction
# ---------------------------------------------------------------------------

def extract_arm64_binary() -> str:
    """Extract ARM64 slice from the universal LoL binary."""
    if os.path.exists(ARM64_BINARY):
        return ARM64_BINARY

    if not os.path.exists(LOL_BINARY):
        print(f"ERROR: LoL binary not found at {LOL_BINARY}")
        sys.exit(1)

    print(f"Extracting ARM64 slice from universal binary...")
    subprocess.run(
        ["lipo", LOL_BINARY, "-thin", "arm64", "-output", ARM64_BINARY],
        check=True,
    )
    print(f"  Saved to {ARM64_BINARY} ({os.path.getsize(ARM64_BINARY):,} bytes)")
    return ARM64_BINARY


def dump_sections():
    """Dump ARM64 binary sections to individual files for emulation."""
    binary_path = extract_arm64_binary()

    with open(binary_path, "rb") as f:
        data = f.read()

    sections = {
        "text":       (TEXT_FILEOFF, TEXT_FILESIZE),
        "data_const": (DATA_CONST_FILEOFF, DATA_CONST_FILESIZE),
        "data":       (DATA_FILEOFF, DATA_FILESIZE),
    }

    print(f"\nDumping ARM64 sections:")
    for name, (offset, size) in sections.items():
        out_path = f"/tmp/lol_arm64_{name}.bin"
        section_data = data[offset:offset + size]
        with open(out_path, "wb") as f:
            f.write(section_data)
        print(f"  {name}: offset=0x{offset:x} size=0x{size:x} -> {out_path}")

    return sections


# ---------------------------------------------------------------------------
# PathPacket Parser (for decrypted payloads)
# ---------------------------------------------------------------------------

def sign_extend_16(value: int) -> int:
    """Sign-extend a 16-bit value."""
    return value - 0x10000 if value & 0x8000 else value


@dataclass
class PathPacket:
    """Parsed movement packet after decryption."""
    timestamp: float
    entity_id: int
    speed: float
    waypoints: list  # list of (x, y) tuples

    @staticmethod
    def parse(
        timestamp: float,
        payload: bytes,
        entity_id_override: int = 0,
    ) -> Optional["PathPacket"]:
        """Parse a PathPacket from decrypted payload bytes."""
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
                if v13 < 0:
                    v16 = v13 + 7; v17 = (v13 & 7) - 8
                byte_idx = v16 >> 3
                if byte_idx >= len(temp_arr):
                    break
                v18 = temp_arr[byte_idx]
                v19 = v13 + 1
                v14 = 2 - (1 if ((v18 >> (v17 & 7)) & 1) else 0)
                v21 = v19 & 7
                if v19 < 0:
                    v21 = ((v13 + 8) & 7) - 8
                byte_idx2 = v19 >> 3
                bit2 = (
                    (temp_arr[byte_idx2] >> (v21 & 7)) & 1
                    if byte_idx2 < len(temp_arr)
                    else 0
                )
                v15 = 2 - (1 if bit2 else 0)
                v13 += 2

            if pos >= len(payload):
                break
            if v14 == 1:
                if pos >= len(payload):
                    break
                x_coord = (x_coord + payload[pos]) & 0xFFFF; pos += 1
            else:
                if pos + 2 > len(payload):
                    break
                x_coord = struct.unpack_from("<H", payload, pos)[0]; pos += 2
            if v15 == 1:
                if pos >= len(payload):
                    break
                y_coord = (y_coord + payload[pos]) & 0xFFFF; pos += 1
            else:
                if pos + 2 > len(payload):
                    break
                y_coord = struct.unpack_from("<H", payload, pos)[0]; pos += 2

            encoded_coords.append(x_coord)
            encoded_coords.append(y_coord)
            v10 += 1

        waypoints = []
        for i in range(0, len(encoded_coords), 2):
            x = sign_extend_16(encoded_coords[i]) * 2.0 + COORD_X_OFFSET
            y = sign_extend_16(encoded_coords[i + 1]) * 2.0 + COORD_Y_OFFSET
            waypoints.append((x, y))

        return PathPacket(
            timestamp=timestamp,
            entity_id=entity_id,
            speed=speed,
            waypoints=waypoints,
        )


def is_valid_position(x, y):
    return -2000 <= x <= 17000 and -2000 <= y <= 17000


def is_valid_speed(s):
    return 50.0 <= s <= 1500.0 and s == s  # also checks NaN


def is_valid_entity_id(eid):
    return 0x40000000 <= eid <= 0x40000200


# ---------------------------------------------------------------------------
# Structural Analysis (no decryption needed)
# ---------------------------------------------------------------------------

def analyze_packet_structure(parser: ROFL2Parser, max_packets: int = 0):
    """Analyze movement packet structure without decryption.

    Extracts structural metadata:
    - Record boundaries (using 0xFE markers in batch packets)
    - Entity mapping (Block.param for individual packets)
    - Timing patterns
    - Non-fill byte distribution
    """
    print(f"\n{'='*60}")
    print("STRUCTURAL ANALYSIS OF MOVEMENT PACKETS")
    print(f"{'='*60}")

    individual_pkts = []
    batch_pkts = []
    total = 0

    for block in parser.iter_blocks():
        if block.packet_id != MOVEMENT_PACKET_ID:
            continue
        total += 1
        if max_packets and total > max_packets:
            break

        if block.param != 0:
            individual_pkts.append(block)
        else:
            batch_pkts.append(block)

    print(f"\n  Total movement packets: {total}")
    print(f"  Individual (param!=0): {len(individual_pkts)}")
    print(f"  Batch (param=0): {len(batch_pkts)}")

    # Entity mapping from individual packets
    entities = {}
    for p in individual_pkts:
        if p.param not in entities:
            entities[p.param] = {"count": 0, "first_t": p.timestamp, "last_t": p.timestamp}
        entities[p.param]["count"] += 1
        entities[p.param]["last_t"] = p.timestamp

    print(f"\n  Entities from individual packets: {len(entities)}")
    for eid in sorted(entities):
        e = entities[eid]
        print(f"    0x{eid:08x}: {e['count']} packets, "
              f"t=[{e['first_t']:.1f}, {e['last_t']:.1f}]")

    # Batch packet record structure
    if batch_pkts:
        print(f"\n  Batch packet structure:")
        for i, bp in enumerate(batch_pkts[:5]):
            fe_positions = [j for j in range(len(bp.payload)) if bp.payload[j] == 0xFE]
            non_fa = sum(1 for b in bp.payload if b != FILL_BYTE)
            print(f"    [{i}] t={bp.timestamp:.3f} len={len(bp.payload)} "
                  f"records~{len(fe_positions)} non_fa={non_fa} "
                  f"({100*non_fa/len(bp.payload):.1f}%)")

    # Size distribution
    all_pkts = individual_pkts + batch_pkts
    sizes = Counter(len(p.payload) for p in all_pkts)
    print(f"\n  Payload size distribution:")
    for sz, cnt in sorted(sizes.items(), key=lambda x: -x[1])[:10]:
        print(f"    {sz:6d} bytes: {cnt:6d} packets")

    # Timing analysis
    timestamps = sorted(set(p.timestamp for p in all_pkts))
    if len(timestamps) > 1:
        deltas = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps)-1)]
        print(f"\n  Timing:")
        print(f"    Unique timestamps: {len(timestamps)}")
        print(f"    Time range: {timestamps[0]:.1f}s - {timestamps[-1]:.1f}s")
        print(f"    Avg interval: {sum(deltas)/len(deltas):.3f}s")

    # Spawn packet analysis (individual packets at t~0)
    spawn_pkts = [p for p in individual_pkts if p.timestamp < 1.0]
    if spawn_pkts:
        print(f"\n  Spawn packets (t<1.0): {len(spawn_pkts)}")
        for sp in spawn_pkts:
            non_fa = [(j, sp.payload[j]) for j in range(len(sp.payload))
                      if sp.payload[j] != FILL_BYTE]
            nf_bytes = bytes(v for _, v in non_fa)
            print(f"    param=0x{sp.param:08x} len={len(sp.payload)} "
                  f"non_fa={len(non_fa)} data={nf_bytes.hex()}")

    return {"individual": individual_pkts, "batch": batch_pkts, "entities": entities}


# ---------------------------------------------------------------------------
# ARM64 Unicorn Emulator
# ---------------------------------------------------------------------------

class ARM64Emulator:
    """Unicorn Engine ARM64 emulator for the LoL Mac binary.

    Emulates the game's packet decrypt function on Apple Silicon ARM64 code.
    """

    def __init__(self):
        self.uc = None
        self.binary_data = None
        self.heap_cursor = 0

    def load_binary(self) -> bool:
        """Load the ARM64 binary."""
        path = extract_arm64_binary()
        with open(path, "rb") as f:
            self.binary_data = f.read()
        print(f"  Loaded ARM64 binary: {len(self.binary_data):,} bytes")
        return True

    def setup(self) -> bool:
        """Set up the Unicorn ARM64 emulator with mapped memory regions."""
        try:
            from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
            from unicorn.unicorn_const import UC_PROT_ALL, UC_PROT_READ, UC_PROT_WRITE
            from unicorn.arm64_const import UC_ARM64_REG_SP
        except ImportError:
            print("ERROR: unicorn not installed. Run: pip install unicorn")
            return False

        self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)

        # Map stack
        self.uc.mem_map(STACK_BASE, STACK_SIZE, UC_PROT_READ | UC_PROT_WRITE)
        self.uc.reg_write(UC_ARM64_REG_SP, STACK_BASE + STACK_SIZE - 0x200)

        # Map heap
        self.uc.mem_map(HEAP_BASE, HEAP_SIZE, UC_PROT_READ | UC_PROT_WRITE)
        self.heap_cursor = 0

        # Map __TEXT segment
        text_size = (TEXT_FILESIZE + 0xFFF) & ~0xFFF
        self.uc.mem_map(TEXT_VMADDR, text_size, UC_PROT_ALL)
        self.uc.mem_write(TEXT_VMADDR, self.binary_data[TEXT_FILEOFF:TEXT_FILEOFF + TEXT_FILESIZE])
        print(f"  Mapped __TEXT: 0x{TEXT_VMADDR:x} size=0x{text_size:x}")

        # Map __DATA_CONST segment
        dc_size = (DATA_CONST_FILESIZE + 0xFFF) & ~0xFFF
        dc_start = DATA_CONST_VMADDR & ~0xFFF
        if dc_start >= TEXT_VMADDR + text_size:
            self.uc.mem_map(dc_start, dc_size, UC_PROT_ALL)
        self.uc.mem_write(
            DATA_CONST_VMADDR,
            self.binary_data[DATA_CONST_FILEOFF:DATA_CONST_FILEOFF + DATA_CONST_FILESIZE],
        )
        print(f"  Mapped __DATA_CONST: 0x{DATA_CONST_VMADDR:x} size=0x{dc_size:x}")

        # Map __DATA segment
        d_size = (DATA_FILESIZE + 0xFFF) & ~0xFFF
        d_start = DATA_VMADDR & ~0xFFF
        if d_start >= DATA_CONST_VMADDR + dc_size:
            self.uc.mem_map(d_start, d_size + 0x100000, UC_PROT_ALL)  # extra for BSS
        self.uc.mem_write(
            DATA_VMADDR,
            self.binary_data[DATA_FILEOFF:DATA_FILEOFF + DATA_FILESIZE],
        )
        print(f"  Mapped __DATA: 0x{DATA_VMADDR:x} size=0x{d_size:x}")

        return True

    def alloc(self, size: int) -> int:
        """Allocate memory on the emulator heap."""
        ptr = HEAP_BASE + self.heap_cursor
        self.heap_cursor += (size + 15) & ~15
        return ptr

    def alloc_write(self, data: bytes) -> int:
        """Allocate and write data to the emulator heap."""
        ptr = self.alloc(len(data))
        self.uc.mem_write(ptr, data)
        return ptr

    def read(self, addr: int, size: int) -> bytes:
        """Read memory from the emulator."""
        return bytes(self.uc.mem_read(addr, size))

    def reset(self):
        """Reset emulator state between calls."""
        from unicorn.arm64_const import UC_ARM64_REG_SP
        self.heap_cursor = 0
        self.uc.reg_write(UC_ARM64_REG_SP, STACK_BASE + STACK_SIZE - 0x200)
        self.uc.mem_write(HEAP_BASE, b'\x00' * min(HEAP_SIZE, 0x10000))

    def test_sm4_init(self) -> bool:
        """Test: call the SM4 IV init function to verify emulation works."""
        from unicorn.arm64_const import (
            UC_ARM64_REG_X0, UC_ARM64_REG_X30,
        )
        from unicorn import UC_HOOK_MEM_UNMAPPED

        self.reset()

        # SM4 init at 0x101ccfc70 takes x0 = output buffer (32 bytes)
        out_buf = self.alloc(32)
        self.uc.reg_write(UC_ARM64_REG_X0, out_buf)

        # Set return address to a known location
        ret_addr = self.alloc(4)
        self.uc.mem_write(ret_addr, b'\xc0\x03\x5f\xd6')  # RET instruction
        self.uc.reg_write(UC_ARM64_REG_X30, ret_addr)

        errors = []
        def hook_err(uc, access, address, size, value, ud):
            errors.append(f"mem error at 0x{address:x}")
            uc.emu_stop()
            return False

        h = self.uc.hook_add(UC_HOOK_MEM_UNMAPPED, hook_err)

        try:
            self.uc.emu_start(SM4_INIT_IV, ret_addr, timeout=5_000_000)
        except Exception as e:
            errors.append(str(e))

        self.uc.hook_del(h)

        if errors:
            print(f"  SM4 init test FAILED: {errors}")
            return False

        result = self.read(out_buf, 32)
        # Expected: SM4 FK constants (0xa3b1bac6, 0x56aa3350, 0x677d9197, 0xb27022dc)
        # Actually this is the SM3/SM4 IV initialization
        expected_first_word = struct.unpack("<I", result[:4])[0]
        print(f"  SM4 init result: {result.hex()}")
        print(f"  First word: 0x{expected_first_word:08x}")

        return len(errors) == 0


# ---------------------------------------------------------------------------
# Decrypt Function Finder
# ---------------------------------------------------------------------------

def find_decrypt_functions():
    """Search the ARM64 binary for packet decrypt function candidates.

    Looks for functions that:
    1. Call into the crypto region (0x101cc0000 - 0x101ce0000)
    2. Take buffer pointers as arguments
    3. XOR cipher output with input data
    """
    extract_arm64_binary()

    with open(ARM64_BINARY, "rb") as f:
        binary = f.read()

    try:
        from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    except ImportError:
        print("ERROR: capstone not installed. Run: pip install capstone")
        return

    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)

    print(f"\n{'='*60}")
    print("SEARCHING FOR DECRYPT FUNCTIONS IN ARM64 BINARY")
    print(f"{'='*60}")

    # Key addresses found by analysis
    print(f"\n  Known crypto functions:")
    print(f"    SM4 key expansion:    0x{SM4_KEY_EXPAND:x}")
    print(f"    SM4 init (load IV):   0x{SM4_INIT_IV:x}")
    print(f"    SM4-CTR encrypt:      0x{SM4_CTR_ENCRYPT:x}")
    print(f"    SM4-ECB encrypt:      0x{SM4_ECB_ENCRYPT:x}")
    print(f"    Stream decrypt A:     0x{CRYPTO_ENCRYPT_STREAM_A:x}")
    print(f"    Stream decrypt B:     0x{CRYPTO_ENCRYPT_STREAM_B:x}")

    # Find callers of the streaming decrypt functions
    targets = {
        "SM4_CTR_stream_A": CRYPTO_ENCRYPT_STREAM_A,
        "SM4_ECB_stream_B": CRYPTO_ENCRYPT_STREAM_B,
        "SM4_CTR_stream_C": CRYPTO_ENCRYPT_STREAM_C,
        "SM4_stream_D":     CRYPTO_ENCRYPT_STREAM_D,
    }

    TEXT_START = 0xa6ec
    TEXT_SIZE = 0x1e2c32c
    CHUNK = 0x200000

    for name, target in targets.items():
        callers = []
        for off in range(TEXT_START, TEXT_START + TEXT_SIZE, CHUNK):
            end = min(off + CHUNK + 4, len(binary))
            chunk = binary[off:end]
            base = TEXT_VMADDR + off
            for insn in md.disasm(chunk, base):
                if insn.mnemonic == 'bl':
                    try:
                        t = int(insn.op_str.strip('#'), 0)
                        if t == target:
                            callers.append(insn.address)
                    except:
                        pass
        print(f"\n  {name} (0x{target:x}): {len(callers)} callers")
        for c in callers[:5]:
            print(f"    0x{c:x}")

    # The packet decrypt function should be called from the packet dispatch
    # handler. Look for the movement packet ID (0x001c = 28) in comparisons.
    print(f"\n  Searching for movement packet ID (0x1c) comparisons...")
    pkt_id_refs = []
    for off in range(TEXT_START, TEXT_START + TEXT_SIZE, CHUNK):
        end = min(off + CHUNK + 4, len(binary))
        chunk = binary[off:end]
        base = TEXT_VMADDR + off
        for insn in md.disasm(chunk, base):
            if insn.mnemonic == 'cmp' and '#0x1c' in insn.op_str:
                pkt_id_refs.append(insn.address)

    print(f"  Found {len(pkt_id_refs)} CMP #0x1c instructions")
    # Show the first few with context
    for ref in pkt_id_refs[:10]:
        file_off = ref - TEXT_VMADDR
        code = binary[file_off:file_off + 20]
        for insn in md.disasm(code, ref):
            print(f"    0x{insn.address:x}: {insn.mnemonic} {insn.op_str}")
            break


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="ARM64 Movement Packet Decoder for ROFL2 replays"
    )
    ap.add_argument(
        "rofl_file",
        nargs="?",
        default="/Users/sommesi/projects/lol-replay-analyzer/replays/EUW1-7816865419.rofl",
    )
    ap.add_argument("--dump-sections", action="store_true",
                    help="Dump ARM64 binary sections to /tmp/")
    ap.add_argument("--find-decrypt", action="store_true",
                    help="Search for decrypt function addresses")
    ap.add_argument("--emulate", action="store_true",
                    help="Test Unicorn ARM64 emulation")
    ap.add_argument("--analyze", action="store_true",
                    help="Run structural analysis on movement packets")
    ap.add_argument("--output", "-o", type=str, metavar="PATH",
                    help="Export results as JSON")
    ap.add_argument("--max-packets", type=int, default=0,
                    help="Max packets to process (0 = all)")

    args = ap.parse_args()

    print("ARM64 Movement Packet Decoder for ROFL2")
    print(f"{'='*60}")

    # Step 1: Dump sections
    if args.dump_sections:
        dump_sections()
        return

    # Step 2: Find decrypt functions
    if args.find_decrypt:
        find_decrypt_functions()
        return

    # Step 3: Test emulation
    if args.emulate:
        print("\nSetting up ARM64 emulator...")
        emu = ARM64Emulator()
        if not emu.load_binary():
            return
        if not emu.setup():
            return
        print("\nTesting SM4 init function...")
        emu.test_sm4_init()
        return

    # Default: analyze replay
    if not os.path.exists(args.rofl_file):
        print(f"ERROR: Replay file not found: {args.rofl_file}")
        sys.exit(1)

    print(f"\nParsing {args.rofl_file}...")
    parser = ROFL2Parser(args.rofl_file)
    print(f"  Game version: {parser.header.game_version}")
    print(f"  Game length: {parser.metadata.game_length_ms / 1000:.0f}s")
    print(f"  Players: {len(parser.metadata.players)}")

    if args.analyze:
        result = analyze_packet_structure(parser, args.max_packets)

        if args.output:
            export = {
                "game_id": parser.game_id,
                "game_version": parser.header.game_version,
                "game_length_ms": parser.metadata.game_length_ms,
                "players": parser.metadata.players,
                "analysis": {
                    "individual_count": len(result["individual"]),
                    "batch_count": len(result["batch"]),
                    "entities": {
                        f"0x{eid:08x}": info
                        for eid, info in result["entities"].items()
                    },
                },
            }
            os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
            with open(args.output, "w") as f:
                json.dump(export, f, indent=2)
            print(f"\n  Exported to {args.output}")
        return

    # Without specific flags, show a summary and suggest next steps
    print(f"\n  Use --dump-sections to extract ARM64 binary segments")
    print(f"  Use --find-decrypt to search for decrypt function addresses")
    print(f"  Use --emulate to test ARM64 emulation")
    print(f"  Use --analyze to analyze movement packet structure")
    print(f"")
    print(f"  Architecture overview:")
    print(f"  {'='*50}")
    print(f"  The ROFL2 movement packets (pkt_id=0x001c) are encrypted")
    print(f"  using SM4 block cipher in CTR mode. The game binary at:")
    print(f"    {LOL_BINARY}")
    print(f"  contains the ARM64 decrypt function.")
    print(f"")
    print(f"  Key binary addresses (ARM64 slice):")
    print(f"    SM4 key expansion:  0x{SM4_KEY_EXPAND:x}")
    print(f"    SM4 CTR encrypt:    0x{SM4_CTR_ENCRYPT:x}")
    print(f"    Crypto dispatch:    0x101c92800 - 0x101c93000")
    print(f"")
    print(f"  The decrypt approach requires:")
    print(f"    1. Finding the exact decrypt entry point (rva_start)")
    print(f"    2. Setting up the packet struct (0x90 bytes)")
    print(f"    3. Loading the raw payload into the struct")
    print(f"    4. Emulating the decrypt function via Unicorn ARM64")
    print(f"    5. Reading the decrypted payload from the output struct")
    print(f"    6. Parsing with PathPacket.parse()")
    print(f"")
    print(f"  To generate a .patch config for this game version,")
    print(f"  use Ghidra to analyze the ARM64 binary and identify:")
    print(f"    - mov_decrypt.rva_start / rva_end")
    print(f"    - mov_decrypt.payload_offset / payload_size_offset")
    print(f"    - alloc1_rva, alloc2_rva, skip_rva")


if __name__ == "__main__":
    main()
