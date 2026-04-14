#!/usr/bin/env python3
"""
ROFL2 Replay Decoder for League of Legends
Handles the ROFL2 format (magic: RIOT, version: 02 00) used in patch 16.7+.

ROFL2 changes from ROFL1:
  - No Blowfish encryption at the chunk level
  - Chunks are directly zstd-compressed
  - No encryption key in payload header
  - SM4 encryption (standard S-box, CTR mode) is used at the individual
    packet field level for specific packet types (e.g., movement, ward spawn)

Usage:
    python sm4_decoder.py <replay.rofl> [--dump-blocks] [--packet-id N] [--sm4-test]
"""

import argparse
import json
import math
import os
import struct
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterator, Optional

try:
    import zstandard
except ImportError:
    sys.exit("ERROR: zstandard not installed. Run: pip install zstandard")


# ---------------------------------------------------------------------------
# SM4 implementation (standard, for packet-level decryption)
# ---------------------------------------------------------------------------

SM4_SBOX = bytes([
    0xd6, 0x90, 0xe9, 0xfe, 0xcc, 0xe1, 0x3d, 0xb7,
    0x16, 0xb6, 0x14, 0xc2, 0x28, 0xfb, 0x2c, 0x05,
    0x2b, 0x67, 0x9a, 0x76, 0x2a, 0xbe, 0x04, 0xc3,
    0xaa, 0x44, 0x13, 0x26, 0x49, 0x86, 0x06, 0x99,
    0x9c, 0x42, 0x50, 0xf4, 0x91, 0xef, 0x98, 0x7a,
    0x33, 0x54, 0x0b, 0x43, 0xed, 0xcf, 0xac, 0x62,
    0xe4, 0xb3, 0x1c, 0xa9, 0xc9, 0x08, 0xe8, 0x95,
    0x80, 0xdf, 0x94, 0xfa, 0x75, 0x8f, 0x3f, 0xa6,
    0x47, 0x07, 0xa7, 0xfc, 0xf3, 0x73, 0x17, 0xba,
    0x83, 0x59, 0x3c, 0x19, 0xe6, 0x85, 0x4f, 0xa8,
    0x68, 0x6b, 0x81, 0xb2, 0x71, 0x64, 0xda, 0x8b,
    0xf8, 0xeb, 0x0f, 0x4b, 0x70, 0x56, 0x9d, 0x35,
    0x1e, 0x24, 0x0e, 0x5e, 0x63, 0x58, 0xd1, 0xa2,
    0x25, 0x22, 0x7c, 0x3b, 0x01, 0x21, 0x78, 0x87,
    0xd4, 0x00, 0x46, 0x57, 0x9f, 0xd3, 0x27, 0x52,
    0x4c, 0x36, 0x02, 0xe7, 0xa0, 0xc4, 0xc8, 0x9e,
    0xea, 0xbf, 0x8a, 0xd2, 0x40, 0xc7, 0x38, 0xb5,
    0xa3, 0xf7, 0xf2, 0xce, 0xf9, 0x61, 0x15, 0xa1,
    0xe0, 0xae, 0x5d, 0xa4, 0x9b, 0x34, 0x1a, 0x55,
    0xad, 0x93, 0x32, 0x30, 0xf5, 0x8c, 0xb1, 0xe3,
    0x1d, 0xf6, 0xe2, 0x2e, 0x82, 0x66, 0xca, 0x60,
    0xc0, 0x29, 0x23, 0xab, 0x0d, 0x53, 0x4e, 0x6f,
    0xd5, 0xdb, 0x37, 0x45, 0xde, 0xfd, 0x8e, 0x2f,
    0x03, 0xff, 0x6a, 0x72, 0x6d, 0x6c, 0x5b, 0x51,
    0x8d, 0x1b, 0xaf, 0x92, 0xbb, 0xdd, 0xbc, 0x7f,
    0x11, 0xd9, 0x5c, 0x41, 0x1f, 0x10, 0x5a, 0xd8,
    0x0a, 0xc1, 0x31, 0x88, 0xa5, 0xcd, 0x7b, 0xbd,
    0x2d, 0x74, 0xd0, 0x12, 0xb8, 0xe5, 0xb4, 0xb0,
    0x89, 0x69, 0x97, 0x4a, 0x0c, 0x96, 0x77, 0x7e,
    0x65, 0xb9, 0xf1, 0x09, 0xc5, 0x6e, 0xc6, 0x84,
    0x18, 0xf0, 0x7d, 0xec, 0x3a, 0xdc, 0x4d, 0x20,
    0x79, 0xee, 0x5f, 0x3e, 0xd7, 0xcb, 0x39, 0x48,
])

SM4_CK = [
    0x00070e15, 0x1c232a31, 0x383f464d, 0x545b6269,
    0x70777e85, 0x8c939aa1, 0xa8afb6bd, 0xc4cbd2d9,
    0xe0e7eef5, 0xfc030a11, 0x181f262d, 0x343b4249,
    0x50575e65, 0x6c737a81, 0x888f969d, 0xa4abb2b9,
    0xc0c7ced5, 0xdce3eaf1, 0xf8ff060d, 0x141b2229,
    0x30373e45, 0x4c535a61, 0x686f767d, 0x848b9299,
    0xa0a7aeb5, 0xbcc3cad1, 0xd8dfe6ed, 0xf4fb0209,
    0x10171e25, 0x2c333a41, 0x484f565d, 0x646b7279,
]

SM4_FK = [0xa3b1bac6, 0x56aa3350, 0x677d9197, 0xb27022dc]

MASK32 = 0xFFFFFFFF


def _rotl32(x: int, n: int) -> int:
    return ((x << n) | (x >> (32 - n))) & MASK32


def _sm4_sbox_word(x: int) -> int:
    """Apply S-box substitution to each byte of a 32-bit word."""
    return (
        (SM4_SBOX[(x >> 24) & 0xFF] << 24)
        | (SM4_SBOX[(x >> 16) & 0xFF] << 16)
        | (SM4_SBOX[(x >> 8) & 0xFF] << 8)
        | SM4_SBOX[x & 0xFF]
    )


def _sm4_l(x: int) -> int:
    """Linear transformation L."""
    return x ^ _rotl32(x, 2) ^ _rotl32(x, 10) ^ _rotl32(x, 18) ^ _rotl32(x, 24)


def _sm4_l_prime(x: int) -> int:
    """Linear transformation L' (used in key expansion)."""
    return x ^ _rotl32(x, 13) ^ _rotl32(x, 23)


def _sm4_t(x: int) -> int:
    """Compound transformation T = L . tau."""
    return _sm4_l(_sm4_sbox_word(x))


def _sm4_t_prime(x: int) -> int:
    """Compound transformation T' = L' . tau (key expansion)."""
    return _sm4_l_prime(_sm4_sbox_word(x))


def sm4_key_expand(key: bytes) -> list[int]:
    """Expand a 16-byte key into 32 round keys."""
    assert len(key) == 16
    mk = [struct.unpack(">I", key[i:i + 4])[0] for i in range(0, 16, 4)]
    k = [mk[i] ^ SM4_FK[i] for i in range(4)]

    rk = []
    for i in range(32):
        val = (k[i] ^ _sm4_t_prime(k[i + 1] ^ k[i + 2] ^ k[i + 3] ^ SM4_CK[i])) & MASK32
        k.append(val)
        rk.append(val)
    return rk


def sm4_encrypt_block(rk: list[int], block: bytes) -> bytes:
    """Encrypt a single 16-byte block with SM4."""
    assert len(block) == 16
    x = [struct.unpack(">I", block[i:i + 4])[0] for i in range(0, 16, 4)]

    for i in range(32):
        tmp = (x[i + 1] ^ x[i + 2] ^ x[i + 3] ^ rk[i]) & MASK32
        x.append((x[i] ^ _sm4_t(tmp)) & MASK32)

    out = struct.pack(">IIII", x[35], x[34], x[33], x[32])
    return out


def sm4_ctr_decrypt(key: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
    """Decrypt data using SM4 in CTR mode.

    Args:
        key: 16-byte SM4 key
        nonce: 12-byte nonce (counter uses last 4 bytes as big-endian u32)
        ciphertext: data to decrypt
    """
    rk = sm4_key_expand(key)
    plaintext = bytearray()
    block_count = (len(ciphertext) + 15) // 16

    for i in range(block_count):
        counter_block = nonce[:12] + struct.pack(">I", i + 1)
        keystream = sm4_encrypt_block(rk, counter_block)

        start = i * 16
        end = min(start + 16, len(ciphertext))
        for j in range(end - start):
            plaintext.append(ciphertext[start + j] ^ keystream[j])

    return bytes(plaintext)


# ---------------------------------------------------------------------------
# ROFL2 file format structures
# ---------------------------------------------------------------------------

@dataclass
class ROFL2Header:
    magic: bytes            # b'RIOT'
    version: tuple          # (major, minor)
    file_hash: bytes        # 8 bytes
    version_str_len: int    # length of game version string
    game_version: str       # e.g. "16.7.760.9485"
    header_size: int        # total header size before chunk data


@dataclass
class ChunkHeader:
    chunk_id: int           # u32
    chunk_type: int         # u8
    chunk_id_2: int         # u32
    uncompressed_len: int   # u32
    compressed_len: int     # u32


@dataclass
class Block:
    timestamp: float        # seconds
    packet_id: int          # u16
    param: int              # u32 (network entity ID)
    payload: bytes          # raw packet data
    chunk_index: int = 0    # which chunk this came from


@dataclass
class ReplayMetadata:
    game_length_ms: int
    last_game_chunk_id: int
    last_keyframe_id: int
    players: list


# ---------------------------------------------------------------------------
# ROFL2 Parser
# ---------------------------------------------------------------------------

class ROFL2Parser:
    """Parser for the ROFL2 replay file format."""

    def __init__(self, filepath: str):
        with open(filepath, "rb") as f:
            self.data = f.read()

        self.filepath = filepath
        self.game_id = self._extract_game_id_from_filename()
        self.header = self._parse_header()
        self.metadata = self._parse_metadata()
        self._dctx = zstandard.ZstdDecompressor()

    def _extract_game_id_from_filename(self) -> str:
        """Extract game ID from filename like EUW1-7816865419.rofl."""
        basename = os.path.splitext(os.path.basename(self.filepath))[0]
        parts = basename.split("-")
        if len(parts) >= 2:
            return parts[-1]
        return basename

    def _parse_header(self) -> ROFL2Header:
        magic = self.data[0:4]
        if magic != b"RIOT":
            raise ValueError(f"Invalid magic: {magic!r}, expected b'RIOT'")

        version = (self.data[4], self.data[5])
        if version[0] != 2:
            raise ValueError(
                f"Expected ROFL2 (version 2.x), got version {version[0]}.{version[1]}"
            )

        file_hash = self.data[6:14]

        # Header layout:
        #   0x00-0x03: RIOT magic (4 bytes)
        #   0x04-0x05: format version (2 bytes)
        #   0x06-0x0D: file hash (8 bytes)
        #   0x0E:      game version string length (u8)
        #   0x0F:      game version string start
        # Chunk data begins immediately after the version string.
        version_str_len = self.data[0x0E]
        game_version = self.data[0x0F : 0x0F + version_str_len].decode("ascii")
        header_size = 0x0F + version_str_len

        return ROFL2Header(
            magic=magic,
            version=version,
            file_hash=file_hash,
            version_str_len=version_str_len,
            game_version=game_version,
            header_size=header_size,
        )

    def _parse_metadata(self) -> ReplayMetadata:
        """Parse JSON metadata from the end of the file."""
        meta_len = struct.unpack_from("<I", self.data, len(self.data) - 4)[0]
        meta_json = self.data[len(self.data) - 4 - meta_len : len(self.data) - 4]
        meta = json.loads(meta_json)

        players = []
        try:
            stats = json.loads(meta["statsJson"])
            positions = ["Top", "Jungle", "Mid", "Adc", "Support"]
            for i, p in enumerate(stats):
                players.append({
                    "name": p.get("NAME", ""),
                    "skin": p.get("SKIN", ""),
                    "team": "Blue" if p.get("TEAM") == "100" else "Red",
                    "position": positions[i % 5],
                })
        except (KeyError, json.JSONDecodeError):
            pass

        return ReplayMetadata(
            game_length_ms=meta.get("gameLength", 0),
            last_game_chunk_id=meta.get("lastGameChunkId", 0),
            last_keyframe_id=meta.get("lastKeyFrameId", 0),
            players=players,
        )

    @property
    def _payload_end(self) -> int:
        """End offset of chunk payload data (before signature + metadata)."""
        meta_len = struct.unpack_from("<I", self.data, len(self.data) - 4)[0]
        return len(self.data) - meta_len - 4 - 0x100  # 0x100 = signature

    def iter_chunks(self) -> Iterator[tuple[ChunkHeader, Optional[bytes]]]:
        """Iterate over chunks, yielding (header, decompressed_payload)."""
        off = self.header.header_size
        end = self._payload_end

        while off + 17 <= end:
            ch = ChunkHeader(
                chunk_id=struct.unpack_from("<I", self.data, off)[0],
                chunk_type=self.data[off + 4],
                chunk_id_2=struct.unpack_from("<I", self.data, off + 5)[0],
                uncompressed_len=struct.unpack_from("<I", self.data, off + 9)[0],
                compressed_len=struct.unpack_from("<I", self.data, off + 13)[0],
            )
            payload_off = off + 17

            if ch.compressed_len > 0:
                compressed = self.data[payload_off : payload_off + ch.compressed_len]
                try:
                    decompressed = self._dctx.decompress(
                        compressed,
                        max_output_size=ch.uncompressed_len + 4096,
                    )
                except zstandard.ZstdError:
                    decompressed = None
                off = payload_off + ch.compressed_len
            else:
                decompressed = None
                off = payload_off + ch.uncompressed_len

            yield ch, decompressed

    def iter_blocks(
        self,
        *,
        skip_type_2: bool = True,
    ) -> Iterator[Block]:
        """Iterate over all blocks in the replay.

        Args:
            skip_type_2: if True, skip chunk type 2 (initial state / keyframe base).
        """
        chunk_idx = 0
        for ch, payload in self.iter_chunks():
            if payload is None:
                chunk_idx += 1
                continue
            if skip_type_2 and ch.chunk_type == 2:
                chunk_idx += 1
                continue

            yield from self._parse_blocks(payload, chunk_idx)
            chunk_idx += 1

    @staticmethod
    def _parse_blocks(data: bytes, chunk_index: int) -> Iterator[Block]:
        """Parse block structures from decompressed chunk data.

        Block format (variable length):
            marker: u8
                bit 7 (0x80): relative timestamp (1 byte delta) vs absolute (f32)
                bit 6 (0x40): reuse previous packet_id vs read u16
                bit 5 (0x20): relative param (u8 delta) vs absolute u32
                bit 4 (0x10): u8 block length vs u32 block length
            timestamp: f32 absolute OR u8 relative (*0.001 seconds)
            length: u8 or u32
            packet_id: u16 (if not reusing previous)
            param: u32 absolute or u8 relative
            payload: <length> bytes
        """
        pos = 0
        acc_time = 0.0
        prev_pid = 0
        prev_param = 0
        size = len(data)

        while pos < size:
            marker = data[pos]
            pos += 1

            # Timestamp
            if marker & 0x80:
                if pos >= size:
                    break
                acc_time += data[pos] * 0.001
                pos += 1
            else:
                if pos + 4 > size:
                    break
                acc_time = struct.unpack_from("<f", data, pos)[0]
                pos += 4

            # Block length
            if marker & 0x10:
                if pos >= size:
                    break
                blen = data[pos]
                pos += 1
            else:
                if pos + 4 > size:
                    break
                blen = struct.unpack_from("<I", data, pos)[0]
                pos += 4

            # Packet ID
            if marker & 0x40:
                pid = prev_pid
            else:
                if pos + 2 > size:
                    break
                pid = struct.unpack_from("<H", data, pos)[0]
                pos += 2

            # Param (network entity ID)
            if marker & 0x20:
                if pos >= size:
                    break
                param = data[pos] + prev_param
                pos += 1
            else:
                if pos + 4 > size:
                    break
                param = struct.unpack_from("<I", data, pos)[0]
                pos += 4

            payload = data[pos : pos + blen]
            pos += blen

            prev_pid = pid
            prev_param = param

            yield Block(
                timestamp=acc_time,
                packet_id=pid,
                param=param,
                payload=payload,
                chunk_index=chunk_index,
            )


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------

def calc_entropy(data: bytes) -> float:
    """Calculate Shannon entropy (bits per byte, max 8.0)."""
    if not data:
        return 0.0
    counts = Counter(data)
    total = len(data)
    return -sum(
        (c / total) * math.log2(c / total) for c in counts.values() if c > 0
    )


def sm4_test():
    """Run SM4 self-test with known test vector from the SM4 specification."""
    # SM4 spec test vector
    key = bytes.fromhex("0123456789ABCDEFFEDCBA9876543210")
    plaintext = bytes.fromhex("0123456789ABCDEFFEDCBA9876543210")
    expected = bytes.fromhex("681EDF34D206965E86B3E94F536E4246")

    rk = sm4_key_expand(key)
    ciphertext = sm4_encrypt_block(rk, plaintext)

    print("SM4 self-test:")
    print(f"  Key:        {key.hex()}")
    print(f"  Plaintext:  {plaintext.hex()}")
    print(f"  Ciphertext: {ciphertext.hex()}")
    print(f"  Expected:   {expected.hex()}")
    print(f"  Result:     {'PASS' if ciphertext == expected else 'FAIL'}")

    # CTR mode test: encrypt then decrypt should give original
    nonce = bytes(12)
    ct = sm4_ctr_decrypt(key, nonce, plaintext)
    pt_back = sm4_ctr_decrypt(key, nonce, ct)
    print(f"\n  CTR roundtrip: {'PASS' if pt_back == plaintext else 'FAIL'}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="ROFL2 Replay Decoder for League of Legends"
    )
    parser.add_argument("rofl_file", nargs="?", help="Path to .rofl replay file")
    parser.add_argument(
        "--dump-blocks",
        action="store_true",
        help="Print all blocks with hex payload preview",
    )
    parser.add_argument(
        "--packet-id",
        type=lambda x: int(x, 0),
        help="Filter blocks by packet ID (decimal or 0x hex)",
    )
    parser.add_argument(
        "--sm4-test",
        action="store_true",
        help="Run SM4 cipher self-test and exit",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print per-packet-ID statistics (count, sizes, entropy)",
    )
    parser.add_argument(
        "--max-blocks",
        type=int,
        default=0,
        help="Maximum number of blocks to process (0 = all)",
    )
    parser.add_argument(
        "--sm4-try",
        action="store_true",
        help="Attempt SM4-CTR decryption on high-entropy packet payloads",
    )
    parser.add_argument(
        "--export-json",
        type=str,
        metavar="PATH",
        help="Export blocks as JSON to the given file path",
    )
    args = parser.parse_args()

    if args.sm4_test:
        sm4_test()
        return

    if not args.rofl_file:
        parser.error("rofl_file is required (unless using --sm4-test)")

    # Parse the replay
    print(f"Parsing {args.rofl_file} ...")
    rp = ROFL2Parser(args.rofl_file)

    print(f"\n--- ROFL2 Header ---")
    print(f"  Magic:        {rp.header.magic.decode()}")
    print(f"  Version:      {rp.header.version[0]}.{rp.header.version[1]}")
    print(f"  Game version: {rp.header.game_version}")
    print(f"  File hash:    {rp.header.file_hash.hex()}")
    print(f"  Header size:  0x{rp.header.header_size:x} ({rp.header.header_size} bytes)")
    print(f"  Game ID:      {rp.game_id}")

    print(f"\n--- Metadata ---")
    game_len_s = rp.metadata.game_length_ms / 1000
    print(f"  Game length:    {game_len_s:.1f}s ({game_len_s/60:.1f} min)")
    print(f"  Last chunk ID:  {rp.metadata.last_game_chunk_id}")
    print(f"  Last keyframe:  {rp.metadata.last_keyframe_id}")
    if rp.metadata.players:
        print(f"  Players ({len(rp.metadata.players)}):")
        for p in rp.metadata.players:
            print(f"    {p['team']:4s} {p['position']:7s} {p['skin']}")

    # Count chunks
    chunk_count = 0
    chunk_type_counts: dict[int, int] = {}
    for ch, _ in rp.iter_chunks():
        chunk_count += 1
        chunk_type_counts[ch.chunk_type] = chunk_type_counts.get(ch.chunk_type, 0) + 1

    print(f"\n--- Chunks ---")
    print(f"  Total: {chunk_count}")
    for ct in sorted(chunk_type_counts):
        print(f"    type {ct}: {chunk_type_counts[ct]} chunks")

    # Parse blocks
    print(f"\n--- Blocks ---")
    pid_stats: dict[int, dict] = {}
    block_count = 0
    printed = 0

    for block in rp.iter_blocks():
        block_count += 1

        if args.max_blocks and block_count > args.max_blocks:
            break

        # Collect stats
        if block.packet_id not in pid_stats:
            pid_stats[block.packet_id] = {
                "count": 0,
                "total_bytes": 0,
                "sizes": [],
                "sample_payloads": [],
            }
        s = pid_stats[block.packet_id]
        s["count"] += 1
        s["total_bytes"] += len(block.payload)
        if len(s["sizes"]) < 100:
            s["sizes"].append(len(block.payload))
        if len(s["sample_payloads"]) < 5:
            s["sample_payloads"].append(block.payload)

        # Dump blocks if requested
        if args.dump_blocks:
            if args.packet_id is not None and block.packet_id != args.packet_id:
                continue
            if printed < 500:
                hex_preview = block.payload[:48].hex()
                print(
                    f"  [{block.chunk_index:3d}] t={block.timestamp:8.3f}s "
                    f"pkt=0x{block.packet_id:04x} "
                    f"param=0x{block.param:08x} "
                    f"len={len(block.payload):5d} "
                    f"{hex_preview}"
                )
                printed += 1

    print(f"  Total blocks: {block_count}")
    print(f"  Unique packet IDs: {len(pid_stats)}")

    if args.stats:
        print(f"\n--- Packet ID Statistics ---")
        print(
            f"  {'PktID':>8s}  {'Count':>7s}  {'TotalBytes':>10s}  "
            f"{'MinLen':>6s}  {'MaxLen':>6s}  {'Entropy':>7s}"
        )
        for pid in sorted(pid_stats):
            s = pid_stats[pid]
            combined = b"".join(s["sample_payloads"])
            ent = calc_entropy(combined)
            min_sz = min(s["sizes"]) if s["sizes"] else 0
            max_sz = max(s["sizes"]) if s["sizes"] else 0
            marker = " <-- high entropy" if ent > 7.0 and s["total_bytes"] > 1000 else ""
            print(
                f"  0x{pid:04x}  {s['count']:7d}  {s['total_bytes']:10d}  "
                f"{min_sz:6d}  {max_sz:6d}  {ent:7.2f}{marker}"
            )

    # Export to JSON
    if args.export_json:
        print(f"\n--- Exporting to {args.export_json} ---")
        export_data = {
            "game_id": rp.game_id,
            "game_version": rp.header.game_version,
            "game_length_ms": rp.metadata.game_length_ms,
            "players": rp.metadata.players,
            "packet_stats": {
                f"0x{pid:04x}": {
                    "count": s["count"],
                    "total_bytes": s["total_bytes"],
                }
                for pid, s in pid_stats.items()
            },
            "total_blocks": block_count,
            "total_chunks": chunk_count,
        }
        with open(args.export_json, "w") as f:
            json.dump(export_data, f, indent=2)
        print(f"  Written {os.path.getsize(args.export_json)} bytes")

    # SM4 trial decryption on high-entropy packets
    if args.sm4_try:
        print(f"\n--- SM4-CTR Trial Decryption ---")
        print("Trying SM4-CTR decryption with game_id as key on high-entropy packets...")

        # Derive a 16-byte key from game ID using SM3 or simple methods
        game_id_bytes = rp.game_id.encode("ascii")

        # Method 1: pad/truncate game_id to 16 bytes
        key_padded = (game_id_bytes * 2)[:16]

        # Method 2: MD5-like hash of game_id (using SM4 in Davies-Meyer mode)
        import hashlib
        key_md5 = hashlib.md5(game_id_bytes).digest()

        # Method 3: SHA256 truncated
        key_sha = hashlib.sha256(game_id_bytes).digest()[:16]

        keys_to_try = [
            ("game_id_padded", key_padded),
            ("md5(game_id)", key_md5),
            ("sha256(game_id)[:16]", key_sha),
            ("all_zeros", bytes(16)),
        ]

        high_entropy_pids = [
            pid
            for pid, s in pid_stats.items()
            if calc_entropy(b"".join(s["sample_payloads"])) > 7.0
            and s["total_bytes"] > 1000
        ]

        for pid in sorted(high_entropy_pids)[:5]:
            samples = pid_stats[pid]["sample_payloads"][:2]
            print(f"\n  Packet 0x{pid:04x} (entropy > 7.0):")
            for sample in samples:
                if len(sample) < 16:
                    continue
                print(f"    Original ({len(sample)} bytes): {sample[:32].hex()}...")
                for key_name, key in keys_to_try:
                    # Try different nonce strategies
                    for nonce_desc, nonce in [
                        ("zeros", bytes(12)),
                        ("from_payload[:12]", sample[:12]),
                    ]:
                        pt = sm4_ctr_decrypt(key, nonce, sample)
                        pt_ent = calc_entropy(pt)
                        if pt_ent < 6.0:  # significantly reduced entropy
                            print(
                                f"    ** {key_name}, nonce={nonce_desc}: "
                                f"entropy {pt_ent:.2f} -> {pt[:32].hex()}"
                            )
                break  # one sample per pid is enough for trial


if __name__ == "__main__":
    main()
