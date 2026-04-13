#!/usr/bin/env python3
"""
ROFL2 Parser - League of Legends replay file parser
Extracts block/packet data from .rofl files (ROFL2 format, patch 14.11+)

Based on reverse engineering of the format and Mowokuma/ROFL project.
Block payloads are obfuscated per-patch and need runtime decryption.
"""

import struct
import sys
import json
import zstandard
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class RoflHeader:
    magic: bytes
    version: int
    signature_field: bytes  # 2 bytes, per-patch
    patch_key: bytes        # 8 bytes, per-patch
    game_version: str


@dataclass
class Chunk:
    id: int
    type: int       # 1=keyframe, 2=game_chunk
    id2: int
    uncompressed_len: int
    compressed_len: int
    data: Optional[bytes] = None


@dataclass
class Block:
    timestamp: float
    packet_id: int
    param: int
    payload: bytes
    length: int = 0


@dataclass
class RoflMetadata:
    game_length: int
    last_game_chunk_id: int
    last_key_frame_id: int
    players: List[dict] = field(default_factory=list)


def parse_header(data: bytes) -> Tuple[RoflHeader, int]:
    """Parse ROFL2 file header, return header and offset to chunk data."""
    magic = data[0:4]
    assert magic == b'RIOT', f"Invalid magic: {magic}"

    version = struct.unpack_from('<H', data, 4)[0]
    assert version == 2, f"Expected ROFL2, got version {version}"

    sig_field = data[6:8]
    patch_key = data[8:16]

    # Game version string at 0x10, null-terminated
    ver_end = data.index(0, 0x10)
    game_version = data[0x10:ver_end].decode('ascii')

    header = RoflHeader(
        magic=magic,
        version=version,
        signature_field=sig_field,
        patch_key=patch_key,
        game_version=game_version,
    )

    # Skip ROFL header (same logic as Mowokuma)
    # After the first 0x10 bytes, skip version string + padding
    offset = 0x10
    # Skip past the null-terminated string + padding to chunk headers
    # The pattern: version string, then zeros, then u16=2, u16=0, u16=3, u16=0
    # then two per-game u32s, then first zstd frame
    offset = ver_end + 1  # skip null terminator
    while offset < len(data) and data[offset] == 0:
        offset += 1
    # Now at the start of header fields (u16=2, ...)
    # Skip: u32(0x00020000) + u32(0x00000003) + u32(per-game) + u32(per-game) = 16 bytes
    # But actually the zstd magic is right after
    # Find the first zstd frame
    zstd_magic = b'\x28\xb5\x2f\xfd'
    chunk_start = data.find(zstd_magic)

    return header, chunk_start


def parse_metadata(data: bytes) -> RoflMetadata:
    """Parse JSON metadata from end of ROFL file."""
    meta_len = struct.unpack_from('<I', data, len(data) - 4)[0]
    meta_start = len(data) - 4 - meta_len
    meta_json = json.loads(data[meta_start:meta_start + meta_len])

    players = []
    if 'statsJson' in meta_json:
        stats = json.loads(meta_json['statsJson'])
        for p in stats:
            players.append({
                'champion': p.get('SKIN', ''),
                'team': p.get('TEAM', 0),
                'win': p.get('WIN', ''),
            })

    return RoflMetadata(
        game_length=meta_json.get('gameLength', 0),
        last_game_chunk_id=meta_json.get('lastGameChunkId', 0),
        last_key_frame_id=meta_json.get('lastKeyFrameId', 0),
        players=players,
    )


def parse_chunks(data: bytes, chunk_start: int, payload_end: int) -> List[Chunk]:
    """Parse chunk headers and decompress zstd payloads.

    Uses Mowokuma's approach: strip metadata + 0x100 signature from end,
    strip ROFL header from start, then parse sequential chunk headers.
    """
    dctx = zstandard.ZstdDecompressor()
    chunks = []

    # Strip metadata + signature
    meta_len = struct.unpack_from('<I', data, len(data) - 4)[0]
    stripped = bytearray(data[:len(data) - meta_len - 4])
    stripped = stripped[:len(stripped) - 0x100]  # remove 256-byte signature

    # Skip ROFL header (Mowokuma: drain 0x10, then 0xC or 0xD based on byte)
    stripped = stripped[0x10:]
    if stripped[0xC] == 1:
        stripped = stripped[0xC:]
    else:
        stripped = stripped[0xD:]

    # Parse sequential chunks: [u32 id][u8 type][u32 id2][u32 uncomp][u32 comp][data]
    pos = 0
    while pos < len(stripped) - 17:
        chunk_id = struct.unpack_from('<I', stripped, pos)[0]
        chunk_type = stripped[pos + 4]
        chunk_id2 = struct.unpack_from('<I', stripped, pos + 5)[0]
        uncomp_len = struct.unpack_from('<I', stripped, pos + 9)[0]
        comp_len = struct.unpack_from('<I', stripped, pos + 13)[0]
        pos += 17

        chunk = Chunk(
            id=chunk_id,
            type=chunk_type,
            id2=chunk_id2,
            uncompressed_len=uncomp_len,
            compressed_len=comp_len,
        )

        if comp_len > 0 and pos + comp_len <= len(stripped):
            compressed = bytes(stripped[pos:pos + comp_len])
            pos += comp_len
            try:
                chunk.data = dctx.decompress(compressed, max_output_size=20 * 1024 * 1024)
            except Exception:
                chunk.data = None
        else:
            pos += uncomp_len

        chunks.append(chunk)

    return chunks


def parse_blocks(chunk_data: bytes) -> List[Block]:
    """Parse blocks (packets) from a decompressed chunk using the marker-based format."""
    blocks = []
    pos = 0
    acc_time = 0.0
    prev_packet_id = 0
    prev_param = 0

    while pos < len(chunk_data):
        marker = chunk_data[pos]
        pos += 1

        # Timestamp
        if marker & 0x80:
            if pos >= len(chunk_data):
                break
            acc_time += chunk_data[pos] * 0.001
            pos += 1
        else:
            if pos + 4 > len(chunk_data):
                break
            acc_time = struct.unpack_from('<f', chunk_data, pos)[0]
            pos += 4

        # Block length
        if marker & 0x10:
            if pos >= len(chunk_data):
                break
            block_len = chunk_data[pos]
            pos += 1
        else:
            if pos + 4 > len(chunk_data):
                break
            block_len = struct.unpack_from('<I', chunk_data, pos)[0]
            pos += 4

        # Packet ID
        if marker & 0x40:
            packet_id = prev_packet_id
        else:
            if pos + 2 > len(chunk_data):
                break
            packet_id = struct.unpack_from('<H', chunk_data, pos)[0]
            pos += 2

        # Param
        if marker & 0x20:
            if pos >= len(chunk_data):
                break
            param = chunk_data[pos] + prev_param
            pos += 1
        else:
            if pos + 4 > len(chunk_data):
                break
            param = struct.unpack_from('<I', chunk_data, pos)[0]
            pos += 4

        # Payload
        payload = b''
        if block_len > 0 and pos + block_len <= len(chunk_data):
            payload = chunk_data[pos:pos + block_len]
        pos += block_len

        prev_packet_id = packet_id
        prev_param = param

        blocks.append(Block(
            timestamp=acc_time,
            packet_id=packet_id,
            param=param,
            payload=payload,
            length=block_len,
        ))

    return blocks


def parse_rofl(filepath: str) -> dict:
    """Parse a ROFL2 file and return structured data."""
    with open(filepath, 'rb') as f:
        data = f.read()

    header, chunk_start = parse_header(data)
    metadata = parse_metadata(data)

    # Parse chunks
    meta_len = struct.unpack_from('<I', data, len(data) - 4)[0]
    payload_end = len(data) - 4 - meta_len - 0x100
    chunks = parse_chunks(data, chunk_start, payload_end)

    # Parse blocks from keyframe chunks (type != 2)
    all_blocks = []
    for chunk in chunks:
        if chunk.data and chunk.type != 2:
            blocks = parse_blocks(chunk.data)
            all_blocks.extend(blocks)

    # Statistics
    pkt_counts = Counter(b.packet_id for b in all_blocks)

    return {
        'header': {
            'version': header.version,
            'game_version': header.game_version,
            'patch_key': header.patch_key.hex(),
        },
        'metadata': {
            'game_length': metadata.game_length,
            'last_game_chunk_id': metadata.last_game_chunk_id,
            'last_key_frame_id': metadata.last_key_frame_id,
            'players': metadata.players,
        },
        'stats': {
            'chunks': len(chunks),
            'keyframes': sum(1 for c in chunks if c.type == 1),
            'game_chunks': sum(1 for c in chunks if c.type == 2),
            'total_blocks': len(all_blocks),
            'movement_blocks': pkt_counts.get(0x001c, 0),
            'top_packet_ids': {f'0x{pid:04x}': count for pid, count in pkt_counts.most_common(10)},
        },
        'blocks': all_blocks,
    }


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 rofl2_parser.py <file.rofl> [--json] [--stats]")
        sys.exit(1)

    filepath = sys.argv[1]
    flags = sys.argv[2:]

    result = parse_rofl(filepath)

    if '--stats' in flags or '--json' not in flags:
        print(f"Game version: {result['header']['game_version']}")
        print(f"Players:")
        for i, p in enumerate(result['metadata']['players']):
            team = 'Blue' if p['team'] == 100 else 'Red'
            win = 'W' if p['win'] == 'Win' else 'L'
            eid = hex(0x400000ae + i)
            print(f"  [{team}] {p['champion']:12s} ({win}) entity={eid}")
        print(f"\nChunks: {result['stats']['chunks']} ({result['stats']['keyframes']} keyframes, {result['stats']['game_chunks']} game chunks)")
        print(f"Blocks: {result['stats']['total_blocks']}")
        print(f"Movement packets (0x001c): {result['stats']['movement_blocks']}")
        print(f"\nTop packet types:")
        for pid, count in result['stats']['top_packet_ids'].items():
            print(f"  {pid}: {count:>8d}")

    if '--json' in flags:
        # Serialize blocks without payload (too large)
        output = {
            'header': result['header'],
            'metadata': result['metadata'],
            'stats': result['stats'],
        }
        print(json.dumps(output, indent=2))
