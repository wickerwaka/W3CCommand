"""Text tracer for the 2D screens: run a screen's render routine ([$144A0 + 4*screen]) or its
panel list ([$43856 + 4*screen] through $B400, which calls each shown panel's draw callback)
under Unicorn on a 512 KB RAM image, and log every character drawn (5x5 $9AD2, 8x8 $9C94,
vector-font strings $9CEE), grouped into strings (font, x, y, text). The stack lives above
512 KB so drawing into the screen buffers can't clobber it. Used by tools/check_readouts.py
and the stscreens_* mirrors (docs/units.md section 10.7).

  uv run python tools/stscreens.py RAMFILE [SCREEN] [--panels] [--call ADDR]
"""
import sys
from pathlib import Path

from unicorn import UC_ARCH_M68K, UC_MODE_BIG_ENDIAN, UC_HOOK_CODE, Uc, UcError
from unicorn.m68k_const import (UC_CPU_M68K_M68000, UC_M68K_REG_A7, UC_M68K_REG_PC, UC_M68K_REG_SR,
                                UC_M68K_REG_D0, UC_M68K_REG_D1, UC_M68K_REG_D2, UC_M68K_REG_D3,
                                UC_M68K_REG_A0, UC_M68K_REG_A6, UC_M68K_REG_A5)

STACK, RETURN = 0xF0000, 0xFFF00
RENDER, PANELS = 0x144A0, 0x43856


class Tracer:
    def __init__(self, ram: bytes):
        uc = self.uc = Uc(UC_ARCH_M68K, UC_MODE_BIG_ENDIAN)
        uc.ctl_set_cpu_model(UC_CPU_M68K_M68000)
        uc.mem_map(0, 0x100000)
        uc.mem_map(0xFF0000, 0x10000)
        uc.mem_map(0xFFFF0000, 0x10000)
        uc.mem_write(0, bytes(ram[:0x80000]))
        self.chars = []          # (font, x, y, ch)
        self.calls = []          # (addr, regs) for watched addresses
        self.watch = {}
        uc.hook_add(UC_HOOK_CODE, lambda u, a, s, d: self._c5(), begin=0x9AD2, end=0x9AD2)
        uc.hook_add(UC_HOOK_CODE, lambda u, a, s, d: self._c8(), begin=0x9C94, end=0x9C94)
        uc.hook_add(UC_HOOK_CODE, lambda u, a, s, d: self._cv(), begin=0x9CEE, end=0x9CEE)
        # trapv after divs in the polygon clipper: Unicorn traps with V clear
        for a in (0x4CE2, 0x4D0A):
            uc.hook_add(UC_HOOK_CODE, lambda u, a_, s, d: self._trapv(), begin=a, end=a)

    def r(self, reg):
        return self.uc.reg_read(reg)

    def _trapv(self):
        self.uc.reg_write(UC_M68K_REG_PC, self.r(UC_M68K_REG_PC) + 2)

    def _c5(self):
        self.chars.append(("5", self.r(UC_M68K_REG_D1) & 0xFFFF, self.r(UC_M68K_REG_D2) & 0xFFFF,
                           chr(self.r(UC_M68K_REG_D0) & 0xFF)))

    def _c8(self):
        self.chars.append(("8", self.r(UC_M68K_REG_D1) & 0xFFFF, self.r(UC_M68K_REG_D2) & 0xFFFF,
                           chr(self.r(UC_M68K_REG_D0) & 0xFF)))

    def _cv(self):
        a = self.r(UC_M68K_REG_A0)
        s = bytes(self.uc.mem_read(a, 64)).split(b"\0")[0]
        s = "".join(chr(c) for c in s if c >= 0x20)
        self.chars.append(("v", self.r(UC_M68K_REG_D1) & 0xFFFF, self.r(UC_M68K_REG_D2) & 0xFFFF, s))

    def call(self, addr: int, **regs) -> None:
        from unicorn import m68k_const as K
        uc = self.uc
        for k, v in regs.items():
            uc.reg_write(getattr(K, f"UC_M68K_REG_{k.upper()}"), v)
        uc.reg_write(UC_M68K_REG_SR, 0x2700)
        uc.reg_write(UC_M68K_REG_A7, STACK - 4)
        uc.mem_write(STACK - 4, RETURN.to_bytes(4, "big"))
        try:
            uc.emu_start(addr, RETURN, count=30_000_000)
        except UcError as e:
            raise RuntimeError(f"${addr:05X}: {e} at PC=${uc.reg_read(UC_M68K_REG_PC):05X}") from e
        if uc.reg_read(UC_M68K_REG_PC) != RETURN:
            raise RuntimeError(f"${addr:05X} did not return")

    def u16(self, a): return int.from_bytes(self.uc.mem_read(a, 2), "big")
    def u32(self, a): return int.from_bytes(self.uc.mem_read(a, 4), "big")

    def render(self, screen: int):
        self.call(self.u32(RENDER + 4 * screen))

    def panels(self, screen: int):
        self.call(0xB400, a6=self.u32(PANELS + 4 * screen))

    def strings(self):
        """Group characters into strings: same font and y, x continuing the previous char."""
        out = []
        for f, x, y, ch in self.chars:
            if f == "v":
                out.append((f, x, y, ch))
                continue
            step = 6 if f == "5" else 1
            if out and out[-1][0] == f and out[-1][2] == y and out[-1][4] == x:
                o = out[-1]
                out[-1] = (f, o[1], y, o[3] + ch, x + step)
            else:
                out.append((f, x, y, ch, x + step))
        return [(f, x, y, s) for f, x, y, s, *_ in out]


if __name__ == "__main__":
    ram = Path(sys.argv[1]).read_bytes()
    t = Tracer(ram)
    screen = int(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith("-") else t.u16(0x718)
    if "--call" in sys.argv:
        t.call(int(sys.argv[sys.argv.index("--call") + 1], 16))
    elif "--panels" in sys.argv:
        t.panels(screen)
    else:
        t.render(screen)
    for s in t.strings():
        print(s)
