// Ghidra script to decompile key functions in LoL binary
// @category Analysis
// @author lol-ia-coaching

import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.listing.Function;
import ghidra.program.model.address.Address;
import java.io.FileWriter;
import java.io.PrintWriter;

public class AnalyzeDecrypt extends GhidraScript {

    private static final long BASE = 0x140000000L;

    // Key RVAs
    private static final long[] RVAS = {
        0x1446636,  // packet dispatch switch
        0x1446d4a,  // packet 0x1c handler
        0x1446da4,  // default handler
        0x14495f0,  // stack reader
        0x14425b0,  // stack builder
        0x1444dbd,  // type byte reader
        0x140b680,  // payload decoder
    };

    private static final String[] NAMES = {
        "packet_dispatch_switch",
        "packet_0x1c_handler",
        "packet_default_handler",
        "stack_reader",
        "stack_builder",
        "type_byte_reader",
        "payload_decoder",
    };

    @Override
    public void run() throws Exception {
        DecompInterface decomp = new DecompInterface();
        decomp.openProgram(currentProgram);

        String outPath = "/home/susini/project/lol-ia-coaching/decoder/ghidra_decompiled.txt";
        PrintWriter out = new PrintWriter(new FileWriter(outPath));

        for (int i = 0; i < RVAS.length; i++) {
            long rva = RVAS[i];
            String name = NAMES[i];
            Address addr = currentProgram.getAddressFactory()
                .getDefaultAddressSpace().getAddress(BASE + rva);

            Function func = getFunctionContaining(addr);
            if (func == null) {
                func = currentProgram.getFunctionManager().getFunctionAt(addr);
            }

            out.println("============================================================");
            out.println("=== " + name + " at RVA 0x" + Long.toHexString(rva) + " ===");

            if (func != null) {
                out.println("Function: " + func.getName() + " at " + func.getEntryPoint());
                out.println("Size: " + func.getBody().getNumAddresses());

                DecompileResults result = decomp.decompileFunction(func, 60, monitor);
                if (result != null && result.decompileCompleted()) {
                    String code = result.getDecompiledFunction().getC();
                    // Print first 300 lines
                    String[] lines = code.split("\n");
                    int maxLines = Math.min(lines.length, 300);
                    for (int j = 0; j < maxLines; j++) {
                        out.println(lines[j]);
                    }
                    if (lines.length > 300) {
                        out.println("... (" + lines.length + " total lines)");
                    }

                    println(name + ": decompiled " + lines.length + " lines");
                } else {
                    out.println("DECOMPILATION FAILED");
                    println(name + ": decompilation failed");
                }
            } else {
                out.println("NO FUNCTION FOUND");
                println(name + ": no function at this address");
            }
            out.println();
        }

        out.close();
        decomp.dispose();
        println("Results saved to " + outPath);
    }
}
