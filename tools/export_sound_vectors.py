#!/usr/bin/env python3
"""Record test vectors for the TypeScript sound engine (web/src/audio/engine.ts).

Runs the original sound engine under Unicorn (stsound.SoundEmu) through scenarios: every
effect id alone, every graded explosion level at a range of distances, overlapping effects
(channel stealing, voice allocation, Timer A buzz switching, pause on/off), the per-pass
vehicle sounds ($20730: engine pitch from the bay records, proximity sound of the nearest other
vehicle) and seeded random fuzz. Each event records the PSG writes it made (MFP-cycle time,
register, value), the Timer A state and a CRC-32 of the engine state regions
(engine.ts STATE_REGIONS); each scenario ends with the full state bytes.

Also writes reference data for the synthesiser check: per-20 ms RMS of tools/ym2149.py renders
of a few effects, and the first noise LFSR bits.

    uv run python tools/export_sound_vectors.py      -> extracted/sound_vectors.json.gz
    cd web && npm run check-sound
"""
import gzip
import json
import math
import random
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stgfx import ROOT, Payload
from stsound import MFP_CLOCK, SoundEmu
from ym2149 import render_log

OUT = ROOT / "extracted" / "sound_vectors.json.gz"
STATE_REGIONS = [(0x4CBD4, 6 * 0x4A), (0x20482, 4), (0x20640, 0x16), (0x2070E, 8),
                 (0xA06, 0xA14 - 0xA06), (0xA16, 0xA28 - 0xA16), (0x1236, 8)]
OBJECTS, OBJ_SIZE = 0x5DCDA, 0x140
CARRIER = OBJECTS + 10 * OBJ_SIZE
TARGET = 0x61800                     # scratch object for explosions (outside the game's data)
ISLAND_ENTRY = 0x61900               # scratch island-buffer entry for $20C08


def obj(i: int) -> int:
    return OBJECTS + i * OBJ_SIZE


class Recorder:
    def __init__(self, p: Payload, name: str):
        self.e = SoundEmu(p, init=False)
        self.name, self.events = name, []
        self.ev("init")

    def state(self) -> bytes:
        return b"".join(bytes(self.e.uc.mem_read(a, n)) for a, n in STATE_REGIONS)

    def ev(self, op: str, *args) -> None:
        e = self.e
        if op == "init":
            w = e._logged(lambda: e.call(0x20188, 0))
        elif op == "poke":
            e.uc.mem_write(args[0], bytes.fromhex(args[1]))
            w = []
        else:
            w = {"frame": e.frame, "play": e.play, "start": e.start, "pass": e.vehicle_pass,
                 "reset": e.vehicle_reset, "off": e.off, "on": e.on,
                 "explosion": lambda lv, a: e.explosion(lv, a),
                 "explosionWord": lambda lv, a: e.explosion(lv, a, word=True)}[op](*args)
        t = e.timer
        self.events.append({"op": op, "a": list(args), "w": [x for wr in w for x in wr],
                            "t": [t.tacr, t.tdr, t.counter, t.phase, t.busy_until,
                                  -1 if t.pending is None else t.pending],
                            "c": zlib.crc32(self.state())})

    def poke(self, addr: int, data: bytes) -> None:
        self.ev("poke", addr, data.hex())

    def poke16(self, addr: int, v: int) -> None:
        self.poke(addr, (v & 0xFFFF).to_bytes(2, "big"))

    def poke32(self, addr: int, v: int) -> None:
        self.poke(addr, (v & 0xFFFFFFFF).to_bytes(4, "big"))

    def pos(self, addr: int, x: int, y: int, z: int) -> None:
        self.poke(addr + 0xA, b"".join((v & 0xFFFFFFFF).to_bytes(4, "big") for v in (x, y, z)))

    def frames(self, n: int) -> None:
        for _ in range(n):
            self.ev("frame")

    def until_quiet(self, limit: int = 300, tail: int = 4) -> None:
        for _ in range(limit):
            self.ev("frame")
            if not self.e.active():
                break
        self.frames(tail)

    def result(self) -> dict:
        return {"name": self.name, "events": self.events, "state": self.state().hex()}


def setup_vehicles(r: Recorder, rnd: random.Random | None = None) -> None:
    """Objects 2-5 Mantas (type 5), 6-9 Walruses (type 6), 10 the carrier."""
    for i in range(2, 11):
        a = obj(i)
        typ = 5 if i < 6 else 6 if i < 10 else 1
        r.poke(a, bytes([0, 1]) + typ.to_bytes(2, "big"))
        r.poke16(a + 0x1A, (i - 2) % 4)
        r.pos(a, 1000 * i, 160, 2000 * i)


def scenarios(p: Payload) -> list:
    out = []
    for i in range(14):
        r = Recorder(p, f"effect {i}")
        r.ev("play", i)
        r.until_quiet()
        out.append(r.result())

    for level in (0, 1, 2):
        for d in (0, 4999, 5000, 13191, 13192, 13193, 21384, 29576, 32767, 32768, -32768, -40000):
            r = Recorder(p, f"explosion level {level} dx {d}")
            r.poke32(0xA02, CARRIER)
            r.pos(CARRIER, 50000, 160, -70000)
            r.pos(TARGET, 50000 + d, 100, -70000 + d // 3)
            r.ev("explosion", level, TARGET)
            r.until_quiet()
            out.append(r.result())

    r = Recorder(p, "explosion gates and word form")
    r.ev("explosion", 2, TARGET)                       # no controlled object
    r.poke32(0xA02, CARRIER)
    r.pos(CARRIER, -1000, 160, 1000)
    r.pos(TARGET, 30000, 0, 0)
    r.poke(0x1249, b"\xff")
    r.ev("explosion", 2, TARGET)                       # sound effects off
    r.poke(0x1249, b"\x00")
    r.ev("explosion", 2, TARGET)
    r.frames(3)
    r.poke(0xA4A, b"".join(v.to_bytes(4, "big", signed=True) for v in (-20000, 0, 9000)))
    r.poke(ISLAND_ENTRY + 0xA, b"".join(v.to_bytes(2, "big", signed=True) for v in (-3000, 40, -12000)))
    r.ev("explosionWord", 1, ISLAND_ENTRY)
    r.until_quiet()
    out.append(r.result())

    r = Recorder(p, "game-over record")
    r.ev("start", 0x4B72E)
    r.until_quiet()
    out.append(r.result())

    # overlapping effects: more than 6 channels, stealing, buzz switching, pause
    r = Recorder(p, "overlap")
    r.poke32(0xA02, CARRIER)
    r.pos(CARRIER, 0, 160, 0)
    for i in (5, 12, 10, 0, 1, 4, 11, 9, 2):
        r.ev("play", i)
    r.frames(10)
    for lv, d in ((2, 0), (1, 9000), (0, 20000), (2, 30000)):
        r.pos(TARGET, d, 0, 0)
        r.ev("explosion", lv, TARGET)
        r.frames(3)
    r.ev("play", 6)
    r.frames(20)
    r.ev("off")
    r.frames(10)
    r.ev("play", 3)
    r.ev("on")
    r.frames(15)
    for i in range(14):
        r.ev("play", i)
        r.frames(2)
    r.until_quiet(400)
    out.append(r.result())

    # vehicle sounds
    r = Recorder(p, "vehicles")
    setup_vehicles(r)
    r.ev("reset")
    r.poke16(0x70C, 1)
    r.poke16(0x70E, 1)
    r.poke16(0xA14, 0)
    r.poke32(0xA02, obj(2))
    for k, speed in enumerate([0, 10, 40, 80, 120, 60, 20, -30, 0, 200, 300, 5]):
        r.poke16(0x496 + 6, speed)
        if k == 6:
            r.poke(0x496, b"\x01")
            r.poke(0x496 + 0x1C, b"\x05")
        if k == 8:
            r.poke(0x496, b"\x00")
        # another Manta approaching, then leaving
        r.pos(obj(3), 2000 + 3000 * abs(6 - k), 160, 4000)
        for _ in range(4):
            r.ev("pass")
            r.frames(3)
    r.ev("play", 2)
    r.poke16(0x70C, 0)
    r.ev("pass")
    r.frames(5)
    r.poke16(0xA14, 1)                                 # Walrus 1
    r.poke32(0xA02, obj(7))
    for k, speed in enumerate([0, 50, 100, 150, 100, 0, 250]):
        r.poke16(0x570 + 0x34 + 4, speed)
        r.pos(obj(6), 7000 + 2500 * k, 0, 14000 - 1500 * k)
        r.pos(obj(3), 1 << 20, 0, 0)
        for _ in range(5):
            r.ev("pass")
            r.frames(3)
    r.ev("play", 3)
    r.ev("explosion", 1, obj(6))
    for i in range(2, 10):
        r.poke(obj(i) + 1, b"\x00")                    # no other vehicle visible
    for _ in range(20):
        r.ev("pass")
        r.frames(2)
    r.poke16(0xA14, 2)                                 # carrier
    r.poke32(0xA02, CARRIER)
    r.ev("pass")
    r.frames(4)
    r.poke16(0xA14, 3)                                 # drone
    r.poke32(0xA02, obj(15))
    r.ev("pass")
    r.ev("pass")
    r.frames(4)
    r.poke(0x1248, b"\xff")
    r.poke16(0xA14, 0)
    r.poke32(0xA02, obj(2))
    r.ev("pass")
    r.frames(3)
    r.poke(0x1248, b"\x00")
    r.poke32(0xA02, 0)
    r.ev("pass")
    r.ev("reset")
    r.poke32(0xA02, obj(2))
    r.ev("pass")
    r.frames(30)
    out.append(r.result())

    for seed in range(6):
        out.append(fuzz(p, seed))
    return out


def fuzz(p: Payload, seed: int, frames: int = 1500) -> dict:
    rnd = random.Random(seed)
    r = Recorder(p, f"fuzz {seed}")
    setup_vehicles(r)
    r.poke16(0x70C, 1)
    r.poke16(0x70E, 1)
    rnd_pos = lambda: rnd.choice([rnd.randint(-40000, 40000), rnd.randint(-3000, 3000),
                                  rnd.randint(-(1 << 31), (1 << 31) - 1)])
    for _ in range(frames):
        n = rnd.random()
        if n < 0.08:
            r.ev("play", rnd.randrange(14))
        elif n < 0.12:
            a = obj(rnd.randrange(2, 11))
            r.ev("explosion", rnd.randrange(3), rnd.choice([a, TARGET]))
        elif n < 0.13:
            r.pos(TARGET, rnd_pos(), rnd_pos(), rnd_pos())
        elif n < 0.30:
            r.ev("pass")
        elif n < 0.33:
            i = rnd.randrange(2, 11)
            r.pos(obj(i), rnd_pos(), rnd.randint(0, 300), rnd_pos())
        elif n < 0.34:
            r.poke(obj(rnd.randrange(2, 10)) + 1, bytes([rnd.randrange(2)]))
        elif n < 0.36:
            kind = rnd.randrange(4)
            r.poke16(0xA14, kind)
            r.poke32(0xA02, rnd.choice([0, obj(2 + rnd.randrange(4)), obj(6 + rnd.randrange(4)),
                                        CARRIER, obj(15)]))
        elif n < 0.38:
            r.poke16(0x496 + 0x34 * rnd.randrange(4) + 6, rnd.randint(-50, 400))
            r.poke16(0x570 + 0x34 * rnd.randrange(4) + 4, rnd.randint(-50, 400))
        elif n < 0.385:
            r.poke16(rnd.choice([0x70C, 0x70E]), rnd.randrange(2))
        elif n < 0.388:
            r.poke(rnd.choice([0x1248, 0x1249]), bytes([rnd.choice([0, 0, 0xFF])]))
        elif n < 0.39:
            r.ev("reset")
        elif n < 0.392:
            r.ev(rnd.choice(["off", "on", "on"]))
        r.ev("frame")
    return r.result()


def synth_reference(p: Payload) -> dict:
    from stsound import render_sound
    ref = {}
    for rec in (0x4B5B6, 0x4B758, 0x4B7BE, 0x4B7E2, 0x4B65C, 0x4B72E, 0x4B888):
        log, length = render_sound(p, rec)
        rate = 44100
        s = render_log(log, length, rate)
        block = rate // 50
        mean = sum(s) / len(s)
        ref[f"{rec:05X}"] = {
            "log": [x for t, r_, v in log for x in (round(t * MFP_CLOCK), r_, v)],
            "length": length,
            "rms": [math.sqrt(sum((x - mean) ** 2 for x in s[i:i + block]) / block)
                    for i in range(0, len(s) - block + 1, block)]}
    lfsr, bits = 1, []
    for _ in range(4000):                                  # tools/ym2149.py's noise LFSR
        bit = (lfsr ^ (lfsr >> 3)) & 1
        lfsr = (lfsr >> 1) | (bit << 16)
        bits.append(lfsr & 1)
    return {"records": ref, "lfsr": "".join(map(str, bits))}


def main() -> None:
    p = Payload()
    data = {"scenarios": scenarios(p), "synth": synth_reference(p)}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT, "wt") as f:
        json.dump(data, f, separators=(",", ":"))
    n = sum(len(s["events"]) for s in data["scenarios"])
    w = sum(len(e["w"]) // 3 for s in data["scenarios"] for e in s["events"])
    print(f"{len(data['scenarios'])} scenarios, {n} events, {w} PSG writes -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
