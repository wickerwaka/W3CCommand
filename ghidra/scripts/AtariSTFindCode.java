// Recovers code that auto-analysis missed in the payload's code area. Candidates are
// 32-bit pointers anywhere in the payload, and undefined bytes after an unconditional
// flow break (rts/rte/jmp/bra). Each is validated with PseudoDisassembler before it is
// disassembled and made a function; repeats until no new code is found.
// Run headless with -postScript (Ghidra GUI must not have the project open).
//@category AtariST

import java.util.ArrayList;
import java.util.List;
import java.util.TreeSet;

import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.app.script.GhidraScript;
import ghidra.app.util.PseudoDisassembler;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressSet;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.Listing;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.mem.MemoryBlock;

public class AtariSTFindCode extends GhidraScript {

	private static final long CODE_START = 0x400;
	private static final long CODE_END = 0x22E62;   // first byte of static data

	@Override
	protected void run() throws Exception {
		Memory mem = currentProgram.getMemory();
		Listing listing = currentProgram.getListing();
		MemoryBlock payload = mem.getBlock("payload");
		PseudoDisassembler pdis = new PseudoDisassembler(currentProgram);

		int pass = 0;
		int totalFuncs = 0;
		while (!monitor.isCancelled()) {
			pass++;
			TreeSet<Long> candidates = new TreeSet<>();

			// Pointers stored anywhere in the payload (vector/jump/handler tables, move.l #imm).
			for (long a = payload.getStart().getOffset(); a + 4 <= payload.getEnd().getOffset() + 1; a += 2) {
				long v = mem.getInt(toAddr(a)) & 0xFFFFFFFFL;
				if (inCode(v) && (v & 1) == 0) {
					candidates.add(v);
				}
			}
			// Undefined bytes directly after an unconditional flow break.
			for (Instruction insn : listing.getInstructions(toAddr(CODE_START), true)) {
				if (insn.getAddress().getOffset() >= CODE_END) {
					break;
				}
				if (!insn.getFlowType().isFallthrough() && !insn.getFlowType().isCall()) {
					long next = insn.getMaxAddress().getOffset() + 1;
					if ((next & 1) == 0 && inCode(next)) {
						candidates.add(next);
					}
				}
			}

			List<Address> accepted = new ArrayList<>();
			for (long c : candidates) {
				Address a = toAddr(c);
				if (listing.getInstructionContaining(a) != null || listing.getDefinedDataContaining(a) != null) {
					continue;
				}
				if (pdis.isValidSubroutine(a, false)) {
					accepted.add(a);
				}
			}
			println(String.format("pass %d: %d candidates, %d valid new entries", pass, candidates.size(), accepted.size()));
			if (accepted.isEmpty()) {
				break;
			}
			for (Address a : accepted) {
				if (listing.getInstructionContaining(a) != null) {
					continue;  // swallowed by an earlier entry this pass
				}
				new DisassembleCommand(new AddressSet(a), null, true).applyTo(currentProgram, monitor);
				if (getFunctionAt(a) == null && createFunction(a, null) != null) {
					totalFuncs++;
				}
			}
			analyzeChanges(currentProgram);
		}

		long undefined = 0;
		for (long a = CODE_START; a < CODE_END; a++) {
			if (listing.getInstructionContaining(toAddr(a)) == null && listing.getDefinedDataContaining(toAddr(a)) == null) {
				undefined++;
			}
		}
		println(String.format("created %d functions; %d of %d code-area bytes still undefined",
			totalFuncs, undefined, CODE_END - CODE_START));
	}

	private static boolean inCode(long v) {
		return v >= CODE_START && v < CODE_END;
	}
}
