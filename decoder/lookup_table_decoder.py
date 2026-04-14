#!/usr/bin/env python3
"""
ROFL2 Movement Packet Decoder using XOR 0xfa encoding.

This decoder handles League of Legends ROFL2 movement packets (packet_id 0x001c).
The encoding is a simple per-byte XOR with 0xfa.

Binary analysis source: /tmp/lol_arm64 (ARM64 Mach-O)
Text segment vmaddr: 0x100000000

Findings:
  - Vtable at 0x1022bf318 references packet_id 0x1c
  - Handler functions at 0x101c96ba8/bbc/be8 implement SHA-256 integrity checks
  - The SHA-256 compression function at 0x101cc8534 uses standard round constants
    (0x428a2f98, 0x71374491, 0xb5c0fbcf, etc.)
  - Lookup tables at 0x101f37af8 and 0x101f379f8 (both 256-byte permutations)
    are used for game logic transforms, NOT packet encoding
  - The transforms at 0x1000aa3f8 (bit-swap, XOR, table lookup, ROL) operate
    on parsed game state fields, not raw packet bytes
  - The actual packet encoding is simple XOR 0xfa

Packet format (after XOR 0xfa decoding):
  - 0x00 bytes = no data / no change for that field
  - Each entity record is approximately 133 bytes of data + 3-byte trailer
  - Records are concatenated in batch packets
  - Entity identifier is in bytes near the start of each record
  - Field values in 8-byte slots within the 128-byte data section

Usage:
    python lookup_table_decoder.py <replay.rofl> [--timestamp T] [--all]
"""

import argparse
import struct
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Optional

sys.path.insert(0, ".")
try:
    from sm4_decoder import ROFL2Parser
except ImportError:
    sys.exit("ERROR: sm4_decoder.py not found. Run from the decoder/ directory.")


XOR_KEY = 0xfa


def xor_decode(data: bytes) -> bytes:
    """Decode packet payload by XOR with 0xfa."""
    return bytes(b ^ XOR_KEY for b in data)


@dataclass
class EntityRecord:
    """A single entity update record from a movement packet."""
    entity_bytes: tuple  # (byte1, byte2) entity identifier
    flags: int  # field presence flags
    data: bytes  # 128-byte data section
    raw_offset: int  # offset in original packet


@dataclass
class MovementUpdate:
    """Parsed movement update for an entity at a timestamp."""
    timestamp: float
    entity_key: tuple  # (byte1, byte2) entity identifier
    entity_id: Optional[int]  # 0x400000xx if mapped from spawn
    champion: str
    slots: dict  # slot_index -> 8-byte slot data
    flags: int


def parse_entity_records(decoded: bytes) -> list[EntityRecord]:
    """Parse entity records from a decoded (XOR'd) batch packet payload."""
    records = []
    pos = 0

    if len(decoded) < 6:
        return records

    # First record: 5-byte header + 128-byte data + 3-byte trailer = 136 bytes
    # Header: type(1) + subtype(1) + entity(2) + flags(1)
    if len(decoded) >= 136:
        entity_bytes = (decoded[2], decoded[3])
        flags = decoded[4]
        data = decoded[5:133]
        records.append(EntityRecord(entity_bytes, flags, data, 0))
        pos = 133  # after data, at trailer

    # Subsequent records: trailer(3) + entity(2) + flags(1) + data(128) + trailer(3)
    while pos + 3 + 2 + 1 + 128 <= len(decoded):
        # Trailer bytes mark the boundary
        trailer = decoded[pos:pos + 3]
        pos += 3

        # Next record header
        if pos + 2 + 1 + 128 > len(decoded):
            break

        entity_bytes = (decoded[pos], decoded[pos + 1])
        flags = decoded[pos + 2]
        data_start = pos + 3
        data_end = data_start + 128

        if data_end > len(decoded):
            break

        data = decoded[data_start:data_end]
        records.append(EntityRecord(entity_bytes, flags, data, pos))
        pos = data_end

    return records


def extract_slots(data: bytes) -> dict[int, bytes]:
    """Extract non-zero 8-byte slots from a 128-byte data section."""
    slots = {}
    for i in range(16):
        slot = data[i * 8:(i + 1) * 8]
        if any(b != 0 for b in slot):
            slots[i] = slot
    return slots


class MovementDecoder:
    """Decoder for ROFL2 movement packets."""

    def __init__(self, rofl_path: str):
        self.parser = ROFL2Parser(rofl_path)
        self.entity_map = {}  # (byte1, byte2) -> entity_id
        self.entity_names = {}  # entity_id -> champion name
        self._build_entity_map()

    def _build_entity_map(self):
        """Build entity identifier map from spawn packets."""
        for block in self.parser.iter_blocks():
            if block.packet_id == 0x001c and block.param != 0:
                decoded = xor_decode(block.payload)
                key = (decoded[2], decoded[3])
                self.entity_map[key] = block.param

                idx = block.param - 0x400000ae
                if 0 <= idx < len(self.parser.metadata.players):
                    self.entity_names[block.param] = (
                        self.parser.metadata.players[idx]["skin"]
                    )

    def iter_updates(self, start_time=0.0, end_time=float("inf")):
        """Iterate over movement updates in the replay."""
        parser = ROFL2Parser(self.parser.filepath)

        for block in parser.iter_blocks():
            if block.packet_id != 0x001c:
                continue
            if block.timestamp < start_time or block.timestamp > end_time:
                continue

            decoded = xor_decode(block.payload)

            if block.param != 0:
                # Spawn packet (single entity)
                entity_id = block.param
                entity_key = (decoded[2], decoded[3])
                slots = extract_slots(decoded[5:133]) if len(decoded) >= 133 else {}
                champion = self.entity_names.get(entity_id, "Unknown")

                yield MovementUpdate(
                    timestamp=block.timestamp,
                    entity_key=entity_key,
                    entity_id=entity_id,
                    champion=champion,
                    slots=slots,
                    flags=decoded[4] if len(decoded) > 4 else 0,
                )
            else:
                # Batch packet (multiple entities)
                records = parse_entity_records(decoded)
                for rec in records:
                    entity_id = self.entity_map.get(rec.entity_bytes)
                    champion = self.entity_names.get(entity_id, "?")
                    slots = extract_slots(rec.data)

                    yield MovementUpdate(
                        timestamp=block.timestamp,
                        entity_key=rec.entity_bytes,
                        entity_id=entity_id,
                        champion=champion,
                        slots=slots,
                        flags=rec.flags,
                    )

    def get_stats(self):
        """Get statistics about movement packets in the replay."""
        parser = ROFL2Parser(self.parser.filepath)

        spawn_count = 0
        batch_count = 0
        total_records = 0
        size_counter = Counter()
        entity_update_counts = Counter()

        for block in parser.iter_blocks():
            if block.packet_id != 0x001c:
                continue

            if block.param != 0:
                spawn_count += 1
            else:
                batch_count += 1
                decoded = xor_decode(block.payload)
                records = parse_entity_records(decoded)
                total_records += len(records)
                for rec in records:
                    entity_update_counts[rec.entity_bytes] += 1

            size_counter[len(block.payload)] += 1

        return {
            "spawn_packets": spawn_count,
            "batch_packets": batch_count,
            "total_entity_records": total_records,
            "top_sizes": size_counter.most_common(10),
            "entity_updates": entity_update_counts.most_common(20),
            "entity_map": dict(self.entity_map),
            "players": self.parser.metadata.players,
        }


def main():
    ap = argparse.ArgumentParser(description="ROFL2 Movement Packet Decoder")
    ap.add_argument("replay", help="Path to .rofl replay file")
    ap.add_argument("--stats", action="store_true", help="Show packet statistics")
    ap.add_argument(
        "--timestamp", "-t", type=float, default=None,
        help="Show updates at a specific timestamp (seconds)",
    )
    ap.add_argument(
        "--window", "-w", type=float, default=1.0,
        help="Time window around --timestamp (default 1.0s)",
    )
    ap.add_argument(
        "--entity", "-e", type=str, default=None,
        help="Filter by champion name (e.g., Sylas)",
    )
    ap.add_argument(
        "--all", action="store_true",
        help="Show all updates (verbose)",
    )
    ap.add_argument(
        "--dump-raw", action="store_true",
        help="Dump raw decoded bytes for each update",
    )
    args = ap.parse_args()

    decoder = MovementDecoder(args.replay)

    if args.stats:
        stats = decoder.get_stats()
        print(f"Replay: {args.replay}")
        print(f"Players:")
        for i, p in enumerate(stats["players"]):
            entity_id = 0x400000ae + i
            print(
                f"  {i}: {p['name']:20s} ({p['skin']:12s}) "
                f"{p['team']:4s} {p['position']:8s} "
                f"entity=0x{entity_id:08x}"
            )

        print(f"\nMovement packets:")
        print(f"  Spawn (individual): {stats['spawn_packets']}")
        print(f"  Batch (multi):      {stats['batch_packets']}")
        print(f"  Total records:      {stats['total_entity_records']}")

        print(f"\nPacket sizes (top 10):")
        for size, count in stats["top_sizes"]:
            print(f"  {size:6d} bytes: {count:6d} packets")

        print(f"\nEntity map (spawn identification):")
        for key, eid in sorted(
            stats["entity_map"].items(), key=lambda x: x[1]
        ):
            idx = eid - 0x400000ae
            name = stats["players"][idx]["skin"] if idx < 10 else "?"
            print(f"  ({key[0]:02x},{key[1]:02x}) -> Entity {idx} ({name})")

        print(f"\nEntity update counts (top 20):")
        for key, count in stats["entity_updates"]:
            eid = stats["entity_map"].get(key)
            if eid:
                idx = eid - 0x400000ae
                name = stats["players"][idx]["skin"]
            else:
                name = "?"
            print(f"  ({key[0]:02x},{key[1]:02x}) [{name:12s}]: {count:6d}")

        return

    # Show updates
    if args.timestamp is not None:
        start = args.timestamp - args.window / 2
        end = args.timestamp + args.window / 2
    elif args.all:
        start = 0.0
        end = float("inf")
    else:
        start = 0.0
        end = 10.0  # default: first 10 seconds

    count = 0
    for update in decoder.iter_updates(start, end):
        if args.entity and args.entity.lower() not in update.champion.lower():
            continue

        eid_str = (
            f"0x{update.entity_id:08x}" if update.entity_id else "unknown"
        )
        print(
            f"t={update.timestamp:8.3f}s "
            f"entity=({update.entity_key[0]:02x},{update.entity_key[1]:02x}) "
            f"[{update.champion:12s}] "
            f"flags=0x{update.flags:02x} "
            f"id={eid_str}"
        )

        if update.slots:
            for sid in sorted(update.slots):
                slot_hex = update.slots[sid].hex()
                print(f"    slot {sid:2d}: {slot_hex}")

        count += 1
        if count >= 1000 and not args.all:
            print(f"\n... truncated at 1000 updates (use --all for full output)")
            break

    print(f"\nTotal updates shown: {count}")


if __name__ == "__main__":
    main()
