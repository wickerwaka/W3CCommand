"""Run the game's sound engine under Unicorn and log YM2149 register writes.

Engine (docs/memory-map.md "Sound"): `$2019A` starts a sound record (A0) on one of six
channels; `jsr $20B90` + inline id plays an entry of the effect table `$20BD0`; `$20C30` /
`$20C08` play one of nine distance-graded explosions. Once per VBL (50 Hz) the VBL handler
calls `$20486`, which runs the channel scripts (`$2020A`), gives the three PSG voices to the
highest-priority sub-voices and writes the PSG. A voice with noise enabled may switch on the
"buzz" mode: MFP Timer A (prescaler from bits 5-7 of the noise period byte) interrupts at
`$20668`, which advances the RNG at `$1236`, reloads the timer data register from it and
toggles the volumes of the noise voices on and off. Once per game pass `game_main` calls
`$20730`: the engine sound of the controlled Manta/Walrus and the sound of the nearest other
vehicle.

Timing model (shared with web/src/audio/engine.ts, all in integer MFP clock cycles, 2.4576 MHz,
49152 per 50 Hz frame):
- the VBL routine runs at the start of each frame; Timer A interrupts follow inside the frame;
- Timer A is a free-running MFP delay-mode timer: the main counter decrements once per
  prescaler period and, on reaching 0, reloads from the data register and interrupts. The
  handler's write to the data register therefore sets the period after the next one (the
  counter has already reloaded). Changing the control register restarts the prescaler; a stopped
  timer keeps its count;
- the handler takes HANDLER_CYCLES (about 430 CPU cycles at 8 MHz); a timeout during that time
  leaves one pending interrupt that is taken when the handler ends (more timeouts collapse into
  it), as the MFP's in-service bit (software end-of-interrupt) holds Timer A off until then.
"""
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.m68k_const import (UC_M68K_REG_A0, UC_M68K_REG_A1, UC_M68K_REG_A6, UC_M68K_REG_A7,
                                UC_M68K_REG_D0)

from stemu import RETURN, STACK, Emu
from stgfx import Payload

SOUND_INIT = 0x20188
SOUND_START = 0x2019A
SOUND_VBL = 0x20486
SOUND_OFF, SOUND_ON = 0x20434, 0x2045E
VEHICLE_RESET = 0x20716          # engine/proximity sound state reset + $20188
VEHICLE_PASS = 0x20730           # per game pass: engine sound $2073A, proximity $2092A
PLAY_EFFECT = 0x20B90
EXPLOSION_LONG, EXPLOSION_WORD = 0x20C30, 0x20C08
TIMER_A = 0x20668
TIMER_A_EXITS = (0x206DA, 0x2070A)   # the handler's two rte instructions
CHANNELS = 0x20466
CHANNEL_BASE, CHANNEL_SIZE = 0x4CBD4, 0x4A
STUB = 0x7E000                   # scratch for `jsr $20B90; dc.w id; rts`

MFP_CLOCK = 2_457_600
FRAME_CYCLES = MFP_CLOCK // 50
HANDLER_CYCLES = 132
PRESCALE = [0, 4, 10, 16, 50, 64, 100, 200]
INF = 1 << 62


class TimerA:
    """MFP Timer A in delay mode (see the module docstring). Times are absolute MFP cycles."""

    def __init__(self):
        self.tacr = 0
        self.tdr = 0            # data register (0 = 256)
        self.counter = 256      # main counter
        self.phase = 0          # cycles until the next decrement
        self.busy_until = 0
        self.pending = None     # time the pending interrupt will be taken

    def set_control(self, v: int) -> None:
        v &= 7
        if v != self.tacr:
            self.tacr = v
            self.phase = PRESCALE[v]
            self.pending = None
        if not v:
            self.pending = None

    def set_data(self, v: int) -> None:
        self.tdr = v & 0xFF
        if not self.tacr:
            self.counter = self.tdr or 256

    def _advance(self, cycles: int) -> None:
        if not self.tacr or cycles <= 0:
            return
        p = PRESCALE[self.tacr]
        if cycles < self.phase:
            self.phase -= cycles
        else:
            e = cycles - self.phase
            self.counter -= 1 + e // p
            self.phase = p - e % p

    def run(self, now: int, end: int, handler) -> int:
        """Run interrupts in [now, end); handler(t) runs the routine at time t."""
        while True:
            tt = now + self.phase + (self.counter - 1) * PRESCALE[self.tacr] if self.tacr else INF
            ts = self.pending if self.pending is not None else INF
            if min(tt, ts) >= end:
                self._advance(end - now)
                return end
            if ts <= tt:
                self._advance(ts - now)
                now, self.pending = ts, None
                self.busy_until = now + HANDLER_CYCLES
                handler(now)
            else:
                now = tt
                self.counter, self.phase = self.tdr or 256, PRESCALE[self.tacr]
                if now >= self.busy_until:
                    self.busy_until = now + HANDLER_CYCLES
                    handler(now)
                elif self.pending is None:
                    self.pending = self.busy_until


class SoundEmu(Emu):
    def __init__(self, p: Payload, init: bool = True):
        super().__init__(p)
        self.uc.mem_map(0xFFFF0000, 0x10000)
        self.log, self.now, self.sel = [], 0, 0
        self.timer = TimerA()
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self._io, begin=0xFFFF8000, end=0xFFFFFFFF)
        for a in TIMER_A_EXITS:
            self.uc.hook_add(UC_HOOK_CODE, lambda uc, addr, size, user: uc.emu_stop(), begin=a, end=a)
        if init:
            self.call(SOUND_INIT, 0)

    def _io(self, uc, access, addr, size, value, user):
        data = (value & ((1 << (8 * size)) - 1)).to_bytes(size, "big")
        for k, b in enumerate(data):
            a = (addr + k) & 0xFFFF
            if a == 0x8800:
                self.sel = b & 15
            elif a == 0x8802:
                self.log.append((self.now, self.sel, b))
            elif a == 0xFA19:
                self.timer.set_control(b)
            elif a == 0xFA1F:
                self.timer.set_data(b)

    def run_at(self, addr: int, **regs) -> None:
        """Call a routine (regs: d0, a0, a1, a6) to its rts."""
        names = {"d0": UC_M68K_REG_D0, "a0": UC_M68K_REG_A0, "a1": UC_M68K_REG_A1, "a6": UC_M68K_REG_A6}
        for k in names:
            self.uc.reg_write(names[k], regs.get(k, 0) & 0xFFFFFFFF)
        self.uc.reg_write(UC_M68K_REG_A7, STACK - 4)
        self.w32(STACK - 4, RETURN)
        self.uc.emu_start(addr, RETURN, count=2_000_000)

    def timer_a(self, t: int) -> None:
        """The Timer A handler, run up to its rte (it restores its registers before it)."""
        self.now = t
        self.uc.reg_write(UC_M68K_REG_A7, STACK - 4)
        self.uc.emu_start(TIMER_A, 0xFFFFFFFF, count=200_000)

    # ---- scenario operations: each returns the PSG writes it made ----------------------------
    def _logged(self, fn) -> list:
        start = len(self.log)
        fn()
        return self.log[start:]

    def frame(self) -> list:
        """One VBL: the VBL routine at the frame start, then Timer A until the next frame."""
        def go():
            t0 = self.now
            self.call(SOUND_VBL, 0)
            self.now = self.timer.run(t0, t0 + FRAME_CYCLES, self.timer_a)
        return self._logged(go)

    def play(self, effect: int) -> list:
        self.uc.mem_write(STUB, bytes.fromhex("4EB9") + PLAY_EFFECT.to_bytes(4, "big")
                          + effect.to_bytes(2, "big") + bytes.fromhex("4E75"))
        return self._logged(lambda: self.run_at(STUB))

    def start(self, record: int) -> list:
        return self._logged(lambda: self.run_at(SOUND_START, a0=record))

    def explosion(self, level: int, obj: int, word: bool = False) -> list:
        return self._logged(lambda: self.run_at(EXPLOSION_WORD if word else EXPLOSION_LONG,
                                                d0=level, a1=obj))

    def vehicle_pass(self) -> list:
        return self._logged(lambda: self.run_at(VEHICLE_PASS))

    def vehicle_reset(self) -> list:
        return self._logged(lambda: self.run_at(VEHICLE_RESET))

    def off(self) -> list:
        return self._logged(lambda: self.run_at(SOUND_OFF))

    def on(self) -> list:
        return self._logged(lambda: self.run_at(SOUND_ON))

    def active(self) -> bool:
        return any(self.r16s(self.p.u32(CHANNELS + 4 * i)) != 0 for i in range(6))

    def run(self, max_seconds: float = 8.0) -> float:
        """Step frames until all channels finish; returns the sound's length in seconds."""
        while self.now < max_seconds * MFP_CLOCK:
            self.frame()
            if not self.active():
                break
        return self.now / MFP_CLOCK


def render_sound(p: Payload, record: int, max_seconds: float = 8.0):
    """(log of (seconds, reg, value), length in seconds) for one record played alone."""
    e = SoundEmu(p)
    e.start(record)
    length = e.run(max_seconds)
    return [(t / MFP_CLOCK, r, v) for t, r, v in e.log], length
