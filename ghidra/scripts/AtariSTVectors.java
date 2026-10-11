// Finds `move.l #handler,<vector>` stores into the exception/MFP vector table and
// creates functions named after the vector. Run as a headless -postScript.
//@category AtariST

import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.scalar.Scalar;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.SourceType;
import ghidra.program.model.symbol.Symbol;

public class AtariSTVectors extends GhidraScript {

	private static final long VECTOR_END = 0x140;

	@Override
	protected void run() throws Exception {
		MemoryBlock payload = currentProgram.getMemory().getBlock("payload");
		int found = 0;
		InstructionIterator it = currentProgram.getListing().getInstructions(payload.getStart(), true);
		while (it.hasNext() && !monitor.isCancelled()) {
			Instruction insn = it.next();
			if (!payload.contains(insn.getAddress())) {
				break;
			}
			if (!insn.getMnemonicString().toLowerCase().startsWith("move.l")) {
				continue;
			}
			for (Reference ref : insn.getReferencesFrom()) {
				long vec = ref.getToAddress().getOffset();
				if (ref.getOperandIndex() != 1 || vec >= VECTOR_END || (vec & 3) != 0) {
					continue;
				}
				Address handler = handlerSource(insn);
				if (handler == null || !payload.contains(handler)) {
					continue;
				}
				Symbol vecSym = getSymbolAt(ref.getToAddress());
				String name = (vecSym != null ? vecSym.getName().replaceFirst("^vec_", "") : "vec_" + Long.toHexString(vec))
					+ "_handler";
				println(String.format("%s: %s -> %s (%s)", insn.getAddress(), ref.getToAddress(), handler, name));
				installHandler(handler, name);
				found++;
			}
		}
		println("vector handler stores found: " + found);
	}

	// Handler address stored by `move.l #handler,vec` or `lea handler(pc),An; move.l An,vec`.
	private Address handlerSource(Instruction store) {
		Scalar imm = store.getScalar(0);
		if (imm != null) {
			return toAddr(imm.getUnsignedValue());
		}
		Register src = store.getRegister(0);
		Instruction prev = getInstructionBefore(store);
		if (src == null || prev == null || !prev.getMnemonicString().toLowerCase().startsWith("lea")
			|| !src.equals(prev.getRegister(1))) {
			return null;
		}
		Address target = prev.getAddress(0);
		if (target == null) {
			for (Reference r : prev.getOperandReferences(0)) {
				target = r.getToAddress();
			}
		}
		return target;
	}

	private void installHandler(Address handler, String name) throws Exception {
		if (getInstructionAt(handler) == null) {
			new DisassembleCommand(handler, null, true).applyTo(currentProgram, monitor);
		}
		Function f = getFunctionAt(handler);
		if (f == null) {
			f = createFunction(handler, name);
		}
		else if (f.getName().startsWith("FUN_")) {
			f.setName(name, SourceType.ANALYSIS);
		}
		else if (!f.getName().equals(name)) {
			f.setComment((f.getComment() == null ? "" : f.getComment() + "\n") + "also installed as " + name);
		}
	}
}
