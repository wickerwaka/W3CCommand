"""Run the game's sound engine under Unicorn and log YM2149 register writes.

Engine: start a sound with $2019A (A0 = sound record), then once per VBL (50 Hz) the VBL
calls $20486, which runs the six channel scripts ($2020A) and maps them onto the three PSG
voices. When a channel uses the "buzz" mode, MFP Timer A is programmed ($FA19 control,
$FA1F data) and its interrupt ($20668) toggles PSG volumes using RNG output.
This harness steps $20486 every 20 ms and calls the Timer A handler at the rate implied by
TACR/TADR in between, timestamping every write to the PSG ($FF8800 select / $FF8802 data).
"""
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.m68k_const import UC_M68K_REG_A0, UC_M68K_REG_A6, UC_M68K_REG_A7

from stemu import RETURN, STACK, Emu
from stgfx import Payload

SOUND_INIT = 0x20188
SOUND_START = 0x2019A
SOUND_VBL = 0x20486
TIMER_A = 0x20668
TIMER_A_EXITS = (0x206DA, 0x2070A)   # the handler's two rte instructions
CHANNELS = 0x20466
MFP_CLOCK = 2_457_600
PRESCALE = [0, 4, 10, 16, 50, 64, 100, 200]
FRAME = 1 / 50


class SoundEmu(Emu):
    def __init__(self, p: Payload):
        super().__init__(p)
        self.uc.mem_map(0xFFFF0000, 0x10000)
        self.log, self.t, self.sel = [], 0.0, 0
        self.tacr, self.tadr = 0, 0
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self._io, begin=0xFFFF8000, end=0xFFFFFFFF)
        for a in TIMER_A_EXITS:
            self.uc.hook_add(UC_HOOK_CODE, lambda uc, addr, size, user: uc.emu_stop(), begin=a, end=a)
        self.call(SOUND_INIT, 0)

    def _io(self, uc, access, addr, size, value, user):
        data = (value & ((1 << (8 * size)) - 1)).to_bytes(size, "big")
        for k, b in enumerate(data):
            a = (addr + k) & 0xFFFF
            if a == 0x8800:
                self.sel = b & 15
            elif a == 0x8802:
                self.log.append((self.t, self.sel, b))
            elif a == 0xFA19:
                self.tacr = b & 7
            elif a == 0xFA1F:
                self.tadr = b

    def call_timer_a(self) -> None:
        """Run the Timer A handler up to (not including) one of its rte instructions; the
        handler restores its registers before them, so nothing else needs unwinding."""
        self.uc.reg_write(UC_M68K_REG_A7, STACK - 4)
        self.uc.emu_start(TIMER_A, 0xFFFFFFFF, count=200_000)

    def start(self, record: int) -> None:
        self.uc.reg_write(UC_M68K_REG_A0, record)
        self.uc.reg_write(UC_M68K_REG_A6, 0)
        self.uc.reg_write(UC_M68K_REG_A7, STACK - 4)
        self.w32(STACK - 4, RETURN)
        self.uc.emu_start(SOUND_START, RETURN, count=200_000)

    def active(self) -> bool:
        return any(self.r16s(self.p.u32(CHANNELS + 4 * i)) != 0 for i in range(6))

    def run(self, max_seconds: float = 8.0) -> float:
        """Step frames until all channels finish; returns the sound's length in seconds."""
        while self.t < max_seconds:
            frame_end = self.t + FRAME
            self.call(SOUND_VBL, 0)
            while self.tacr:
                period = PRESCALE[self.tacr] * (self.tadr or 256) / MFP_CLOCK
                if self.t + period >= frame_end:
                    break
                self.t += period
                self.call_timer_a()
            self.t = frame_end
            if not self.active():
                break
        return self.t


def render_sound(p: Payload, record: int, max_seconds: float = 8.0):
    e = SoundEmu(p)
    e.start(record)
    length = e.run(max_seconds)
    return e.log, length
