"""Run payload routines under Unicorn (68000) to evaluate model vertex builders.

The game builds a model's vertices in two steps: the record's basis routine (second long
of the body), then the vertex-op interpreter at $5A02. Both are called here with
A1 = vertex buffer, A4 = a zeroed object and $9FA = a zeroed camera, so all rotations
are identity and the result is in model space. Object fields and globals (turret angles,
lift positions, animation values) can be poked before the call.
"""
from unicorn import UC_ARCH_M68K, UC_MODE_BIG_ENDIAN, Uc, UcError
from unicorn.m68k_const import (UC_CPU_M68K_M68000, UC_M68K_REG_A0, UC_M68K_REG_A1, UC_M68K_REG_A4,
                                UC_M68K_REG_A7, UC_M68K_REG_PC)

from stgfx import PAYLOAD_BASE, Payload

VERTEX_BUILDER = 0x5A02
CAMERA_SETUP = (0xA484, 0xA494, 0xA4A4)
VBUF = 0x60000        # vertex array V (6 bytes per entry, indices 0..$FF)
OBJ = 0x61000         # fake object passed in A4
CAM = 0x61400         # fake camera object (*$9FA)
STACK = 0x7F000
RETURN = 0x7FF00      # return address that stops emulation
UNSET = (0x7FFF, 0x7FFF, 0x7FFF)


class Emu:
    def __init__(self, p: Payload):
        self.p = p
        self.uc = Uc(UC_ARCH_M68K, UC_MODE_BIG_ENDIAN)
        self.uc.ctl_set_cpu_model(UC_CPU_M68K_M68000)
        self.uc.mem_map(0, 0x100000)
        self.uc.mem_write(PAYLOAD_BASE, p.data)
        self.w32(0x9FA, CAM)
        # Per-frame camera rotation (sin, cos) pairs at $A74..$A7E, normally set by the
        # 3D view before drawing (see scene_collect_visible).
        for setup in CAMERA_SETUP:
            self.call(setup, 0)
        self.clear_vertices()

    def w16(self, addr: int, v: int) -> None:
        self.uc.mem_write(addr, (v & 0xFFFF).to_bytes(2, "big"))

    def w32(self, addr: int, v: int) -> None:
        self.uc.mem_write(addr, (v & 0xFFFFFFFF).to_bytes(4, "big"))

    def r16s(self, addr: int) -> int:
        return int.from_bytes(self.uc.mem_read(addr, 2), "big", signed=True)

    def clear_vertices(self) -> None:
        self.uc.mem_write(VBUF, b"\x7f\xff" * 3 * 256)

    def call(self, addr: int, a0: int) -> int:
        """Call a routine with the model register setup; returns A0 afterwards."""
        self.uc.reg_write(UC_M68K_REG_A0, a0)
        self.uc.reg_write(UC_M68K_REG_A1, VBUF)
        self.uc.reg_write(UC_M68K_REG_A4, OBJ)
        self.uc.reg_write(UC_M68K_REG_A7, STACK - 4)
        self.w32(STACK - 4, RETURN)
        try:
            self.uc.emu_start(addr, RETURN, count=2_000_000)
        except UcError as e:
            pc = self.uc.reg_read(UC_M68K_REG_PC)
            raise RuntimeError(f"emulating ${addr:05X}: {e} at PC=${pc:05X}") from e
        if self.uc.reg_read(UC_M68K_REG_PC) != RETURN:
            raise RuntimeError(f"emulating ${addr:05X}: did not return")
        return self.uc.reg_read(UC_M68K_REG_A0)

    def vertex(self, i: int):
        v = tuple(self.r16s(VBUF + 6 * i + 2 * k) for k in range(3))
        return None if v == UNSET else v

    def build_vertices(self, basis: int, params: int) -> int:
        """Run basis routine (params = address of its parameters) and the vertex ops.
        Returns the address after the vertex-op stream (the nverts word)."""
        a0 = self.call(basis, params)
        return self.call(VERTEX_BUILDER, a0)
