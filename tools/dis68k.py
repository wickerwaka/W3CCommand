#!/usr/bin/env python3
"""Disassemble payload ranges with Capstone, independent of Ghidra's function bounds.

usage: uv run python tools/dis68k.py ADDR [LENGTH]    (hex addresses, e.g. 5A02 40)
Stops at the first rts/rte/jmp when LENGTH is omitted.
"""
import sys
from pathlib import Path

import capstone as cs

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stgfx import Payload

MD = cs.Cs(cs.CS_ARCH_M68K, cs.CS_MODE_BIG_ENDIAN | cs.CS_MODE_M68K_000)


def disasm(p: Payload, addr: int, length: int | None = None):
    for insn in MD.disasm(p.bytes(addr, length or 0x400), addr):
        yield insn
        if length is None and insn.mnemonic in ("rts", "rte", "jmp"):
            return


if __name__ == "__main__":
    start = int(sys.argv[1], 16)
    n = int(sys.argv[2], 16) if len(sys.argv) > 2 else None
    for i in disasm(Payload(), start, n):
        print(f"{i.address:05X}  {i.bytes.hex():<20} {i.mnemonic} {i.op_str}")
