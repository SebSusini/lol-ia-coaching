"""
Ghidra headless script to analyze LoL packet decrypt functions.
Run with: analyzeHeadless ... -postScript ghidra_analyze_decrypt.py

Targets:
- Jump table at RVA 0x1449510 (packet dispatch for types 0x00-0x1c)
- Handler for packet 0x1c at RVA 0x1446d4a
- Xorshift PRNG at 0x1446d67
- Stack reader at RVA 0x14495f0
- Functions referencing lookup tables at 0x1912450 and 0x1912550
"""
# @category Analysis

from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor
import json

BASE = 0x140000000

# Key RVAs to analyze
TARGETS = {
    "packet_dispatch_switch": 0x1446636,
    "packet_0x1c_handler": 0x1446d4a,
    "packet_default_handler": 0x1446da4,
    "xorshift_prng": 0x1446d67,
    "stack_reader": 0x14495f0,
    "stack_builder": 0x14425b0,
    "type_byte_reader": 0x1444dbd,
    "payload_decoder": 0x140b680,
    "lookup_table_A": 0x1912450,
    "lookup_table_B": 0x1912550,
}

monitor = ConsoleTaskMonitor()
decompiler = DecompInterface()
decompiler.openProgram(currentProgram)

results = {}

for name, rva in TARGETS.items():
    addr = currentProgram.getAddressFactory().getDefaultAddressSpace().getAddress(BASE + rva)
    func = getFunctionContaining(addr)

    if func:
        print(f"\n{'='*60}")
        print(f"=== {name} at RVA 0x{rva:x} ===")
        print(f"Function: {func.getName()} at {func.getEntryPoint()}")
        print(f"Size: {func.getBody().getNumAddresses()} bytes")

        # Decompile
        dec_result = decompiler.decompileFunction(func, 30, monitor)
        if dec_result and dec_result.decompileCompleted():
            c_code = dec_result.getDecompiledFunction().getC()
            # Save to file
            results[name] = {
                "rva": hex(rva),
                "function_name": func.getName(),
                "entry": str(func.getEntryPoint()),
                "size": func.getBody().getNumAddresses(),
                "decompiled": c_code[:5000],  # first 5000 chars
            }

            # Print first 100 lines
            lines = c_code.split('\n')
            for line in lines[:100]:
                print(line)
            if len(lines) > 100:
                print(f"... ({len(lines)} lines total)")
        else:
            print(f"Decompilation failed for {name}")
            results[name] = {"rva": hex(rva), "error": "decompilation failed"}
    else:
        print(f"No function found at RVA 0x{rva:x}")
        results[name] = {"rva": hex(rva), "error": "no function"}

# Save results
with open("/home/susini/project/lol-ia-coaching/decoder/ghidra_analysis.json", "w") as f:
    json.dump(results, f, indent=2)

print(f"\nResults saved to decoder/ghidra_analysis.json")
decompiler.dispose()
