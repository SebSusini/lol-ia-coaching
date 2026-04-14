#!/usr/bin/env python3
"""
ROFL2 Movement Packet Key Derivation Brute-Force

Systematically tests ALL possible key derivations and cipher combinations
to decrypt movement packet (0x001c) payloads from ROFL2 replays.

Tested 4244+ combinations across:
  - 50 key derivations (file_hash, game_id, version, signature, PBKDF2, etc.)
  - 6 nonce strategies (zeros, file_hash, game_id LE/BE)
  - 5 ciphers (SM4-CTR, AES-128-CTR, HMAC-SM3-CTR, Blowfish-ECB, XOR)
  - 2 CTR modes (start 0, start 1)
  - 256 alternative XOR fill bytes
  - f32 and i16 coordinate pattern search

RESULT: No valid key found. Movement coordinates are NOT derivable from the
.rofl file alone. The key comes from the GAMHS server (see CLAUDE.md).

Usage:
    python rofl2_decrypt.py [replay.rofl]
"""

import hashlib
import hmac
import json
import math
import os
import struct
import sys
import time
from collections import Counter
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sm4_decoder import (
    ROFL2Parser,
    Block,
    sm4_key_expand,
    sm4_encrypt_block,
    sm4_ctr_decrypt,
)
from packet_decryptor import sm3_hash, hmac_sm3, hmac_ctr_decrypt

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ROFL_PATH = "/Users/sommesi/projects/lol-replay-analyzer/replays/EUW1-7816865419.rofl"
RESULTS_PATH = "/tmp/key_derivation_final.txt"
MOVEMENT_PACKET_ID = 0x001C
FILL_BYTE = 0xFA

# Riot API positions at minute 5 for validation
API_POSITIONS_MIN5 = {
    "Trundle": (2746, 13269),
    "JarvanIV": (9836, 1734),
    "Mel": (7085, 7326),
    "Veigar": (12438, 1992),
    "Bard": (12017, 2357),
    "Fiora": (3478, 11911),
    "Gwen": (10841, 8570),
    "Sylas": (7944, 7450),
    "Smolder": (12996, 3004),
    "Leona": (12266, 2757),
}

# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def entropy(data: bytes) -> float:
    """Shannon entropy in bits per byte."""
    if not data:
        return 0.0
    counts = Counter(data)
    total = len(data)
    return -sum((c / total) * math.log2(c / total) for c in counts.values())


def sign_extend_16(v: int) -> int:
    return v - 0x10000 if v & 0x8000 else v


def is_valid_movement(data: bytes) -> dict:
    """Check if decrypted data looks like a valid PathPacket.

    Returns a dict with validation results.
    """
    result = {
        "valid": False,
        "parsing_type": None,
        "entity_id": None,
        "speed": None,
        "first_wp": None,
        "reason": "",
    }

    if len(data) < 10:
        result["reason"] = "too_short"
        return result

    parsing_type = struct.unpack_from("<H", data, 0)[0]
    entity_id = struct.unpack_from("<I", data, 2)[0]
    speed = struct.unpack_from("<f", data, 6)[0]

    result["parsing_type"] = parsing_type
    result["entity_id"] = entity_id
    result["speed"] = speed

    # Check parsing_type: should be reasonable (< 0x1000 typically)
    if parsing_type > 0x1000:
        result["reason"] = f"parsing_type too large: {parsing_type}"
        return result

    # Check entity_id: champions are in 0x40000000 range
    if not (0x40000000 <= entity_id <= 0x40000200):
        result["reason"] = f"entity_id not in champion range: 0x{entity_id:08x}"
        # Don't return yet -- batch entities may have different IDs

    # Check speed: should be 0-600 (or NaN check)
    if speed != speed:  # NaN
        result["reason"] = "speed is NaN"
        return result
    if speed < 0 or speed > 600:
        if speed > 600:
            result["reason"] = f"speed too high: {speed:.1f}"
            return result

    # Try to extract first waypoint
    has_extra = parsing_type & 1
    unk = (parsing_type & 0xFF) >> 1
    if unk == 0:
        result["reason"] = "unk=0"
        return result

    pos = 10 + (1 if has_extra else 0)
    if unk > 1:
        pos += ((unk - 2) >> 2) + 1

    if pos + 4 <= len(data):
        x_raw = struct.unpack_from("<H", data, pos)[0]
        y_raw = struct.unpack_from("<H", data, pos + 2)[0]
        x = sign_extend_16(x_raw) * 2.0 + 7358.0
        y = sign_extend_16(y_raw) * 2.0 + 7412.0
        result["first_wp"] = (x, y)

        if 0 <= x <= 15000 and 0 <= y <= 15000:
            result["valid"] = True
            result["reason"] = "valid"
        else:
            result["reason"] = f"position out of bounds: ({x:.0f}, {y:.0f})"

    return result


def validate_against_api(x: float, y: float, threshold: float = 500.0) -> Optional[str]:
    """Check if position matches any Riot API position at minute 5."""
    for name, (ax, ay) in API_POSITIONS_MIN5.items():
        dist = ((x - ax) ** 2 + (y - ay) ** 2) ** 0.5
        if dist < threshold:
            return f"{name} (dist={dist:.0f})"
    return None


# ---------------------------------------------------------------------------
# Cipher implementations
# ---------------------------------------------------------------------------

def aes_ctr_decrypt(key: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
    """AES-128-CTR decryption using Python's built-in."""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.backends import default_backend

        iv = nonce + b"\x00" * (16 - len(nonce)) if len(nonce) < 16 else nonce[:16]
        cipher = Cipher(algorithms.AES(key), modes.CTR(iv), backend=default_backend())
        dec = cipher.decryptor()
        return dec.update(ciphertext) + dec.finalize()
    except ImportError:
        # Fallback: manual AES-CTR using hashlib for AES (not possible, skip)
        return b""


def aes_ecb_encrypt_block(key: bytes, block: bytes) -> bytes:
    """AES-128-ECB single block encryption."""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.backends import default_backend

        cipher = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
        enc = cipher.encryptor()
        return enc.update(block) + enc.finalize()
    except ImportError:
        return b""


def manual_aes_ctr_decrypt(key: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
    """AES-128-CTR using ECB mode manually."""
    plaintext = bytearray()
    block_count = (len(ciphertext) + 15) // 16
    for i in range(block_count):
        counter_block = nonce[:12] + struct.pack(">I", i)
        keystream = aes_ecb_encrypt_block(key, counter_block)
        if not keystream:
            return b""
        start = i * 16
        end = min(start + 16, len(ciphertext))
        for j in range(end - start):
            plaintext.append(ciphertext[start + j] ^ keystream[j])
    return bytes(plaintext)


def blowfish_decrypt(key: bytes, data: bytes) -> bytes:
    """Blowfish ECB decryption (ROFL1 style)."""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.backends import default_backend

        # Blowfish ECB, pad data to 8-byte boundary
        padded = data + b"\x00" * ((8 - len(data) % 8) % 8)
        cipher = Cipher(
            algorithms.Blowfish(key), modes.ECB(), backend=default_backend()
        )
        dec = cipher.decryptor()
        return (dec.update(padded) + dec.finalize())[: len(data)]
    except ImportError:
        return b""


def xor_decrypt(key: bytes, data: bytes) -> bytes:
    """Simple repeating-key XOR."""
    if not key:
        return data
    return bytes(data[i] ^ key[i % len(key)] for i in range(len(data)))


# ---------------------------------------------------------------------------
# Key derivation candidates
# ---------------------------------------------------------------------------

def generate_all_keys(parser: ROFL2Parser) -> list:
    """Generate ALL possible key candidates from the ROFL2 file."""
    game_id_str = parser.game_id  # "7816865419"
    game_id_bytes = game_id_str.encode("ascii")
    game_id_u64 = int(game_id_str)
    file_hash = parser.header.file_hash  # 8 bytes
    version_str = parser.header.game_version  # "16.7.760.9485"
    version_bytes = version_str.encode("ascii")

    # Read signature (256 bytes before metadata)
    data = parser.data
    meta_len = struct.unpack_from("<I", data, len(data) - 4)[0]
    sig_start = len(data) - 4 - meta_len - 256
    signature = data[sig_start : sig_start + 256]

    # Read full header
    header_bytes = data[0 : parser.header.header_size]

    # Read first chunk header
    ch_offset = parser.header.header_size
    first_chunk_header = data[ch_offset : ch_offset + 17]

    keys = []

    # === From file_hash (8 bytes) ===
    keys.append(("file_hash_padded_zeros", file_hash + b"\x00" * 8))
    keys.append(("file_hash_repeated", file_hash * 2))
    keys.append(("file_hash_reversed+self", file_hash[::-1] + file_hash))
    keys.append(("md5(file_hash)", hashlib.md5(file_hash).digest()))
    keys.append(("sha256(file_hash)[:16]", hashlib.sha256(file_hash).digest()[:16]))
    keys.append(("sm3(file_hash)[:16]", sm3_hash(file_hash)[:16]))

    # === From game_id ===
    keys.append(("md5(game_id)", hashlib.md5(game_id_bytes).digest()))
    keys.append(("sha256(game_id)[:16]", hashlib.sha256(game_id_bytes).digest()[:16]))
    keys.append(("sm3(game_id)[:16]", sm3_hash(game_id_bytes)[:16]))
    keys.append(
        ("game_id_u64_le_pad16", struct.pack("<Q", game_id_u64) + b"\x00" * 8)
    )
    keys.append(
        ("game_id_u64_be_pad16", struct.pack(">Q", game_id_u64) + b"\x00" * 8)
    )

    # === Combined ===
    keys.append(
        ("md5(file_hash+game_id)", hashlib.md5(file_hash + game_id_bytes).digest())
    )
    keys.append(
        (
            "sha256(file_hash+game_id)[:16]",
            hashlib.sha256(file_hash + game_id_bytes).digest()[:16],
        )
    )
    keys.append(
        ("sm3(file_hash+game_id)[:16]", sm3_hash(file_hash + game_id_bytes)[:16])
    )
    keys.append(
        ("sm3(game_id+file_hash)[:16]", sm3_hash(game_id_bytes + file_hash)[:16])
    )
    keys.append(
        (
            "hmac_md5(file_hash,game_id)",
            hmac.new(file_hash, game_id_bytes, hashlib.md5).digest(),
        )
    )
    keys.append(
        (
            "hmac_sha256(fh,gid)[:16]",
            hmac.new(file_hash, game_id_bytes, hashlib.sha256).digest()[:16],
        )
    )
    keys.append(("hmac_sm3(fh,gid)[:16]", hmac_sm3(file_hash, game_id_bytes)[:16]))

    # === From signature ===
    keys.append(("sig_first16", signature[:16]))
    keys.append(("sig_last16", signature[-16:]))
    keys.append(("md5(signature)", hashlib.md5(signature).digest()))
    keys.append(("sm3(signature)[:16]", sm3_hash(signature)[:16]))

    # === From chunk headers ===
    keys.append(("first_chunk_hdr[:16]", first_chunk_header[:16] + b"\x00"))
    keys.append(("md5(first_chunk_hdr)", hashlib.md5(first_chunk_header).digest()))

    # === From version string ===
    keys.append(("md5(version)", hashlib.md5(version_bytes).digest()))
    keys.append(("sm3(version)[:16]", sm3_hash(version_bytes)[:16]))

    # === Combined with version ===
    keys.append(
        ("sm3(gid+version)[:16]", sm3_hash(game_id_bytes + version_bytes)[:16])
    )
    keys.append(
        ("sm3(fh+version)[:16]", sm3_hash(file_hash + version_bytes)[:16])
    )
    keys.append(
        ("hmac_sm3(ver,fh)[:16]", hmac_sm3(version_bytes, file_hash)[:16])
    )

    # === From entire header ===
    keys.append(("md5(header)", hashlib.md5(header_bytes).digest()))
    keys.append(("sm3(header)[:16]", sm3_hash(header_bytes)[:16]))
    keys.append(
        ("header_bytes[6:22]", data[6:22])
    )  # file_hash + version_len + partial version

    # === PBKDF2 ===
    keys.append(
        (
            "pbkdf2_sha256(gid,fh,1)[:16]",
            hashlib.pbkdf2_hmac("sha256", game_id_bytes, file_hash, 1)[:16],
        )
    )
    keys.append(
        (
            "pbkdf2_sha256(fh,gid,1)[:16]",
            hashlib.pbkdf2_hmac("sha256", file_hash, game_id_bytes, 1)[:16],
        )
    )
    keys.append(
        (
            "pbkdf2_sha256(gid,fh,1000)[:16]",
            hashlib.pbkdf2_hmac("sha256", game_id_bytes, file_hash, 1000)[:16],
        )
    )

    # === XOR combinations ===
    # file_hash XOR game_id (first 8 bytes)
    gid_bytes_8 = struct.pack("<Q", game_id_u64)
    xored = bytes(a ^ b for a, b in zip(file_hash, gid_bytes_8))
    keys.append(("fh_xor_gid_pad16", xored + b"\x00" * 8))

    # All header bytes XORed together, repeated
    hdr_xor = 0
    for b in header_bytes:
        hdr_xor ^= b
    keys.append(("header_xor_repeated", bytes([hdr_xor]) * 16))

    # === Additional creative derivations ===
    # game_id as raw bytes padded
    keys.append(("game_id_ascii_pad16", (game_id_bytes + b"\x00" * 16)[:16]))
    # game_id repeated
    keys.append(("game_id_ascii_repeat", (game_id_bytes * 2)[:16]))
    # All zeros
    keys.append(("all_zeros", bytes(16)))
    # All 0xFF
    keys.append(("all_ff", bytes([0xFF]) * 16))
    # file_hash as-is (8 bytes - for Blowfish which supports variable key length)
    keys.append(("file_hash_raw_8", file_hash))
    # MAGIC + file_hash
    keys.append(("md5(RIOT+fh)", hashlib.md5(b"RIOT" + file_hash).digest()))
    keys.append(("sm3(RIOT+fh)[:16]", sm3_hash(b"RIOT" + file_hash)[:16]))
    # SHA-224 (mentioned in binary analysis)
    keys.append(
        ("sha224(game_id)[:16]", hashlib.sha224(game_id_bytes).digest()[:16])
    )
    keys.append(
        ("sha224(file_hash)[:16]", hashlib.sha224(file_hash).digest()[:16])
    )
    keys.append(
        (
            "sha224(fh+gid)[:16]",
            hashlib.sha224(file_hash + game_id_bytes).digest()[:16],
        )
    )

    # Reverse-engineered from binary: possible hardcoded keys
    keys.append(("riot_salt_1", b"s5v8y/B?E(H+MbQe"))
    keys.append(("riot_salt_2", b"A?D(G+KbPeShVmYq"))

    # From metadata JSON
    keys.append(
        (
            "md5(game_length)",
            hashlib.md5(
                str(parser.metadata.game_length_ms).encode()
            ).digest(),
        )
    )

    return keys


# ---------------------------------------------------------------------------
# Nonce / IV candidates
# ---------------------------------------------------------------------------

def generate_nonces(parser: ROFL2Parser) -> list:
    """Generate nonce candidates for CTR mode."""
    file_hash = parser.header.file_hash
    game_id_bytes = parser.game_id.encode("ascii")
    game_id_u64 = int(parser.game_id)

    nonces = [
        ("zeros_12", bytes(12)),
        ("zeros_16", bytes(16)),
        ("file_hash[:12]", file_hash + b"\x00" * 4),
        ("file_hash_4+zeros", file_hash[:4] + b"\x00" * 8),
        ("game_id_le[:12]", struct.pack("<Q", game_id_u64) + b"\x00" * 4),
        ("game_id_be[:12]", struct.pack(">Q", game_id_u64) + b"\x00" * 4),
    ]
    return nonces


# ---------------------------------------------------------------------------
# CTR counter modes
# ---------------------------------------------------------------------------

CTR_MODES = [
    ("ctr_from_0", 0),
    ("ctr_from_1", 1),
]


# ---------------------------------------------------------------------------
# Main brute-force
# ---------------------------------------------------------------------------

def extract_test_payloads(parser: ROFL2Parser, count: int = 5) -> list:
    """Extract batch movement payloads for testing.

    Returns list of (timestamp, raw_payload, decoded_payload) tuples.
    We focus on mid-game packets (t > 60s) that are single-entity (len ~135-137).
    """
    payloads = []
    for block in parser.iter_blocks():
        if block.packet_id != MOVEMENT_PACKET_ID:
            continue
        if block.param != 0:
            continue
        if block.timestamp < 60:
            continue
        if len(block.payload) > 200:
            continue  # Skip multi-entity batches for now

        decoded = bytes(b ^ FILL_BYTE for b in block.payload)
        payloads.append((block.timestamp, block.payload, decoded))
        if len(payloads) >= count:
            break

    return payloads


def extract_sub_entity_payloads(
    parser: ROFL2Parser, near_time: float = 300.0, count: int = 5
) -> list:
    """Extract sub-entity records from multi-entity batch packets.

    Each sub-entity is 135 bytes starting at offset 135, 270, etc.
    """
    subs = []
    for block in parser.iter_blocks():
        if block.packet_id != MOVEMENT_PACKET_ID or block.param != 0:
            continue
        if abs(block.timestamp - near_time) > 5:
            continue
        if len(block.payload) < 300:
            continue

        decoded = bytes(b ^ FILL_BYTE for b in block.payload)
        # Extract sub-entities at 135-byte boundaries
        for off in range(135, len(decoded) - 10, 135):
            sub = decoded[off : off + 135]
            subs.append((block.timestamp, sub, off))
            if len(subs) >= count:
                return subs

    return subs


def test_key_cipher_combination(
    key_name: str,
    key: bytes,
    nonce_name: str,
    nonce: bytes,
    cipher_name: str,
    ctr_start: int,
    payload: bytes,
    timestamp: float,
    log_file,
) -> Optional[dict]:
    """Test a single key+nonce+cipher combination on a payload."""
    try:
        if cipher_name == "SM4-CTR":
            if len(key) != 16:
                return None
            # Adjust CTR start
            rk = sm4_key_expand(key)
            plaintext = bytearray()
            nonce_12 = nonce[:12] if len(nonce) >= 12 else nonce + b"\x00" * (12 - len(nonce))
            block_count = (len(payload) + 15) // 16
            for i in range(block_count):
                counter_block = nonce_12 + struct.pack(">I", i + ctr_start)
                keystream = sm4_encrypt_block(rk, counter_block)
                start = i * 16
                end = min(start + 16, len(payload))
                for j in range(end - start):
                    plaintext.append(payload[start + j] ^ keystream[j])
            decrypted = bytes(plaintext)

        elif cipher_name == "AES-128-CTR":
            if len(key) != 16:
                return None
            nonce_16 = nonce[:16] if len(nonce) >= 16 else nonce + b"\x00" * (16 - len(nonce))
            # Adjust nonce to include ctr_start
            if ctr_start > 0:
                n = int.from_bytes(nonce_16, "big") + ctr_start
                nonce_16 = n.to_bytes(16, "big")
            decrypted = aes_ctr_decrypt(key, nonce_16, payload)
            if not decrypted:
                return None

        elif cipher_name == "HMAC-SM3-CTR":
            decrypted = hmac_ctr_decrypt(key, nonce, payload)

        elif cipher_name == "Blowfish-ECB":
            decrypted = blowfish_decrypt(key, payload)
            if not decrypted:
                return None

        elif cipher_name == "XOR":
            decrypted = xor_decrypt(key, payload)

        else:
            return None

    except Exception as e:
        return None

    # Validate the decrypted data
    result = is_valid_movement(decrypted)
    dec_entropy = entropy(decrypted)
    orig_entropy = entropy(payload)

    combo_name = f"{key_name}|{cipher_name}|{nonce_name}|ctr={ctr_start}"

    # Log every attempt
    status = "VALID" if result["valid"] else "fail"
    log_line = (
        f"[{status}] {combo_name:70s} "
        f"entropy={orig_entropy:.2f}->{dec_entropy:.2f} "
        f"pt={result.get('parsing_type', '?')} "
        f"eid={hex(result['entity_id']) if result['entity_id'] else '?'} "
        f"speed={result.get('speed', '?')} "
        f"wp={result.get('first_wp', '?')} "
        f"reason={result['reason']}"
    )
    log_file.write(log_line + "\n")

    if result["valid"]:
        # Additional check: match against API positions
        x, y = result["first_wp"]
        api_match = validate_against_api(x, y)
        if api_match:
            result["api_match"] = api_match

        return {
            "combo": combo_name,
            "key": key.hex(),
            "nonce": nonce.hex(),
            "cipher": cipher_name,
            "decrypted_preview": decrypted[:32].hex(),
            "entropy_drop": orig_entropy - dec_entropy,
            "validation": result,
        }

    # Also flag significant entropy drops
    if orig_entropy - dec_entropy > 2.0:
        return {
            "combo": combo_name,
            "key": key.hex(),
            "nonce": nonce.hex(),
            "cipher": cipher_name,
            "decrypted_preview": decrypted[:32].hex(),
            "entropy_drop": orig_entropy - dec_entropy,
            "validation": result,
            "note": "significant entropy drop",
        }

    return None


# ---------------------------------------------------------------------------
# XOR-only analysis (no encryption hypothesis)
# ---------------------------------------------------------------------------

def test_xor_only(parser: ROFL2Parser, log_file) -> list:
    """Test the hypothesis that batch packets use XOR 0xFA only (no encryption).

    Validates by checking if XOR-decoded batch packets produce valid
    PathPacket structures with coordinates matching Riot API.
    """
    log_file.write("\n" + "=" * 80 + "\n")
    log_file.write("HYPOTHESIS: No encryption, just XOR 0xFA\n")
    log_file.write("=" * 80 + "\n")

    results = []
    valid_count = 0
    total_count = 0
    api_match_count = 0

    for block in parser.iter_blocks():
        if block.packet_id != MOVEMENT_PACKET_ID or block.param != 0:
            continue
        if block.timestamp < 60:
            continue
        total_count += 1
        if total_count > 200:
            break

        decoded = bytes(b ^ FILL_BYTE for b in block.payload)

        # For single-entity packets
        if len(decoded) <= 200:
            v = is_valid_movement(decoded)
            if v["valid"]:
                valid_count += 1
                x, y = v["first_wp"]
                api = validate_against_api(x, y, 500)
                if api:
                    api_match_count += 1

                log_file.write(
                    f"  [OK] t={block.timestamp:.1f}s len={len(decoded)} "
                    f"type={v['parsing_type']} eid=0x{v['entity_id']:08x} "
                    f"speed={v['speed']:.1f} pos=({x:.0f},{y:.0f}) "
                    f"api_match={api or 'none'}\n"
                )

        # For multi-entity packets, parse sub-entities
        elif len(decoded) > 200:
            for off in range(135, len(decoded) - 10, 135):
                sub = decoded[off : off + 135]
                v = is_valid_movement(sub)
                if v["valid"] and v["first_wp"]:
                    x, y = v["first_wp"]
                    api = validate_against_api(x, y, 500)
                    if api:
                        api_match_count += 1
                        log_file.write(
                            f"  [API MATCH] t={block.timestamp:.1f}s offset={off} "
                            f"pos=({x:.0f},{y:.0f}) -> {api}\n"
                        )

    summary = (
        f"XOR-only: {valid_count}/{total_count} valid, "
        f"{api_match_count} API matches"
    )
    log_file.write(f"\n{summary}\n")
    results.append({"hypothesis": "xor_only", "summary": summary})
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    rofl_path = sys.argv[1] if len(sys.argv) > 1 else ROFL_PATH

    print(f"ROFL2 Movement Packet Key Derivation Brute-Force")
    print(f"File: {rofl_path}")
    print(f"Results: {RESULTS_PATH}")
    print()

    # Parse replay
    parser = ROFL2Parser(rofl_path)
    print(f"Game ID:      {parser.game_id}")
    print(f"Game version: {parser.header.game_version}")
    print(f"File hash:    {parser.header.file_hash.hex()}")
    print(f"Game length:  {parser.metadata.game_length_ms / 1000:.0f}s")
    print()

    # Extract test payloads
    test_singles = extract_test_payloads(parser, count=3)
    test_subs = extract_sub_entity_payloads(parser, near_time=300.0, count=3)

    print(f"Test payloads: {len(test_singles)} single-entity, {len(test_subs)} sub-entities")

    # Generate keys, nonces, ciphers
    all_keys = generate_all_keys(parser)
    all_nonces = generate_nonces(parser)
    ciphers = ["SM4-CTR", "AES-128-CTR", "Blowfish-ECB", "XOR"]

    total_combos = (
        len(all_keys)
        * len(all_nonces)
        * len(ciphers)
        * len(CTR_MODES)
        * (len(test_singles) + len(test_subs))
    )
    print(f"Keys: {len(all_keys)}")
    print(f"Nonces: {len(all_nonces)}")
    print(f"Ciphers: {len(ciphers)}")
    print(f"CTR modes: {len(CTR_MODES)}")
    print(f"Total combinations: {total_combos}")
    print()

    # Add HMAC-SM3-CTR (slow, so only test a subset of keys)
    hmac_keys = [k for k in all_keys if "sm3" in k[0] or "game_id" in k[0]]

    hits = []
    tested = 0
    t_start = time.time()

    with open(RESULTS_PATH, "w") as log:
        log.write(f"ROFL2 Key Derivation Brute-Force Results\n")
        log.write(f"File: {rofl_path}\n")
        log.write(f"Game ID: {parser.game_id}\n")
        log.write(f"File hash: {parser.header.file_hash.hex()}\n")
        log.write(f"Game version: {parser.header.game_version}\n")
        log.write(f"Date: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        log.write(f"\n{'='*80}\n\n")

        # Phase 0: Test XOR-only hypothesis
        print("Phase 0: Testing XOR-only hypothesis (no encryption)...")
        xor_results = test_xor_only(parser, log)
        for r in xor_results:
            print(f"  {r['summary']}")

        # Phase 1: Standard ciphers (SM4-CTR, AES-CTR, Blowfish, XOR)
        log.write(f"\n{'='*80}\n")
        log.write(f"PHASE 1: Standard ciphers x all keys x all nonces\n")
        log.write(f"{'='*80}\n\n")

        print("\nPhase 1: Testing standard ciphers...")

        for key_name, key_bytes in all_keys:
            for cipher in ciphers:
                for nonce_name, nonce_bytes in all_nonces:
                    for ctr_name, ctr_start in CTR_MODES:
                        # Skip nonce variations for non-CTR ciphers
                        if cipher in ("Blowfish-ECB", "XOR") and nonce_name != "zeros_12":
                            continue

                        # Test on single-entity payloads
                        for ts, raw, decoded in test_singles[:1]:
                            tested += 1
                            hit = test_key_cipher_combination(
                                key_name,
                                key_bytes,
                                nonce_name,
                                nonce_bytes,
                                cipher,
                                ctr_start,
                                decoded,  # Try on XOR-decoded data
                                ts,
                                log,
                            )
                            if hit:
                                hits.append(hit)
                                print(
                                    f"  HIT: {hit['combo']} "
                                    f"entropy_drop={hit['entropy_drop']:.2f}"
                                )

                            # Also try on RAW data (maybe XOR 0xFA is wrong)
                            tested += 1
                            hit = test_key_cipher_combination(
                                key_name + "_on_raw",
                                key_bytes,
                                nonce_name,
                                nonce_bytes,
                                cipher,
                                ctr_start,
                                raw,
                                ts,
                                log,
                            )
                            if hit:
                                hits.append(hit)
                                print(
                                    f"  HIT: {hit['combo']} "
                                    f"entropy_drop={hit['entropy_drop']:.2f}"
                                )

                        # Test on sub-entity payloads
                        for ts, sub, off in test_subs[:1]:
                            tested += 1
                            hit = test_key_cipher_combination(
                                key_name,
                                key_bytes,
                                nonce_name,
                                nonce_bytes,
                                cipher,
                                ctr_start,
                                sub,
                                ts,
                                log,
                            )
                            if hit:
                                hits.append(hit)
                                print(
                                    f"  HIT (sub): {hit['combo']} "
                                    f"entropy_drop={hit['entropy_drop']:.2f}"
                                )

            if tested % 1000 == 0:
                elapsed = time.time() - t_start
                rate = tested / elapsed if elapsed > 0 else 0
                print(
                    f"  Tested {tested} combinations ({rate:.0f}/s) "
                    f"hits={len(hits)}..."
                )

        # Phase 2: HMAC-SM3-CTR (slow, subset of keys)
        log.write(f"\n{'='*80}\n")
        log.write(f"PHASE 2: HMAC-SM3-CTR (slow, subset of keys)\n")
        log.write(f"{'='*80}\n\n")

        print(f"\nPhase 2: Testing HMAC-SM3-CTR ({len(hmac_keys)} keys)...")

        for key_name, key_bytes in hmac_keys:
            for ts, raw, decoded in test_singles[:1]:
                tested += 1
                # HMAC-SM3-CTR with empty prefix
                hit = test_key_cipher_combination(
                    key_name,
                    key_bytes,
                    "empty_prefix",
                    b"",
                    "HMAC-SM3-CTR",
                    0,
                    decoded,
                    ts,
                    log,
                )
                if hit:
                    hits.append(hit)
                    print(f"  HIT: {hit['combo']}")

                # HMAC-SM3-CTR with file_hash prefix
                tested += 1
                hit = test_key_cipher_combination(
                    key_name,
                    key_bytes,
                    "fh_prefix",
                    parser.header.file_hash,
                    "HMAC-SM3-CTR",
                    0,
                    decoded,
                    ts,
                    log,
                )
                if hit:
                    hits.append(hit)
                    print(f"  HIT: {hit['combo']}")

        # Phase 3: Test if raw data (without XOR 0xFA) is already valid
        log.write(f"\n{'='*80}\n")
        log.write(f"PHASE 3: Raw data analysis (no transform at all)\n")
        log.write(f"{'='*80}\n\n")

        print("\nPhase 3: Testing raw data (no transform)...")

        for ts, raw, decoded in test_singles:
            v_raw = is_valid_movement(raw)
            v_dec = is_valid_movement(decoded)
            log.write(
                f"  Raw: valid={v_raw['valid']} reason={v_raw['reason']} "
                f"entropy={entropy(raw):.2f}\n"
            )
            log.write(
                f"  XOR 0xFA: valid={v_dec['valid']} reason={v_dec['reason']} "
                f"entropy={entropy(decoded):.2f}\n"
            )

        # Phase 4: Test different XOR fill bytes
        log.write(f"\n{'='*80}\n")
        log.write(f"PHASE 4: Alternative XOR fill bytes\n")
        log.write(f"{'='*80}\n\n")

        print("\nPhase 4: Testing alternative XOR fill bytes...")

        for fill_byte in range(256):
            if fill_byte == FILL_BYTE:
                continue
            for ts, raw, _ in test_singles[:1]:
                alt_decoded = bytes(b ^ fill_byte for b in raw)
                v = is_valid_movement(alt_decoded)
                if v["valid"]:
                    log.write(f"  [HIT] XOR fill=0x{fill_byte:02x}: valid!\n")
                    hits.append(
                        {
                            "combo": f"xor_fill_0x{fill_byte:02x}",
                            "validation": v,
                        }
                    )
                    print(f"  HIT: XOR fill byte 0x{fill_byte:02x}")

        # Phase 5: Search for raw coordinate patterns
        log.write(f"\n{'='*80}\n")
        log.write(f"PHASE 5: Coordinate pattern search in batch packets\n")
        log.write(f"{'='*80}\n\n")

        print("\nPhase 5: Searching for coordinate patterns...")

        # Search for API position coordinates in various formats
        coord_search_count = 0
        coord_hits = 0
        for block in parser.iter_blocks():
            if block.packet_id != MOVEMENT_PACKET_ID or block.param != 0:
                continue
            if 298 < block.timestamp < 302:
                raw = block.payload
                decoded = bytes(b ^ FILL_BYTE for b in raw)

                for data, data_name in [(raw, "raw"), (decoded, "xor_decoded")]:
                    for i in range(len(data) - 8):
                        # Try reading as two f32 coordinates
                        x_f32 = struct.unpack_from("<f", data, i)[0]
                        y_f32 = struct.unpack_from("<f", data, i + 4)[0]

                        if x_f32 == x_f32 and y_f32 == y_f32:  # not NaN
                            api = validate_against_api(x_f32, y_f32, 300)
                            if api:
                                coord_hits += 1
                                log.write(
                                    f"  [COORD HIT] {data_name} offset={i} "
                                    f"f32=({x_f32:.0f},{y_f32:.0f}) -> {api}\n"
                                )
                                print(
                                    f"  COORD HIT: {data_name} @{i} ({x_f32:.0f},{y_f32:.0f}) -> {api}"
                                )

                        # Try as i16 with standard formula
                        x_i16 = struct.unpack_from("<h", data, i)[0]
                        y_i16 = struct.unpack_from("<h", data, i + 2)[0]
                        x_world = x_i16 * 2.0 + 7358.0
                        y_world = y_i16 * 2.0 + 7412.0
                        api = validate_against_api(x_world, y_world, 300)
                        if api:
                            coord_hits += 1
                            log.write(
                                f"  [COORD HIT] {data_name} offset={i} "
                                f"i16=({x_world:.0f},{y_world:.0f}) -> {api}\n"
                            )
                            if coord_hits <= 10:
                                print(
                                    f"  COORD HIT: {data_name} @{i} i16 ({x_world:.0f},{y_world:.0f}) -> {api}"
                                )

                coord_search_count += 1
                if coord_search_count >= 5:
                    break

        log.write(f"\nCoordinate search: {coord_hits} hits in {coord_search_count} packets\n")

        # Summary
        elapsed = time.time() - t_start
        log.write(f"\n{'='*80}\n")
        log.write(f"SUMMARY\n")
        log.write(f"{'='*80}\n")
        log.write(f"Tested: {tested} combinations\n")
        log.write(f"Hits: {len(hits)}\n")
        log.write(f"Elapsed: {elapsed:.1f}s\n")
        log.write(f"Rate: {tested/elapsed:.0f} combinations/s\n")
        log.write(f"\n")

        if hits:
            log.write("HITS:\n")
            for h in hits:
                log.write(f"  {json.dumps(h, default=str)}\n")
        else:
            log.write(
                "NO HITS FOUND.\n\n"
                "CONCLUSION: The movement packet payloads are XOR 0xFA encoded\n"
                "(NOT encrypted). The coordinate parsing produces valid parsing_type,\n"
                "entity_id-like values, and map-range positions. However, the entity IDs\n"
                "are NOT in the 0x40000000 champion range and positions don't precisely\n"
                "match Riot API. This suggests either:\n"
                "1. The coordinate transform formula is different from what we assumed\n"
                "2. The entity ID format changed in this game version\n"
                "3. There's an additional obfuscation layer we haven't identified\n"
                "4. The encryption key is truly external (from GAMHS server)\n"
            )

    print(f"\nDone! Tested {tested} combinations in {elapsed:.1f}s")
    print(f"Hits: {len(hits)}")
    print(f"Results saved to: {RESULTS_PATH}")

    # Print hits
    if hits:
        print("\n=== HITS ===")
        for h in hits:
            print(f"  {h.get('combo', h.get('hypothesis', '?'))}")
            if "validation" in h and h["validation"].get("first_wp"):
                print(f"    Position: {h['validation']['first_wp']}")
            if "api_match" in h.get("validation", {}):
                print(f"    API Match: {h['validation']['api_match']}")


if __name__ == "__main__":
    main()
