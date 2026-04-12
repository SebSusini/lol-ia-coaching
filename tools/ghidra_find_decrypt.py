# Ghidra headless script to find decrypt functions in LoL binary
# Run with: analyzeHeadless /tmp/ghidra_project lol_analysis -import /tmp/lol_x86_64 -postScript ghidra_find_decrypt.py -scriptPath /path/to/this/dir
#
# This script searches for the movement and ward spawn decrypt functions
# by looking for characteristic patterns:
# - High density of ROL/ROR/XOR instructions
# - Specific function sizes (~1KB for mov_decrypt, ~9KB for ward_spawn_decrypt)
# - Functions that write to structure offsets used by Mowokuma

from ghidra.program.model.listing import CodeUnit
from ghidra.program.model.symbol import SourceType
from ghidra.app.decompiler import DecompileOptions, DecompInterface
import json

def get_function_bitop_count(func):
    """Count ROL/ROR/XOR/NOT instructions in a function."""
    body = func.getBody()
    listing = currentProgram.getListing()

    rol_ror = 0
    xor_count = 0
    not_count = 0

    code_units = listing.getCodeUnits(body, True)
    for cu in code_units:
        mnemonic = cu.getMnemonicString()
        if mnemonic in ("ROL", "ROR"):
            rol_ror += 1
        elif mnemonic == "XOR":
            xor_count += 1
        elif mnemonic == "NOT":
            not_count += 1

    return rol_ror, xor_count, not_count

def get_function_size(func):
    """Get function size in bytes."""
    body = func.getBody()
    return body.getNumAddresses()

def analyze():
    fm = currentProgram.getFunctionManager()
    functions = fm.getFunctions(True)

    candidates = []

    print("Analyzing functions for decrypt patterns...")
    count = 0

    for func in functions:
        count += 1
        if count % 5000 == 0:
            print(f"  Analyzed {count} functions...")

        size = get_function_size(func)

        # Skip very small or very large functions
        if size < 200 or size > 20000:
            continue

        rol_ror, xor_count, not_count = get_function_bitop_count(func)
        total_bitops = rol_ror + xor_count + not_count

        # Filter for high density of bitwise operations
        if total_bitops < 20:
            continue

        density = total_bitops / (size / 100.0)  # bitops per 100 bytes

        candidates.append({
            "name": func.getName(),
            "address": str(func.getEntryPoint()),
            "size": size,
            "rol_ror": rol_ror,
            "xor": xor_count,
            "not": not_count,
            "total_bitops": total_bitops,
            "density": round(density, 2)
        })

    print(f"\nTotal functions analyzed: {count}")
    print(f"Candidates with 20+ bitops: {len(candidates)}")

    # Sort by density
    candidates.sort(key=lambda x: -x["density"])

    # Print top candidates
    print("\n=== TOP 30 CANDIDATES (by bitop density) ===")
    for c in candidates[:30]:
        label = ""
        # Flag potential mov_decrypt (~1KB) and ward_spawn_decrypt (~9KB)
        if 500 <= c["size"] <= 2000:
            label = " [MOV_DECRYPT candidate - size matches]"
        elif 5000 <= c["size"] <= 15000:
            label = " [WARD_SPAWN_DECRYPT candidate - size matches]"

        print(f"  {c['address']} | {c['name']:40s} | size={c['size']:6d} | ROL/ROR={c['rol_ror']:3d} XOR={c['xor']:3d} NOT={c['not']:3d} | density={c['density']}{label}")

    # Also look for packet dispatch (switch with many cases)
    print("\n=== LOOKING FOR PACKET DISPATCH (large switch/case) ===")
    for func in fm.getFunctions(True):
        size = get_function_size(func)
        if size < 5000:
            continue

        # Count CMP instructions (indicative of switch/case)
        body = func.getBody()
        listing = currentProgram.getListing()
        cmp_count = 0
        jmp_count = 0
        for cu in listing.getCodeUnits(body, True):
            mn = cu.getMnemonicString()
            if mn == "CMP":
                cmp_count += 1
            elif mn in ("JE", "JNE", "JZ", "JNZ", "JA", "JB", "JAE", "JBE"):
                jmp_count += 1

        if cmp_count > 30:
            print(f"  {func.getEntryPoint()} | {func.getName():40s} | size={size} | CMP={cmp_count} JMP={jmp_count}")

    # Save results
    output = {
        "top_candidates": candidates[:50],
        "total_analyzed": count
    }

    with open("/tmp/ghidra_results.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nResults saved to /tmp/ghidra_results.json")

analyze()
