// Builds an Atari ST memory map around the CARRIER.TOS payload (raw import at $400).
// Run as a headless -preScript so auto-analysis sees the full map.
//@category AtariST

import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.data.ByteDataType;
import ghidra.program.model.data.DataType;
import ghidra.program.model.data.PointerDataType;
import ghidra.program.model.data.WordDataType;
import ghidra.program.model.data.DataUtilities;
import ghidra.program.model.data.DataUtilities.ClearDataMode;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.symbol.SourceType;
import ghidra.program.model.symbol.SymbolTable;

public class AtariSTMemoryMap extends GhidraScript {

	private static final long PAYLOAD_START = 0x400;
	private static final long RAM_END = 0x80000;          // 512 KB ST
	private static final long LOADER_BASE = 0x00F00000;   // unused on ST; holds the GEMDOS loader stub
	private static final long IO_START = 0xFF8000;
	private static final long IO_MIRROR = 0xFFFF8000L;    // sign-extended abs.w view of I/O
	private static final long IO_SIZE = 0x8000;

	private static final String[] EXCEPTION_VECTORS = {
		"vec_reset_ssp", "vec_reset_pc", "vec_bus_error", "vec_address_error",
		"vec_illegal", "vec_zero_divide", "vec_chk", "vec_trapv",
		"vec_privilege", "vec_trace", "vec_line_a", "vec_line_f",
	};

	private static final String[] AUTOVECTORS = {
		"vec_spurious", "vec_level1", "vec_hbl", "vec_level3",
		"vec_vbl", "vec_level5", "vec_level6_mfp", "vec_nmi",
	};

	private static final String[] MFP_VECTORS = {
		"mfp_vec_centronics_busy", "mfp_vec_rs232_dcd", "mfp_vec_rs232_cts", "mfp_vec_blitter",
		"mfp_vec_timer_d", "mfp_vec_timer_c", "mfp_vec_acia", "mfp_vec_fdc_hdc",
		"mfp_vec_timer_b", "mfp_vec_send_error", "mfp_vec_send_empty", "mfp_vec_recv_error",
		"mfp_vec_recv_full", "mfp_vec_timer_a", "mfp_vec_rs232_ri", "mfp_vec_mono_detect",
	};

	// offset from $FF8000, name, size in bytes
	private static final Object[][] IO_REGS = {
		{ 0x0001, "MMU_memconf", 1 },
		{ 0x0201, "VID_base_hi", 1 },
		{ 0x0203, "VID_base_mid", 1 },
		{ 0x0205, "VID_count_hi", 1 },
		{ 0x0207, "VID_count_mid", 1 },
		{ 0x0209, "VID_count_lo", 1 },
		{ 0x020A, "VID_sync_mode", 1 },
		{ 0x020D, "VID_base_lo_ste", 1 },
		{ 0x0260, "VID_shift_mode", 1 },
		{ 0x0265, "VID_hscroll_ste", 1 },
		{ 0x0604, "DMA_data", 2 },
		{ 0x0606, "DMA_mode_status", 2 },
		{ 0x0609, "DMA_addr_hi", 1 },
		{ 0x060B, "DMA_addr_mid", 1 },
		{ 0x060D, "DMA_addr_lo", 1 },
		{ 0x0800, "PSG_select", 1 },
		{ 0x0802, "PSG_write", 1 },
		{ 0x7A01, "MFP_gpip", 1 },
		{ 0x7A03, "MFP_aer", 1 },
		{ 0x7A05, "MFP_ddr", 1 },
		{ 0x7A07, "MFP_iera", 1 },
		{ 0x7A09, "MFP_ierb", 1 },
		{ 0x7A0B, "MFP_ipra", 1 },
		{ 0x7A0D, "MFP_iprb", 1 },
		{ 0x7A0F, "MFP_isra", 1 },
		{ 0x7A11, "MFP_isrb", 1 },
		{ 0x7A13, "MFP_imra", 1 },
		{ 0x7A15, "MFP_imrb", 1 },
		{ 0x7A17, "MFP_vr", 1 },
		{ 0x7A19, "MFP_tacr", 1 },
		{ 0x7A1B, "MFP_tbcr", 1 },
		{ 0x7A1D, "MFP_tcdcr", 1 },
		{ 0x7A1F, "MFP_tadr", 1 },
		{ 0x7A21, "MFP_tbdr", 1 },
		{ 0x7A23, "MFP_tcdr", 1 },
		{ 0x7A25, "MFP_tddr", 1 },
		{ 0x7A27, "MFP_scr", 1 },
		{ 0x7A29, "MFP_ucr", 1 },
		{ 0x7A2B, "MFP_rsr", 1 },
		{ 0x7A2D, "MFP_tsr", 1 },
		{ 0x7A2F, "MFP_udr", 1 },
		{ 0x7C00, "ACIA_kbd_ctrl", 1 },
		{ 0x7C02, "ACIA_kbd_data", 1 },
		{ 0x7C04, "ACIA_midi_ctrl", 1 },
		{ 0x7C06, "ACIA_midi_data", 1 },
	};

	@Override
	protected void run() throws Exception {
		Memory mem = currentProgram.getMemory();

		MemoryBlock payload = mem.getBlock(addr(PAYLOAD_START));
		if (payload == null || !payload.getStart().equals(addr(PAYLOAD_START))) {
			throw new IllegalStateException("expected raw import based at $400");
		}
		payload.setName("payload");
		payload.setPermissions(true, true, true);
		long payloadEnd = payload.getEnd().getOffset() + 1;

		MemoryBlock vectors = mem.createUninitializedBlock("vectors", addr(0), PAYLOAD_START, false);
		vectors.setPermissions(true, true, false);

		MemoryBlock ramHi = mem.createUninitializedBlock("ram_hi", addr(payloadEnd), RAM_END - payloadEnd, false);
		ramHi.setPermissions(true, true, true);

		File repoRoot = getSourceFile().getFile(false).getParentFile().getParentFile().getParentFile();
		File loaderFile = new File(repoRoot, "extracted/loader.bin");
		try (InputStream in = new FileInputStream(loaderFile)) {
			MemoryBlock loader = mem.createInitializedBlock("loader", addr(LOADER_BASE), in,
				loaderFile.length(), monitor, false);
			loader.setPermissions(true, false, true);
			loader.setComment("GEMDOS text segment of CARRIER.TOS; copies file $400.. to $400 and jumps there");
		}

		MemoryBlock io = mem.createUninitializedBlock("io", addr(IO_START), IO_SIZE, false);
		io.setPermissions(true, true, false);
		io.setVolatile(true);
		MemoryBlock mirror = mem.createByteMappedBlock("io_absw", addr(IO_MIRROR), addr(IO_START), IO_SIZE, false);
		mirror.setPermissions(true, true, false);
		mirror.setVolatile(true);
		mirror.setComment("Sign-extended abs.w alias of $FF8000-$FFFFFF");

		labelVectors();
		labelIo(IO_START);
		labelIo(IO_MIRROR);

		SymbolTable st = currentProgram.getSymbolTable();
		st.addExternalEntryPoint(addr(PAYLOAD_START));
		st.createLabel(addr(PAYLOAD_START), "payload_entry", SourceType.IMPORTED);
		st.addExternalEntryPoint(addr(LOADER_BASE));
		st.createLabel(addr(LOADER_BASE), "tos_loader_entry", SourceType.IMPORTED);
		disassemble(addr(PAYLOAD_START));
		disassemble(addr(LOADER_BASE));
		createFunction(addr(LOADER_BASE), "tos_loader_entry");
	}

	private void labelVectors() throws Exception {
		DataType ptr = new PointerDataType(null, 4);
		for (int i = 0; i < EXCEPTION_VECTORS.length; i++) {
			labelData(i * 4L, EXCEPTION_VECTORS[i], ptr);
		}
		for (int i = 0; i < AUTOVECTORS.length; i++) {
			labelData(0x60 + i * 4L, AUTOVECTORS[i], ptr);
		}
		for (int i = 0; i < 16; i++) {
			labelData(0x80 + i * 4L, "vec_trap" + i, ptr);
		}
		for (int i = 0; i < MFP_VECTORS.length; i++) {
			labelData(0x100 + i * 4L, MFP_VECTORS[i], ptr);
		}
	}

	private void labelIo(long base) throws Exception {
		for (Object[] reg : IO_REGS) {
			long off = ((Integer) reg[0]).longValue();
			int size = (Integer) reg[2];
			labelData(base + off, (String) reg[1], size == 2 ? WordDataType.dataType : ByteDataType.dataType);
		}
		for (int i = 0; i < 16; i++) {
			labelData(base + 0x240 + i * 2L, "VID_palette" + i, WordDataType.dataType);
		}
	}

	private void labelData(long offset, String name, DataType dt) throws Exception {
		Address a = addr(offset);
		currentProgram.getSymbolTable().createLabel(a, name, SourceType.IMPORTED);
		DataUtilities.createData(currentProgram, a, dt, -1, ClearDataMode.CLEAR_ALL_CONFLICT_DATA);
	}

	private Address addr(long offset) {
		return currentProgram.getAddressFactory().getDefaultAddressSpace().getAddress(offset);
	}
}
