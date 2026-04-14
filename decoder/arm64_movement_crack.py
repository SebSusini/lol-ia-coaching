#!/usr/bin/env python3
"""
ARM64 Movement Packet Cracker for ROFL2 League of Legends replays.

Attempts to decode movement packets (packet_id=0x001c) by emulating the
ARM64 streaming decrypt function from the Mac LoL binary using Unicorn Engine.

Binary analysis findings (ARM64 slice, vmaddr base 0x100000000):
  Movement handler vtable:     0x1022bf318 (returned by 0x101c96968)
    [0x08] = 0x1c (packet_id)
    [0x18] = 0x101c96ba8 (init handler - calls SHA-224 init at 0x101cc8024)
    [0x20] = 0x101c96bbc (process handler - calls SHA-224 update at 0x101cc808c)
    [0x28] = 0x101c96be8 (finalize handler - calls SHA-224 final at 0x101cc8530)

  SHA-224 crypto functions:
    SHA-224 init:     0x101cc8024  (loads standard SHA-224 IV from 0x101fbfd90)
    SHA-224 update:   0x101cc808c  (Merkle-Damgard, 64-byte blocks)
    SHA-256 compress: 0x101cc8534  (confirmed by K constants: 0x428a2f98...)
    SHA-224 finalize: 0x101cc8194  (pad + compress + output 28 bytes)
    SHA-224 IV:       0x101fbfd90  (standard: c1059ed8 367cd507 3070dd17 ...)

  Streaming cipher functions (from handler region 0x101c89000-0x101c8d000):
    STREAM_A (CBC-encrypt-like): 0x101ca3e20  XOR + block cipher
    STREAM_B (ECB-like):         0x101ca402c
    STREAM_C (CTR-like):         0x101ca5354
    STREAM_D (CTR+counter):      0x101ca8600
    SM4 key expansion:           0x101ccfc8c
    SM4 CTR encrypt fn ptr:      0x101ccfdd8
    SM4 ECB encrypt fn ptr:      0x101cd0754

  Crypto context helpers:
    GET_CRYPTO_CTX:   [x0+0x78]    (0x101c95e54)
    GET_CTR_FLAG:     [x0+0x10]    (0x101c95e4c)
    GET_ENCRYPT_KEY:  x0+0x28      (0x101c95e6c)
    GET_FIELD_COUNT:  [x0+0x68]    (0x101c95e94)
    GET_SHA224_STATE: [x0+0x18]    (0x101c95fe0)

  Key findings:
    - Movement payloads are NOT plaintext with 0xFA fill
    - They are encrypted: entity_ids, speeds, positions are all wrong after XOR 0xFA
    - SHA-224 is used for INTEGRITY (hash/MAC), not for encryption itself
    - The streaming cipher (STREAM_A-D) handles actual encryption using SM4
    - Spawn packets (t=0) appear to work with XOR 0xFA but positions are at map center
    - Batch packets (param=0) contain all actual movement data (57K+ per game)
    - Batch records are 137 bytes with 19-21 non-0xFA data bytes

  Architecture:
    Packet dispatch (0x101c5c294) -> create handler via vtable 0x1022bf318
    -> SHA-224 init + process (hash payload for integrity)
    -> Streaming cipher decrypt (SM4-based, key from session context)
    -> Output decrypted PathPacket fields

Status:
    - Spawn packets (t=0): decode correctly with XOR 0xFA
    - Gameplay movement (t>0, batch packets): ENCRYPTED, requires session key
    - The session key is derived during game initialization (not stored in replay)
    - Next step: find the key derivation or extract key from a live game session

Usage:
    python arm64_movement_crack.py [replay.rofl] [--step N]
"""

import argparse
import json
import math
import os
import struct
import sys
import time
from collections import Counter
from dataclasses import dataclass
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sm4_decoder import ROFL2Parser, Block, sm4_key_expand, sm4_encrypt_block

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MOVEMENT_PACKET_ID = 0x001C
FILL_BYTE = 0xFA

ARM64_BINARY = "/tmp/lol_arm64"
BASE = 0x100000000

# Segment layout
TEXT_VMADDR = 0x100000000
TEXT_FILEOFF = 0x000000000
TEXT_FILESIZE = 0x002170000

DATA_CONST_VMADDR = 0x102170000
DATA_CONST_FILEOFF = 0x002170000
DATA_CONST_FILESIZE = 0x000188000

DATA_VMADDR = 0x1022f8000
DATA_FILEOFF = 0x0022f8000
DATA_FILESIZE = 0x000020000

# Key function addresses
STREAM_A = 0x101ca3e20
STREAM_B = 0x101ca402c
STREAM_C = 0x101ca5354
STREAM_D = 0x101ca8600
SM4_KEY_EXPAND = 0x101ccfc8c
SM4_CTR_ENCRYPT = 0x101ccfdd8
SM4_ECB_ENCRYPT = 0x101cd0754
SM4_INIT_IV = 0x101ccfc70

# Movement handler vtable
MOV_HANDLER_VTABLE = 0x1022bf318

# Crypto helper addrs
ADDR_GET_CRYPTO_CTX = 0x101c95e54  # ldr x0, [x0, #0x78]; ret
ADDR_GET_CTR_FLAG = 0x101c95e4c    # ldr w0, [x0, #0x10]; ret
ADDR_GET_ENCRYPT_KEY = 0x101c95e6c  # add x0, x0, #0x28; ret
ADDR_GET_FIELD_COUNT = 0x101c95e94  # ldr w0, [x0, #0x68]; ret

# SM4 tables in binary
SM4_SBOX_TABLE = 0x101fc0340
SM4_CK_TABLE = 0x101fc02c0

# Emulator memory
STACK_BASE = 0x7FFFFF000000
STACK_SIZE = 0x10000
HEAP_BASE = 0x7FFFFF100000
HEAP_SIZE = 0x200000

# Coordinate transform
COORD_X_OFFSET = 7358.0
COORD_Y_OFFSET = 7412.0


# ---------------------------------------------------------------------------
# PathPacket Parser
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
                bit2 = ((temp_arr[byte_idx2] >> (v21 & 7)) & 1
                        if byte_idx2 < len(temp_arr) else 0)
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
    return 50.0 <= s <= 1500.0 and s == s


def is_valid_entity_id(eid):
    return 0x40000000 <= eid <= 0x40000200


# ---------------------------------------------------------------------------
# ARM64 Emulator
# ---------------------------------------------------------------------------

class ARM64MovementCracker:
    """Emulates the ARM64 streaming decrypt for movement packets."""

    def __init__(self):
        self.uc = None
        self.binary_data = None
        self.heap_cursor = 0

    def load_binary(self) -> bool:
        if not os.path.exists(ARM64_BINARY):
            print(f"ERROR: ARM64 binary not found at {ARM64_BINARY}")
            return False
        with open(ARM64_BINARY, "rb") as f:
            self.binary_data = f.read()
        print(f"  Loaded ARM64 binary: {len(self.binary_data):,} bytes")
        return True

    def setup_emulator(self) -> bool:
        from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
        from unicorn.unicorn_const import UC_PROT_ALL, UC_PROT_READ, UC_PROT_WRITE

        self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)

        # Stack
        self.uc.mem_map(STACK_BASE, STACK_SIZE, UC_PROT_READ | UC_PROT_WRITE)

        # Heap
        self.uc.mem_map(HEAP_BASE, HEAP_SIZE, UC_PROT_READ | UC_PROT_WRITE)

        # __TEXT segment (contains code + const data)
        text_size = (TEXT_FILESIZE + 0xFFF) & ~0xFFF
        self.uc.mem_map(TEXT_VMADDR, text_size, UC_PROT_ALL)
        self.uc.mem_write(TEXT_VMADDR,
                          self.binary_data[TEXT_FILEOFF:TEXT_FILEOFF + TEXT_FILESIZE])

        # __DATA_CONST (contains vtables, SM4 tables)
        dc_size = (DATA_CONST_FILESIZE + 0xFFF) & ~0xFFF
        self.uc.mem_map(DATA_CONST_VMADDR, dc_size, UC_PROT_ALL)
        self.uc.mem_write(DATA_CONST_VMADDR,
                          self.binary_data[DATA_CONST_FILEOFF:DATA_CONST_FILEOFF + DATA_CONST_FILESIZE])

        # __DATA (contains globals)
        d_size = (DATA_FILESIZE + 0xFFF) & ~0xFFF
        self.uc.mem_map(DATA_VMADDR, d_size + 0x200000, UC_PROT_ALL)
        self.uc.mem_write(DATA_VMADDR,
                          self.binary_data[DATA_FILEOFF:DATA_FILEOFF + DATA_FILESIZE])

        print(f"  Mapped __TEXT:       0x{TEXT_VMADDR:x} ({text_size:#x})")
        print(f"  Mapped __DATA_CONST: 0x{DATA_CONST_VMADDR:x} ({dc_size:#x})")
        print(f"  Mapped __DATA:       0x{DATA_VMADDR:x} ({d_size:#x})")

        return True

    def reset(self):
        from unicorn.arm64_const import UC_ARM64_REG_SP
        self.heap_cursor = 0
        self.uc.reg_write(UC_ARM64_REG_SP, STACK_BASE + STACK_SIZE - 0x200)
        self.uc.mem_write(HEAP_BASE, b'\x00' * min(HEAP_SIZE, 0x20000))

    def alloc(self, size: int) -> int:
        ptr = HEAP_BASE + self.heap_cursor
        self.heap_cursor += (size + 15) & ~15
        return ptr

    def alloc_write(self, data: bytes) -> int:
        ptr = self.alloc(len(data))
        self.uc.mem_write(ptr, data)
        return ptr

    def read(self, addr: int, size: int) -> bytes:
        return bytes(self.uc.mem_read(addr, size))

    # ------------------------------------------------------------------
    # Test: verify SM4 init works
    # ------------------------------------------------------------------
    def test_sm4_init(self) -> bool:
        from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X30
        from unicorn import UC_HOOK_MEM_UNMAPPED

        self.reset()
        out_buf = self.alloc(64)
        self.uc.reg_write(UC_ARM64_REG_X0, out_buf)

        ret_addr = self.alloc(4)
        self.uc.mem_write(ret_addr, b'\xc0\x03\x5f\xd6')  # RET
        self.uc.reg_write(UC_ARM64_REG_X30, ret_addr)

        errors = []
        def hook_err(uc, access, address, size, value, ud):
            errors.append(f"unmapped mem at 0x{address:x}")
            uc.emu_stop()
            return False

        h = self.uc.hook_add(UC_HOOK_MEM_UNMAPPED, hook_err)
        try:
            self.uc.emu_start(SM4_INIT_IV, ret_addr, timeout=5_000_000)
        except Exception as e:
            errors.append(str(e))
        self.uc.hook_del(h)

        if errors:
            print(f"  SM4 init FAILED: {errors}")
            return False

        result = self.read(out_buf, 32)
        print(f"  SM4 init result: {result.hex()}")
        return True

    # ------------------------------------------------------------------
    # Test: verify SM4 key expansion works
    # ------------------------------------------------------------------
    def test_sm4_key_expand(self, key: bytes) -> bool:
        from unicorn.arm64_const import (
            UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X30,
        )
        from unicorn import UC_HOOK_MEM_UNMAPPED

        self.reset()

        # SM4 key expansion: x0 = output (expanded key), x1 = 16-byte key
        key_buf = self.alloc_write(key)
        out_buf = self.alloc(256)  # room for 32 round keys

        self.uc.reg_write(UC_ARM64_REG_X0, out_buf)
        self.uc.reg_write(UC_ARM64_REG_X1, key_buf)

        ret_addr = self.alloc(4)
        self.uc.mem_write(ret_addr, b'\xc0\x03\x5f\xd6')
        self.uc.reg_write(UC_ARM64_REG_X30, ret_addr)

        errors = []
        def hook_err(uc, access, address, size, value, ud):
            errors.append(f"unmapped mem at 0x{address:x}")
            uc.emu_stop()
            return False

        h = self.uc.hook_add(UC_HOOK_MEM_UNMAPPED, hook_err)
        try:
            self.uc.emu_start(SM4_KEY_EXPAND, ret_addr, timeout=5_000_000)
        except Exception as e:
            errors.append(str(e))
        self.uc.hook_del(h)

        if errors:
            print(f"  SM4 key expand FAILED: {errors}")
            return False

        result = self.read(out_buf, 128)
        print(f"  SM4 expanded key (first 32 bytes): {result[:32].hex()}")
        return True

    # ------------------------------------------------------------------
    # Approach 1: Emulate the decrypt wrapper function (0x101c89d94)
    # ------------------------------------------------------------------
    def try_decrypt_wrapper(self, payload: bytes) -> Optional[bytes]:
        """Try to emulate the full decrypt wrapper function.

        The wrapper at 0x101c89d94 takes:
          x0 = crypto_context_wrapper (object with [+0x78] = crypto_ctx)
          x1 = output_buffer
          x2 = input_buffer
          x3 = length

        The crypto_ctx at [x0+0x78] must have:
          [+0x10]  = ctr_flag (w32)
          [+0x28]  = key_state (16 bytes)
          [+0x68]  = field_count (w32)
          [+0xf8]  = cipher_fn_ptr (ptr to block cipher function)
          [+0x100] = alt_fn_ptr (ptr or 0)
        """
        from unicorn.arm64_const import (
            UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2,
            UC_ARM64_REG_X3, UC_ARM64_REG_X30, UC_ARM64_REG_SP,
        )
        from unicorn import UC_HOOK_MEM_UNMAPPED, UC_HOOK_CODE

        self.reset()

        # Allocate input/output buffers
        in_buf = self.alloc_write(payload)
        out_buf = self.alloc(len(payload) + 64)

        # Create crypto context (0x110 bytes)
        ctx_size = 0x310
        ctx = self.alloc(ctx_size)

        # Set CTR flag at [ctx+0x10]
        self.uc.mem_write(ctx + 0x10, struct.pack("<I", 1))  # CTR mode

        # Set key state at [ctx+0x28] - initialize with zeros for now
        # (The actual key needs to come from the game session)
        self.uc.mem_write(ctx + 0x28, bytes(16))

        # Set field count at [ctx+0x68]
        self.uc.mem_write(ctx + 0x68, struct.pack("<I", 38))

        # Set cipher function pointer at [ctx+0xf8]
        # This should point to the SM4 encrypt function
        # Using SM4_CTR_ENCRYPT = 0x101ccfdd8
        self.uc.mem_write(ctx + 0xf8, struct.pack("<Q", SM4_CTR_ENCRYPT))

        # Set alt_fn_ptr at [ctx+0x100] = 0 (use standard path)
        self.uc.mem_write(ctx + 0x100, struct.pack("<Q", 0))

        # Create the wrapper object
        wrapper = self.alloc(0x80)
        # [wrapper+0x78] = ctx pointer
        self.uc.mem_write(wrapper + 0x78, struct.pack("<Q", ctx))

        # Set up registers
        self.uc.reg_write(UC_ARM64_REG_X0, wrapper)
        self.uc.reg_write(UC_ARM64_REG_X1, out_buf)
        self.uc.reg_write(UC_ARM64_REG_X2, in_buf)
        self.uc.reg_write(UC_ARM64_REG_X3, len(payload))

        # Set return address
        ret_addr = self.alloc(4)
        self.uc.mem_write(ret_addr, b'\xc0\x03\x5f\xd6')
        self.uc.reg_write(UC_ARM64_REG_X30, ret_addr)

        # Set up stack
        self.uc.reg_write(UC_ARM64_REG_SP, STACK_BASE + STACK_SIZE - 0x200)

        errors = []
        instructions_executed = [0]

        def hook_err(uc, access, address, size, value, ud):
            errors.append(f"unmapped mem access at 0x{address:x} (size={size})")
            uc.emu_stop()
            return False

        def hook_code(uc, address, size, ud):
            instructions_executed[0] += 1
            if instructions_executed[0] > 50000:
                errors.append("too many instructions")
                uc.emu_stop()

        h1 = self.uc.hook_add(UC_HOOK_MEM_UNMAPPED, hook_err)
        h2 = self.uc.hook_add(UC_HOOK_CODE, hook_code)

        try:
            # Execute the decrypt wrapper
            self.uc.emu_start(0x101c89d94, ret_addr, timeout=10_000_000)
        except Exception as e:
            errors.append(str(e))

        self.uc.hook_del(h1)
        self.uc.hook_del(h2)

        print(f"  Instructions executed: {instructions_executed[0]}")
        if errors:
            print(f"  Errors: {errors[:3]}")
            return None

        result = self.read(out_buf, len(payload))
        return result

    # ------------------------------------------------------------------
    # Approach 2: Emulate STREAM_A directly
    # ------------------------------------------------------------------
    def try_stream_a_direct(self, payload: bytes, key_state: bytes = None) -> Optional[bytes]:
        """Try to call STREAM_A directly with constructed arguments.

        STREAM_A at 0x101ca3e20:
          x0 = output_buf
          x1 = input_buf  (encrypted payload)
          x2 = length
          x3 = crypto_context (passed to cipher_fn as 2nd arg)
          x4 = key_state (16 bytes, XOR'd with input before cipher)
          x5 = cipher_fn (block cipher function pointer)

        The function does for each 16-byte block:
          block = input[i:i+16] XOR key_state
          cipher_fn(block, block, crypto_context)
          output[i:i+16] = block
          key_state = block  (for next iteration)
        """
        from unicorn.arm64_const import (
            UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2,
            UC_ARM64_REG_X3, UC_ARM64_REG_X4, UC_ARM64_REG_X5,
            UC_ARM64_REG_X30, UC_ARM64_REG_SP,
        )
        from unicorn import UC_HOOK_MEM_UNMAPPED, UC_HOOK_CODE

        self.reset()

        if key_state is None:
            key_state = bytes(16)

        # Allocate buffers
        in_buf = self.alloc_write(payload)
        out_buf = self.alloc(len(payload) + 32)
        key_buf = self.alloc_write(key_state)

        # Create crypto context for the cipher function
        # The SM4 cipher functions need a context with expanded round keys
        ctx = self.alloc(0x200)
        # Initialize SM4 context (the init function at SM4_INIT_IV stores IV)
        self.uc.mem_write(ctx, bytes(0x200))

        # Set up registers
        self.uc.reg_write(UC_ARM64_REG_X0, out_buf)
        self.uc.reg_write(UC_ARM64_REG_X1, in_buf)
        self.uc.reg_write(UC_ARM64_REG_X2, len(payload))
        self.uc.reg_write(UC_ARM64_REG_X3, ctx)
        self.uc.reg_write(UC_ARM64_REG_X4, key_buf)
        self.uc.reg_write(UC_ARM64_REG_X5, SM4_CTR_ENCRYPT)

        ret_addr = self.alloc(4)
        self.uc.mem_write(ret_addr, b'\xc0\x03\x5f\xd6')
        self.uc.reg_write(UC_ARM64_REG_X30, ret_addr)
        self.uc.reg_write(UC_ARM64_REG_SP, STACK_BASE + STACK_SIZE - 0x200)

        errors = []
        insn_count = [0]

        def hook_err(uc, access, address, size, value, ud):
            errors.append(f"unmapped at 0x{address:x}")
            uc.emu_stop()
            return False

        def hook_code(uc, address, size, ud):
            insn_count[0] += 1
            if insn_count[0] > 200000:
                errors.append("insn limit")
                uc.emu_stop()

        h1 = self.uc.hook_add(UC_HOOK_MEM_UNMAPPED, hook_err)
        h2 = self.uc.hook_add(UC_HOOK_CODE, hook_code)

        try:
            self.uc.emu_start(STREAM_A, ret_addr, timeout=30_000_000)
        except Exception as e:
            errors.append(str(e))

        self.uc.hook_del(h1)
        self.uc.hook_del(h2)

        print(f"  STREAM_A: {insn_count[0]} instructions")
        if errors:
            print(f"  Errors: {errors[:3]}")
            return None

        result = self.read(out_buf, len(payload))
        return result

    # ------------------------------------------------------------------
    # Approach 3: Pure Python SM4-based streaming decrypt
    # ------------------------------------------------------------------
    def try_sm4_streaming_decrypt(self, payload: bytes, key: bytes = None,
                                   iv: bytes = None) -> bytes:
        """Try SM4-based streaming decryption in various modes.

        Tries multiple approaches:
        1. SM4-CTR with various keys and nonces
        2. SM4-CBC decrypt
        3. SM4-ECB + XOR (what STREAM_A actually does)
        """
        if key is None:
            key = bytes(16)
        if iv is None:
            iv = bytes(16)

        results = {}

        # Mode 1: CTR - XOR with SM4-encrypted counter blocks
        rk = sm4_key_expand(key)
        decrypted_ctr = bytearray()
        for i in range((len(payload) + 15) // 16):
            counter_block = iv[:12] + struct.pack(">I", i + 1)
            keystream = sm4_encrypt_block(rk, counter_block)
            start = i * 16
            end = min(start + 16, len(payload))
            for j in range(end - start):
                decrypted_ctr.append(payload[start + j] ^ keystream[j])
        results["ctr"] = bytes(decrypted_ctr)

        # Mode 2: ECB decrypt (reverse of encrypt)
        # SM4 decryption uses reversed round keys
        rk_rev = list(reversed(rk))
        decrypted_ecb = bytearray()
        for i in range(0, len(payload) - 15, 16):
            block = payload[i:i+16]
            decrypted_ecb.extend(sm4_encrypt_block(rk_rev, block))
        results["ecb_decrypt"] = bytes(decrypted_ecb)

        # Mode 3: CBC-like (STREAM_A pattern)
        # STREAM_A does: output = encrypt(input XOR prev_state)
        # So to decrypt: input XOR prev_state = decrypt(output)
        # -> input = decrypt(output) XOR prev_state
        prev = iv
        decrypted_cbc = bytearray()
        for i in range(0, len(payload) - 15, 16):
            block = payload[i:i+16]
            decrypted_block = sm4_encrypt_block(rk_rev, block)
            for j in range(16):
                decrypted_cbc.append(decrypted_block[j] ^ prev[j])
            prev = block
        results["cbc_decrypt"] = bytes(decrypted_cbc)

        # Mode 4: XOR with key state only (no cipher, like a Vigenere)
        decrypted_xor = bytes(p ^ k for p, k in
                              zip(payload, (key * ((len(payload) // 16) + 1))))
        results["xor_key"] = decrypted_xor

        return results

    # ------------------------------------------------------------------
    # Validate decrypted output
    # ------------------------------------------------------------------
    def validate_decryption(self, decrypted: bytes, timestamp: float,
                            entity_id: int = 0) -> Optional[PathPacket]:
        """Try to parse decrypted bytes as a PathPacket."""
        if len(decrypted) < 10:
            return None

        pkt = PathPacket.parse(timestamp, decrypted, entity_id)
        if pkt is None:
            return None

        # Validate
        if not is_valid_entity_id(pkt.entity_id) and entity_id == 0:
            return None
        if not (pkt.speed == pkt.speed):  # NaN check
            return None
        if pkt.speed < 0 or pkt.speed > 10000:
            return None
        if not pkt.waypoints:
            return None

        valid_wps = sum(1 for x, y in pkt.waypoints if is_valid_position(x, y))
        if valid_wps < len(pkt.waypoints) * 0.5:
            return None

        return pkt


# ---------------------------------------------------------------------------
# Analysis: Extract movement packets for testing
# ---------------------------------------------------------------------------

def get_movement_packets(replay_path: str, max_packets: int = 50):
    """Extract movement packets from a replay file."""
    parser = ROFL2Parser(replay_path)
    print(f"  Game: {parser.game_id}")
    print(f"  Version: {parser.header.game_version}")
    print(f"  Length: {parser.metadata.game_length_ms / 1000:.0f}s")

    packets = []
    for block in parser.iter_blocks():
        if block.packet_id != MOVEMENT_PACKET_ID:
            continue
        packets.append(block)
        if max_packets and len(packets) >= max_packets:
            break

    return parser, packets


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="ARM64 Movement Packet Cracker")
    ap.add_argument("rofl_file", nargs="?",
                    default="/Users/sommesi/projects/lol-replay-analyzer/replays/EUW1-7816865419.rofl")
    ap.add_argument("--step", type=int, default=0,
                    help="Run a specific step (1-5)")
    ap.add_argument("--max-packets", type=int, default=20)
    ap.add_argument("--output", "-o", type=str, metavar="PATH")
    args = ap.parse_args()

    print("ARM64 Movement Packet Cracker")
    print("=" * 60)

    # Step 1: Set up emulator
    print("\n[Step 1] Setting up ARM64 emulator...")
    cracker = ARM64MovementCracker()

    if not cracker.load_binary():
        return

    if not cracker.setup_emulator():
        return

    # Step 2: Test SM4 primitives
    if args.step == 0 or args.step == 2:
        print("\n[Step 2] Testing SM4 primitives...")
        cracker.test_sm4_init()

        test_key = bytes.fromhex("0123456789ABCDEFFEDCBA9876543210")
        cracker.test_sm4_key_expand(test_key)

    # Step 3: Extract movement packets
    print("\n[Step 3] Extracting movement packets...")
    parser, packets = get_movement_packets(args.rofl_file, args.max_packets)

    individual = [p for p in packets if p.param != 0]
    batch = [p for p in packets if p.param == 0]
    print(f"  Individual packets: {len(individual)}")
    print(f"  Batch packets: {len(batch)}")

    # Step 4: Try decryption approaches
    print("\n[Step 4] Trying decryption approaches...")

    # Use the first individual packet with a known entity ID
    test_packets = [p for p in individual
                    if 0x400000ae <= p.param <= 0x400000b7][:5]
    if not test_packets:
        test_packets = individual[:5]

    for i, pkt in enumerate(test_packets):
        print(f"\n  --- Packet {i} ---")
        print(f"  Entity: 0x{pkt.param:08x}")
        print(f"  Timestamp: {pkt.timestamp:.3f}s")
        print(f"  Payload length: {len(pkt.payload)}")
        print(f"  Payload (first 32): {pkt.payload[:32].hex()}")

        # Count non-0xFA bytes
        non_fa = sum(1 for b in pkt.payload if b != 0xFA)
        print(f"  Non-0xFA bytes: {non_fa}/{len(pkt.payload)}")

        # Approach A: XOR with 0xFA (simple fill byte removal)
        decoded_fa = bytes(b ^ FILL_BYTE for b in pkt.payload)
        print(f"  XOR 0xFA (first 32): {decoded_fa[:32].hex()}")

        # Try parsing the XOR'd result
        pkt_fa = cracker.validate_decryption(decoded_fa, pkt.timestamp, pkt.param)
        if pkt_fa:
            print(f"  ** XOR 0xFA VALID! entity=0x{pkt_fa.entity_id:08x} "
                  f"speed={pkt_fa.speed:.0f} waypoints={len(pkt_fa.waypoints)}")
            if pkt_fa.waypoints:
                x, y = pkt_fa.waypoints[0]
                print(f"     First position: ({x:.0f}, {y:.0f})")
        else:
            print(f"  XOR 0xFA: invalid PathPacket")

        # Approach B: Try SM4 streaming decrypt with various keys
        game_id_bytes = parser.game_id.encode("ascii")
        file_hash = parser.header.file_hash

        keys_to_try = [
            ("zeros", bytes(16)),
            ("game_id_padded", (game_id_bytes * 2)[:16]),
            ("file_hash+pad", file_hash + bytes(8)),
            ("file_hash_doubled", (file_hash * 2)[:16]),
        ]

        for key_name, key in keys_to_try:
            results = cracker.try_sm4_streaming_decrypt(pkt.payload, key)
            for mode_name, decrypted in results.items():
                validated = cracker.validate_decryption(
                    decrypted, pkt.timestamp, pkt.param)
                if validated:
                    print(f"  ** SM4 {mode_name} with {key_name} VALID!")
                    print(f"     entity=0x{validated.entity_id:08x} "
                          f"speed={validated.speed:.0f}")
                    if validated.waypoints:
                        x, y = validated.waypoints[0]
                        print(f"     pos=({x:.0f}, {y:.0f})")

        # Approach C: Try emulating STREAM_A directly
        if args.step == 0 or args.step == 4:
            print(f"\n  Trying STREAM_A emulation...")
            result = cracker.try_stream_a_direct(pkt.payload)
            if result:
                print(f"  STREAM_A result (first 32): {result[:32].hex()}")
                validated = cracker.validate_decryption(
                    result, pkt.timestamp, pkt.param)
                if validated:
                    print(f"  ** STREAM_A VALID!")

        # Approach D: Try full wrapper emulation
        if args.step == 0 or args.step == 4:
            print(f"\n  Trying full wrapper emulation...")
            result = cracker.try_decrypt_wrapper(pkt.payload)
            if result:
                print(f"  Wrapper result (first 32): {result[:32].hex()}")
                validated = cracker.validate_decryption(
                    result, pkt.timestamp, pkt.param)
                if validated:
                    print(f"  ** Wrapper VALID!")

        # Only try first 2 packets in full mode
        if i >= 1 and (args.step == 0 or args.step == 4):
            break

    # Step 5: Summary and export
    print("\n" + "=" * 60)
    print("[Step 5] Summary")
    print("=" * 60)

    # Try the simple XOR on all packets to see overall patterns
    valid_count = 0
    total_count = 0
    valid_positions = []

    for pkt in packets:
        total_count += 1
        decoded = bytes(b ^ FILL_BYTE for b in pkt.payload)
        validated = cracker.validate_decryption(decoded, pkt.timestamp, pkt.param)
        if validated and validated.waypoints:
            valid_count += 1
            x, y = validated.waypoints[0]
            valid_positions.append({
                "t": round(pkt.timestamp, 3),
                "entity": pkt.param,
                "speed": round(validated.speed, 1),
                "x": round(x, 1),
                "y": round(y, 1),
                "wps": len(validated.waypoints),
            })

    print(f"\n  XOR 0xFA results: {valid_count}/{total_count} valid PathPackets")
    if valid_positions:
        print(f"  Sample valid positions:")
        for p in valid_positions[:10]:
            print(f"    t={p['t']:7.1f}s entity=0x{p['entity']:08x} "
                  f"pos=({p['x']:7.0f}, {p['y']:7.0f}) "
                  f"speed={p['speed']:.0f} wps={p['wps']}")

    if args.output and valid_positions:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w") as f:
            json.dump({
                "game_id": parser.game_id,
                "game_version": parser.header.game_version,
                "method": "xor_0xfa",
                "valid_count": valid_count,
                "total_count": total_count,
                "positions": valid_positions,
            }, f, indent=2)
        print(f"\n  Exported to {args.output}")


if __name__ == "__main__":
    main()
