#!/usr/bin/env python3
"""
Packet decryptor for ROFL2 League of Legends replays.

Handles two decryption scenarios:
1. Movement packets (0x001C): XOR 0xFA encoding (not HMAC-SM3 encrypted)
2. High-entropy packets: HMAC-SM3-CTR keystream decryption

The HMAC-SM3-CTR algorithm (reverse-engineered from the Mac x86_64 binary):
    keystream_block[i] = HMAC_SM3(key, data_prefix || BigEndian32(counter))
    plaintext = ciphertext XOR keystream

SM3 is the Chinese standard hash (GB/T 32905-2016) with 256-bit output.

Binary analysis references (Mac Mach-O, base VA 0x100000000):
    0x101f779c0  streaming_decrypt_with_xor (sets up context, calls keystream gen, XORs)
    0x101ed1000  hmac_stream_decrypt (HMAC-CTR keystream generator)
    0x101f7a080  SM3 block cipher primitive (confirmed by IV: 7380166f 4914b2b9 ...)
    0x1025741c0  Algorithm descriptor (output_size=32, IDs 0x477/0x478)

Usage:
    python packet_decryptor.py <replay.rofl> [--movement] [--decrypt-all] [--key HEX]
"""

import argparse
import json
import math
import os
import struct
import sys
from collections import Counter
from dataclasses import dataclass
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sm4_decoder import ROFL2Parser, Block


# ===========================================================================
# SM3 Hash Implementation (GB/T 32905-2016)
# ===========================================================================

SM3_IV = [
    0x7380166f, 0x4914b2b9, 0x172442d7, 0xda8a0600,
    0xa96f30bc, 0x163138aa, 0xe38dee4d, 0xb0fb0e4e,
]

MASK32 = 0xFFFFFFFF


def _rotl32(x: int, n: int) -> int:
    """32-bit left rotation."""
    return ((x << n) | (x >> (32 - n))) & MASK32


def _sm3_ff(j: int, x: int, y: int, z: int) -> int:
    if j < 16:
        return (x ^ y ^ z) & MASK32
    return ((x & y) | (x & z) | (y & z)) & MASK32


def _sm3_gg(j: int, x: int, y: int, z: int) -> int:
    if j < 16:
        return (x ^ y ^ z) & MASK32
    return ((x & y) | (~x & z)) & MASK32


def _sm3_p0(x: int) -> int:
    return (x ^ _rotl32(x, 9) ^ _rotl32(x, 17)) & MASK32


def _sm3_p1(x: int) -> int:
    return (x ^ _rotl32(x, 15) ^ _rotl32(x, 23)) & MASK32


def _sm3_t(j: int) -> int:
    return 0x79cc4519 if j < 16 else 0x7a879d8a


def _sm3_expand(b: bytes) -> tuple[list[int], list[int]]:
    """Message expansion: produce W[0..67] and W'[0..63]."""
    w = [0] * 68
    for i in range(16):
        w[i] = struct.unpack(">I", b[i * 4 : i * 4 + 4])[0]
    for i in range(16, 68):
        w[i] = (_sm3_p1(w[i - 16] ^ w[i - 9] ^ _rotl32(w[i - 3], 15))
                ^ _rotl32(w[i - 13], 7) ^ w[i - 6]) & MASK32
    wp = [(w[i] ^ w[i + 4]) & MASK32 for i in range(64)]
    return w, wp


def _sm3_compress(v: list[int], block: bytes) -> list[int]:
    """Compress one 64-byte block into the SM3 state."""
    w, wp = _sm3_expand(block)
    a, b, c, d, e, f, g, h = v

    for j in range(64):
        tj = _sm3_t(j)
        ss1 = _rotl32((_rotl32(a, 12) + e + _rotl32(tj, j % 32)) & MASK32, 7)
        ss2 = (ss1 ^ _rotl32(a, 12)) & MASK32
        tt1 = (_sm3_ff(j, a, b, c) + d + ss2 + wp[j]) & MASK32
        tt2 = (_sm3_gg(j, e, f, g) + h + ss1 + w[j]) & MASK32
        d = c
        c = _rotl32(b, 9)
        b = a
        a = tt1
        h = g
        g = _rotl32(f, 19)
        f = e
        e = _sm3_p0(tt2)

    return [
        (a ^ v[0]) & MASK32, (b ^ v[1]) & MASK32,
        (c ^ v[2]) & MASK32, (d ^ v[3]) & MASK32,
        (e ^ v[4]) & MASK32, (f ^ v[5]) & MASK32,
        (g ^ v[6]) & MASK32, (h ^ v[7]) & MASK32,
    ]


def sm3_hash(message: bytes) -> bytes:
    """Compute SM3 hash of a message. Returns 32 bytes."""
    # Pad message: append 1 bit, then zeros, then 64-bit big-endian length
    msg_len_bits = len(message) * 8
    message += b"\x80"
    while (len(message) % 64) != 56:
        message += b"\x00"
    message += struct.pack(">Q", msg_len_bits)

    # Process 64-byte blocks
    v = list(SM3_IV)
    for i in range(0, len(message), 64):
        v = _sm3_compress(v, message[i : i + 64])

    return b"".join(struct.pack(">I", word) for word in v)


# ===========================================================================
# SM3 Self-test
# ===========================================================================

def sm3_self_test() -> bool:
    """Verify SM3 against the official test vectors from GB/T 32905-2016."""
    # Test vector 1: "abc"
    msg1 = b"abc"
    expected1 = "66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0"
    result1 = sm3_hash(msg1).hex()
    ok1 = result1 == expected1

    # Test vector 2: "abcd" * 16 (64 bytes)
    msg2 = b"abcd" * 16
    expected2 = "debe9ff92275b8a138604889c18e5a4d6fdb70e5387e5765293dcba39c0c5732"
    result2 = sm3_hash(msg2).hex()
    ok2 = result2 == expected2

    return ok1 and ok2


# ===========================================================================
# HMAC-SM3
# ===========================================================================

SM3_BLOCK_SIZE = 64  # SM3 processes 64-byte (512-bit) blocks


def hmac_sm3(key: bytes, message: bytes) -> bytes:
    """Compute HMAC-SM3(key, message). Returns 32 bytes.

    HMAC(K, m) = H((K' XOR opad) || H((K' XOR ipad) || m))
    where K' = H(K) if len(K) > block_size, else K padded to block_size.
    """
    # Step 1: Derive K' (key padded/hashed to block size)
    if len(key) > SM3_BLOCK_SIZE:
        key = sm3_hash(key)
    key_padded = key + b"\x00" * (SM3_BLOCK_SIZE - len(key))

    # Step 2: Compute inner and outer padded keys
    ipad = bytes(k ^ 0x36 for k in key_padded)
    opad = bytes(k ^ 0x5C for k in key_padded)

    # Step 3: HMAC = H(opad || H(ipad || message))
    inner_hash = sm3_hash(ipad + message)
    return sm3_hash(opad + inner_hash)


# ===========================================================================
# HMAC-CTR Keystream Generator
# ===========================================================================

def hmac_ctr_keystream(key: bytes, data_prefix: bytes, length: int) -> bytes:
    """Generate keystream using HMAC-CTR mode.

    From the binary at 0x101ed1000 (hmac_stream_decrypt):
        for counter = 1, 2, 3, ...:
            keystream_block = HMAC_SM3(key, data_prefix || BigEndian32(counter))
        keystream is concatenated blocks, truncated to `length`.

    The binary also supports an `extra` parameter appended after the counter,
    but in the streaming_decrypt path both extra_data and extra_len are 0.

    Args:
        key: HMAC key (from the session/algo descriptor)
        data_prefix: data fed into each HMAC call before the counter
        length: total keystream bytes needed (= ciphertext length)

    Returns:
        Keystream bytes of the requested length.
    """
    keystream = bytearray()
    counter = 1
    while len(keystream) < length:
        block_input = data_prefix + struct.pack(">I", counter)
        keystream.extend(hmac_sm3(key, block_input))
        counter += 1
    return bytes(keystream[:length])


def hmac_ctr_decrypt(key: bytes, data_prefix: bytes, ciphertext: bytes) -> bytes:
    """Decrypt ciphertext using HMAC-SM3-CTR mode.

    plaintext[i] = ciphertext[i] XOR keystream[i]
    """
    ks = hmac_ctr_keystream(key, data_prefix, len(ciphertext))
    return bytes(c ^ k for c, k in zip(ciphertext, ks))


# ===========================================================================
# Movement Packet Decoder
# ===========================================================================

MOVEMENT_PACKET_ID = 0x001C
FILL_BYTE = 0xFA


def sign_extend_16(value: int) -> int:
    """Sign-extend a 16-bit value to signed integer."""
    return value - 0x10000 if value & 0x8000 else value


@dataclass
class MovementUpdate:
    """A single entity's position/movement update."""
    timestamp: float
    entity_id: int           # from Block.param (0x400000XX for champions)
    speed: float
    waypoints: list          # list of (x, y) tuples in world coordinates
    is_batch: bool = False   # True if from a batch (param=0) packet

    @property
    def position(self) -> Optional[tuple[float, float]]:
        """Current position (first waypoint)."""
        return self.waypoints[0] if self.waypoints else None


@dataclass
class PathPacket:
    """Parser for the PathPacket movement format.

    Format (after XOR 0xFA):
        u16: parsing_type
        u32: entity_id (often overridden by Block.param)
        f32: speed
        [bit-packed delta-compressed waypoints]

    Coordinate transform:
        world_x = sign_extend(raw_x, 16) * 2.0 + 7358.0
        world_y = sign_extend(raw_y, 16) * 2.0 + 7412.0

    Map dimensions: roughly (0, 0) to (15000, 15000).
    """
    timestamp: float
    entity_id: int
    speed: float
    waypoints: list

    @staticmethod
    def parse(timestamp: float, payload: bytes,
              entity_id_override: int = 0) -> Optional["PathPacket"]:
        """Parse a PathPacket from XOR-decoded payload bytes."""
        if len(payload) < 10:
            return None

        pos = 0
        parsing_type = struct.unpack_from("<H", payload, pos)[0]
        pos += 2
        entity_id = struct.unpack_from("<I", payload, pos)[0]
        pos += 4
        speed = struct.unpack_from("<f", payload, pos)[0]
        pos += 4

        # Use Block.param as entity_id if provided and payload eid looks wrong
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
                v16 = v13
                v17 = v13 & 7
                if v13 < 0:
                    v16 = v13 + 7
                    v17 = (v13 & 7) - 8
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
                bit2 = ((temp_arr[byte_idx2] >> (v21 & 7)) & 1
                        if byte_idx2 < len(temp_arr) else 0)
                v15 = 2 - (1 if bit2 else 0)
                v13 += 2

            if pos >= len(payload):
                break
            if v14 == 1:
                if pos >= len(payload):
                    break
                x_coord = (x_coord + payload[pos]) & 0xFFFF
                pos += 1
            else:
                if pos + 2 > len(payload):
                    break
                x_coord = struct.unpack_from("<H", payload, pos)[0]
                pos += 2
            if v15 == 1:
                if pos >= len(payload):
                    break
                y_coord = (y_coord + payload[pos]) & 0xFFFF
                pos += 1
            else:
                if pos + 2 > len(payload):
                    break
                y_coord = struct.unpack_from("<H", payload, pos)[0]
                pos += 2

            encoded_coords.append(x_coord)
            encoded_coords.append(y_coord)
            v10 += 1

        waypoints = []
        for i in range(0, len(encoded_coords), 2):
            x = sign_extend_16(encoded_coords[i]) * 2.0 + 7358.0
            y = sign_extend_16(encoded_coords[i + 1]) * 2.0 + 7412.0
            waypoints.append((x, y))

        return PathPacket(
            timestamp=timestamp,
            entity_id=entity_id,
            speed=speed,
            waypoints=waypoints,
        )


def decode_movement_payload(payload: bytes) -> bytes:
    """Decode a movement packet payload by XOR with 0xFA fill byte.

    Movement packets in ROFL2 use 0xFA as the "zero" byte. The actual data
    is obtained by XOR-ing each byte with 0xFA. This is NOT cryptographic
    encryption -- it's a format convention where 0xFA represents unused/zero
    bytes in the sparse packet structure.
    """
    return bytes(b ^ FILL_BYTE for b in payload)


def extract_movement_updates(
    parser: ROFL2Parser,
    *,
    champions_only: bool = True,
    max_packets: int = 0,
) -> list[MovementUpdate]:
    """Extract all movement updates from a ROFL2 replay.

    Args:
        parser: initialized ROFL2Parser
        champions_only: if True, only return updates for champion entities
                       (param in 0x400000ae-0x400000b7 range)
        max_packets: limit number of packets processed (0 = all)

    Returns:
        List of MovementUpdate objects sorted by timestamp.
    """
    updates = []
    count = 0

    for block in parser.iter_blocks():
        if block.packet_id != MOVEMENT_PACKET_ID:
            continue
        count += 1
        if max_packets and count > max_packets:
            break

        decoded = decode_movement_payload(block.payload)

        if block.param != 0:
            # Individual entity packet
            if champions_only and not (0x400000ae <= block.param <= 0x400000b7):
                continue

            pkt = PathPacket.parse(block.timestamp, decoded, block.param)
            if pkt and pkt.waypoints:
                updates.append(MovementUpdate(
                    timestamp=block.timestamp,
                    entity_id=block.param,
                    speed=pkt.speed,
                    waypoints=pkt.waypoints,
                    is_batch=False,
                ))
        else:
            # Batch packet (param=0): contains one entity's path data
            # Entity ID is encoded in the payload but not in the standard
            # 0x40000000 format. Use the parsed entity_id from PathPacket.
            pkt = PathPacket.parse(block.timestamp, decoded)
            if pkt and pkt.waypoints:
                updates.append(MovementUpdate(
                    timestamp=block.timestamp,
                    entity_id=pkt.entity_id,
                    speed=pkt.speed,
                    waypoints=pkt.waypoints,
                    is_batch=True,
                ))

    updates.sort(key=lambda u: u.timestamp)
    return updates


# ===========================================================================
# Encrypted Packet Handler
# ===========================================================================

# Packet types with high entropy (> 6.0) that may be HMAC-SM3-CTR encrypted.
# These were identified by entropy analysis of the ROFL2 replay data.
HIGH_ENTROPY_PACKET_IDS = {
    0x02d3, 0x024f, 0x031c, 0x034a, 0x00e2, 0x0061,
    0x00dc, 0x03f7, 0x0314, 0x036c, 0x026a, 0x0268,
    0x0101, 0x0449, 0x0363, 0x0259, 0x039b, 0x00f4,
}


def try_decrypt_packet(
    payload: bytes,
    key: bytes,
    data_prefix: bytes = b"",
) -> tuple[bytes, float]:
    """Attempt HMAC-SM3-CTR decryption of a packet payload.

    Returns:
        (decrypted_bytes, entropy_reduction)
        entropy_reduction > 0 means decryption likely succeeded.
    """
    original_entropy = _entropy(payload)
    decrypted = hmac_ctr_decrypt(key, data_prefix, payload)
    new_entropy = _entropy(decrypted)
    return decrypted, original_entropy - new_entropy


def _entropy(data: bytes) -> float:
    """Shannon entropy in bits per byte."""
    if not data:
        return 0.0
    counts = Counter(data)
    total = len(data)
    return -sum((c / total) * math.log2(c / total) for c in counts.values())


# ===========================================================================
# Key Discovery
# ===========================================================================

def discover_potential_keys(parser: ROFL2Parser) -> list[tuple[str, bytes]]:
    """Generate potential decryption keys from ROFL2 file metadata.

    The actual key derivation used by the game client involves:
    1. Session key from the replay file header or server handshake
    2. Key expansion through the crypto API (EVP-like, see binary analysis)
    3. HMAC key setup via set_hmac_key at 0x101e8d9e0

    Since we don't have the original session key, we try various derivations.
    """
    game_id = parser.game_id.encode("ascii")
    file_hash = parser.header.file_hash
    version = parser.header.game_version.encode("ascii")

    candidates = [
        ("game_id_raw", game_id),
        ("game_id_sm3", sm3_hash(game_id)),
        ("file_hash_raw", file_hash),
        ("file_hash_sm3", sm3_hash(file_hash)),
        ("game_id+hash", sm3_hash(game_id + file_hash)),
        ("hash+game_id", sm3_hash(file_hash + game_id)),
        ("version+game_id", sm3_hash(version + game_id)),
        ("game_id+version", sm3_hash(game_id + version)),
        ("game_id_u64_le", struct.pack("<Q", int(parser.game_id))),
        ("game_id_u64_be", struct.pack(">Q", int(parser.game_id))),
    ]

    return candidates


def try_key_on_packets(
    parser: ROFL2Parser,
    key: bytes,
    data_prefix: bytes = b"",
    packet_ids: set[int] = None,
    sample_count: int = 3,
) -> list[dict]:
    """Try a key on high-entropy packets and report results.

    Returns list of result dicts with entropy information.
    """
    if packet_ids is None:
        packet_ids = HIGH_ENTROPY_PACKET_IDS

    results = []
    samples_per_pid = {}

    for block in parser.iter_blocks():
        if block.packet_id not in packet_ids:
            continue
        pid = block.packet_id
        if pid not in samples_per_pid:
            samples_per_pid[pid] = 0
        if samples_per_pid[pid] >= sample_count:
            continue
        samples_per_pid[pid] += 1

        decrypted, reduction = try_decrypt_packet(block.payload, key, data_prefix)
        results.append({
            "packet_id": pid,
            "timestamp": block.timestamp,
            "param": block.param,
            "payload_len": len(block.payload),
            "original_entropy": _entropy(block.payload),
            "decrypted_entropy": _entropy(decrypted),
            "entropy_reduction": reduction,
            "decrypted_preview": decrypted[:32].hex(),
            "success": reduction > 2.0,  # significant entropy drop
        })

        if len(results) >= sample_count * len(packet_ids):
            break

    return results


# ===========================================================================
# Main CLI
# ===========================================================================

def main():
    ap = argparse.ArgumentParser(
        description="Packet decryptor for ROFL2 replays (SM3/HMAC-CTR + movement)"
    )
    ap.add_argument(
        "rofl_file",
        nargs="?",
        default="/Users/sommesi/projects/lol-replay-analyzer/replays/EUW1-7816865419.rofl",
    )
    ap.add_argument("--sm3-test", action="store_true", help="Run SM3 self-test")
    ap.add_argument("--movement", action="store_true",
                    help="Decode and display movement packets")
    ap.add_argument("--decrypt-all", action="store_true",
                    help="Try all key candidates on encrypted packets")
    ap.add_argument("--key", type=str, metavar="HEX",
                    help="Hex-encoded key for HMAC-SM3-CTR decryption")
    ap.add_argument("--data-prefix", type=str, metavar="HEX", default="",
                    help="Hex-encoded data prefix for HMAC input")
    ap.add_argument("--max-packets", type=int, default=0,
                    help="Max packets to process (0 = all)")
    ap.add_argument("--output", "-o", type=str, metavar="PATH",
                    help="Export results as JSON to this path")
    args = ap.parse_args()

    # SM3 self-test
    if args.sm3_test:
        print("SM3 self-test...")
        ok = sm3_self_test()
        print(f"  Test vector 1 ('abc'): "
              f"{sm3_hash(b'abc').hex()}")
        print(f"  Test vector 2 ('abcd'*16): "
              f"{sm3_hash(b'abcd' * 16).hex()}")
        print(f"  Result: {'PASS' if ok else 'FAIL'}")

        # HMAC-SM3 test
        test_key = b"test_key"
        test_msg = b"test_message"
        hmac_result = hmac_sm3(test_key, test_msg)
        print(f"\n  HMAC-SM3('test_key', 'test_message'):")
        print(f"    {hmac_result.hex()}")

        # HMAC-CTR roundtrip
        key = sm3_hash(b"test_key")
        prefix = b"prefix_data"
        plaintext = b"Hello, HMAC-CTR decryption test! " * 3
        ks = hmac_ctr_keystream(key, prefix, len(plaintext))
        ciphertext = bytes(p ^ k for p, k in zip(plaintext, ks))
        decrypted = hmac_ctr_decrypt(key, prefix, ciphertext)
        print(f"\n  HMAC-CTR roundtrip: {'PASS' if decrypted == plaintext else 'FAIL'}")
        return

    if not args.rofl_file:
        ap.error("rofl_file is required (unless using --sm3-test)")

    # Parse replay
    print(f"Parsing {args.rofl_file} ...")
    parser = ROFL2Parser(args.rofl_file)

    print(f"  Game ID:      {parser.game_id}")
    print(f"  Game version: {parser.header.game_version}")
    print(f"  File hash:    {parser.header.file_hash.hex()}")
    print(f"  Game length:  {parser.metadata.game_length_ms / 1000:.0f}s")

    # Movement decoding
    if args.movement:
        print(f"\n{'=' * 60}")
        print("Movement Packet Decoding (XOR 0xFA)")
        print(f"{'=' * 60}")

        updates = extract_movement_updates(
            parser,
            champions_only=True,
            max_packets=args.max_packets,
        )

        # Group by entity
        by_entity = {}
        for u in updates:
            if u.entity_id not in by_entity:
                by_entity[u.entity_id] = []
            by_entity[u.entity_id].append(u)

        print(f"\n  Total updates: {len(updates)}")
        print(f"  Entities: {len(by_entity)}")

        for eid in sorted(by_entity):
            entity_updates = by_entity[eid]
            print(f"\n  Entity 0x{eid:08x}: {len(entity_updates)} updates")
            print(f"    Time range: {entity_updates[0].timestamp:.1f}s - "
                  f"{entity_updates[-1].timestamp:.1f}s")

            # Show first few positions
            for u in entity_updates[:5]:
                pos = u.position
                if pos:
                    print(f"    t={u.timestamp:7.1f}s pos=({pos[0]:7.0f}, {pos[1]:7.0f}) "
                          f"speed={u.speed:.0f} wps={len(u.waypoints)} "
                          f"{'batch' if u.is_batch else 'individual'}")

        # Export if requested
        if args.output:
            export_data = {
                "game_id": parser.game_id,
                "game_version": parser.header.game_version,
                "total_updates": len(updates),
                "entities": {},
            }
            for eid in sorted(by_entity):
                entity_updates = by_entity[eid]
                export_data["entities"][f"0x{eid:08x}"] = [
                    {
                        "t": round(u.timestamp, 3),
                        "speed": round(u.speed, 1),
                        "pos": [round(u.position[0], 1), round(u.position[1], 1)]
                        if u.position else None,
                        "wps": len(u.waypoints),
                    }
                    for u in entity_updates
                ]
            os.makedirs(os.path.dirname(args.output), exist_ok=True)
            with open(args.output, "w") as f:
                json.dump(export_data, f, indent=2)
            print(f"\n  Exported to {args.output}")

    # HMAC-SM3-CTR decryption attempt
    if args.decrypt_all or args.key:
        print(f"\n{'=' * 60}")
        print("HMAC-SM3-CTR Decryption")
        print(f"{'=' * 60}")

        data_prefix = bytes.fromhex(args.data_prefix) if args.data_prefix else b""

        if args.key:
            # Use provided key
            key = bytes.fromhex(args.key)
            print(f"  Key: {key.hex()}")
            print(f"  Data prefix: {data_prefix.hex() if data_prefix else '(empty)'}")

            results = try_key_on_packets(parser, key, data_prefix)
            for r in results:
                status = "OK" if r["success"] else "--"
                print(f"  [{status}] pkt=0x{r['packet_id']:04x} "
                      f"entropy {r['original_entropy']:.2f} -> {r['decrypted_entropy']:.2f} "
                      f"({r['entropy_reduction']:+.2f}) "
                      f"preview={r['decrypted_preview'][:32]}...")
        else:
            # Try all key candidates
            candidates = discover_potential_keys(parser)
            print(f"  Trying {len(candidates)} key candidates...\n")

            for key_name, key in candidates:
                results = try_key_on_packets(
                    parser, key, data_prefix, sample_count=1
                )
                successes = sum(1 for r in results if r["success"])
                best = max(results, key=lambda r: r["entropy_reduction"]) if results else None

                if best and best["entropy_reduction"] > 1.0:
                    print(f"  {key_name:25s}: "
                          f"best reduction = {best['entropy_reduction']:+.2f} "
                          f"({successes}/{len(results)} succeeded)")
                    print(f"    key = {key[:32].hex()}"
                          f"{'...' if len(key) > 32 else ''}")
                    print(f"    preview = {best['decrypted_preview'][:48]}")
                else:
                    avg_red = (sum(r["entropy_reduction"] for r in results) / len(results)
                               if results else 0)
                    print(f"  {key_name:25s}: avg reduction = {avg_red:+.2f} (no success)")

    # Default: show summary
    if not args.movement and not args.decrypt_all and not args.key and not args.sm3_test:
        print(f"\n  Use --movement to decode movement packets")
        print(f"  Use --decrypt-all to try HMAC-SM3-CTR decryption")
        print(f"  Use --sm3-test to verify SM3 implementation")

        # Quick stats
        print(f"\n--- Packet Type Summary ---")
        pid_stats = {}
        total = 0
        for block in parser.iter_blocks():
            total += 1
            pid = block.packet_id
            if pid not in pid_stats:
                pid_stats[pid] = {"count": 0, "bytes": 0, "samples": []}
            pid_stats[pid]["count"] += 1
            pid_stats[pid]["bytes"] += len(block.payload)
            if len(pid_stats[pid]["samples"]) < 3:
                pid_stats[pid]["samples"].append(block.payload)

        print(f"  Total blocks: {total}")
        print(f"  Movement (0x001c): {pid_stats.get(0x001c, {}).get('count', 0)} packets "
              f"(XOR 0xFA encoded, NOT encrypted)")

        encrypted_count = sum(
            pid_stats[pid]["count"]
            for pid in HIGH_ENTROPY_PACKET_IDS
            if pid in pid_stats
        )
        print(f"  High-entropy packets: {encrypted_count} "
              f"(potentially HMAC-SM3-CTR encrypted)")


if __name__ == "__main__":
    main()
