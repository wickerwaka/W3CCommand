"""Group B readouts: screens 4 messages, 11 defence radar, 12 weapons status, 32 game status
(overview + options), 34 game over, 35 enemy carrier destroyed.

Structured readers (mirrored in TS) + expected draw lists, compared with what the original
draws under Unicorn (Tracer from stscreens.py). usage: B_misc.py [--fuzz N]
"""
import random
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stscreens import Tracer  # noqa: E402

from unicorn import UC_HOOK_CODE  # noqa: E402
from unicorn.m68k_const import UC_M68K_REG_D0, UC_M68K_REG_D1, UC_M68K_REG_D2, UC_M68K_REG_D3, UC_M68K_REG_D4  # noqa: E402

from stgfx import ROOT  # noqa: E402


def u8(r, a): return r[a]
def u16(r, a): return struct.unpack_from(">H", r, a)[0]
def s16(r, a): return struct.unpack_from(">h", r, a)[0]
def u32(r, a): return struct.unpack_from(">I", r, a)[0]


def tdiv(a, b):
    """divs/divu truncation towards zero."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q


# ---- number formatting --------------------------------------------------------------------

def digits(v, n):
    """$9FAA/$9FB6/$9FC2/$9FCE: n digits by repeated subtraction (unsigned word); the first
    digit is not limited to 9 (byte '0' + quotient)."""
    v &= 0xFFFF
    out = []
    for p in [10 ** k for k in range(n - 1, 0, -1)]:
        q = v // p
        out.append(chr((0x30 + q) & 0xFF))
        v -= q * p
    out.append(chr(0x30 + v))
    return "".join(out)


def blank(s, n):
    """$9FFE / the inline `cmpi.b #'0'`: up to n leading '0' -> ' '."""
    s = list(s)
    for i in range(n):
        if s[i] != "0":
            break
        s[i] = " "
    return "".join(s)


def percent_value(cur, mx):
    return ((cur & 0xFFFF) * 100 // mx) & 0xFFFF if mx else 0


def percent_text(cur, mx):
    """$EC4A + '%': "100%" only at exactly 100, else tens blank when 0 (" 5%" -> "  5%")."""
    p = percent_value(cur, mx)
    if p == 100:
        return "100%"
    t, o = p // 10, p % 10
    return " " + (chr((0x30 + t) & 0xFF) if t else " ") + chr(0x30 + o) + "%"


# ---- 5x5 / 8x8 text expansion (what the tracer logs) -------------------------------------

def text5(x, y, s):
    """$9A56: chars >= $20 drawn at x += 6; 5 n = colour; 6 = skip spaces; other control ends."""
    out, i = [], 0
    b = s.encode("latin-1") if isinstance(s, str) else s
    while i < len(b):
        c = b[i]; i += 1
        if c >= 0x20:
            out.append(("5", x, y, chr(c))); x += 6
        elif c == 5:
            i += 1
        elif c == 6:
            while i < len(b) and b[i] == 0x20:
                i += 1
            if i >= len(b) or b[i] == 0:
                break
        else:
            break
    return out


class Text8:
    """$9BC0/$9BE2 stream interpreter: 0 end, 1 x, 2 y, 3 x y, 4 margin = x, 5 colour, 6 skip
    spaces, 13 newline (x = margin, y += 8), others drawn as 8x8 cells (x += 1)."""

    def __init__(self, ram, x=None, y=None, margin=None):
        self.x = ram[0x122B] if x is None else x
        self.y = ram[0x122C] if y is None else y
        self.margin = ram[0x122A] if margin is None else margin
        self.out = []

    def run(self, b):
        i = 0
        while i < len(b):
            c = b[i]; i += 1
            if c == 0:
                return
            if c >= 0x80:
                raise ValueError("macro")
            if c == 13:
                self.x = self.margin; self.y += 8
            elif c > 6:
                self.out.append(("8", self.x, self.y, chr(c))); self.x = (self.x + 1) & 0xFF
            elif c == 1:
                self.x = b[i]; i += 1
            elif c == 2:
                self.y = b[i]; i += 1
            elif c == 3:
                self.x, self.y = b[i], b[i + 1]; i += 2
            elif c == 4:
                self.margin = self.x
            elif c == 5:
                i += 1
            elif c == 6:
                while i < len(b) and b[i] == 0x20:
                    i += 1
                if i >= len(b) or b[i] == 0:
                    return


def group(chars):
    out = []
    for f, x, y, ch in chars:
        if f == "v":
            out.append((f, x, y, ch, None)); continue
        step = 6 if f == "5" else 1
        if out and out[-1][0] == f and out[-1][2] == y and out[-1][4] == x:
            o = out[-1]
            out[-1] = (f, o[1], y, o[3] + ch, x + step)
        else:
            out.append((f, x, y, ch, x + step))
    return [(f, x, y, s) for f, x, y, s, _ in out]


# ---- readers ------------------------------------------------------------------------------

MSG_POOL, MSG_HEAD, MSG_SIZE, MSG_SLOTS, MSG_LIFE = 0x65D10, 0x1148, 0x30, 16, 300
GAME_SECONDS = 0x97A


def message_log(r):
    """Screen 4: [(age_s, text)] oldest first, as the render ($162B0) lists them."""
    a, now, out = u32(r, MSG_HEAD), u32(r, GAME_SECONDS), []
    seen = 0
    while a and seen < 64:                       # skip the expired prefix
        if (now - u32(r, a + 4)) & 0xFFFFFFFF <= MSG_LIFE:
            break
        a = u32(r, a); seen += 1
    while a and seen < 64:
        age = (now - u32(r, a + 4)) & 0xFFFFFFFF
        b = bytes(r[a + 8:a + 0x30])
        out.append((age, b))
        a = u32(r, a); seen += 1
    return out


def message_text(b):
    """The text as $9A56 draws it (5 n colour, 6 skip spaces, other control ends)."""
    return "".join(ch for _, _, _, ch in text5(0, 0, b.split(b"\0")[0] + b"\0"))


def expected_messages(r):
    out = Text8(r, 0, 0, 0)
    out.run(b"\x03\x10\x0d\x05\x02Messages\0")
    chars = out.out
    log = message_log(r)
    if not log:
        chars += text5(0x7F, 0x38, "NO MESSAGES")
        return group(chars)
    y = 0x1C
    for age, b in log:
        q, rem = age // 60, age % 60
        if q > 0xFFFF:
            raise ValueError("divu overflow")
        chars.append(("5", 33, y, chr((0x30 + q) & 0xFF)))
        chars.append(("5", 38, y, ":"))
        chars += text5(42, y, digits(rem, 2))
        chars += text5(59, y, b.split(b"\0")[0] if 0 in b else b)
        y += 6
    return group(chars)


SYS = 0xAF4


def sysrec(r, k):
    a = SYS + 8 * k
    return u16(r, a), u16(r, a + 2), u16(r, a + 6)


def weapons_status(r):
    def system(k, word):
        cur, mx, st = sysrec(r, k)
        ok = (st != 0) if word else (r[SYS + 8 * k + 6] != 0)
        return {"operative": ok, "repair": percent_value(cur, mx), "repair_text": percent_text(cur, mx)}
    flares_stock, flares_active, reload = u16(r, 0xC82), u16(r, 0x1152), u16(r, 0x1150)
    flare_state = "none" if flares_stock == 0 else (
        "reloading" if flares_active == 2 or reload != 0 else "ready")
    return {
        "laser": system(4, True), "overheating": u16(r, 0x6C8) > 0x3000,
        "turret": system(3, False), "flare_launcher": system(5, False), "ssm": system(6, False),
        "flares_active": flares_active, "flares_stock": flares_stock, "flare_state": flare_state,
        "viewers_stock": u16(r, 0xC9C), "missiles_stock": u16(r, 0xBB2),
        "drone_active": bool(r[0x5EF9B] & 1),
        "drones_active": u16(r, 0x65C), "drones_stock": u16(r, 0xC1A), "drone_state": drone_state(r),
    }


STATIONS = 0x65E


def drone_state(r):
    if r[0x122F]:
        return "docking"
    out = sum(1 for k in range(4) if r[STATIONS + 14 * k + 1] & 1)
    moving = sum(1 for k in range(4) if not r[STATIONS + 14 * k + 1] & 2)
    return "none" if out == 0 else ("on station" if moving == 0 else "in transit")


DRONE_LINE = {"docking": (0xB7, "DRONES DOCKING"), "on station": (0xAE, "DRONES ON STATION"),
              "in transit": (0xAE, "DRONES IN TRANSIT"), "none": (0xB1, "NO DRONES ACTIVE")}
DRONE_LINE_11 = {"docking": (0x3A, "DRONES DOCKING"), "on station": (0x31, "DRONES ON STATION"),
                 "in transit": (0x31, "DRONES IN TRANSIT"), "none": (0x34, "NO DRONES ACTIVE")}
FLARE_LINE = {"none": (0xA8, "NO FLARES AVAILABLE"), "reloading": (0xAB, "RELOADING LAUNCHER"),
              "ready": (0xA5, "FLARE LAUNCHER READY")}


def labels(r, a):
    """$C6F6 list: x.w y.w ptr.l until x < 0."""
    out = []
    while s16(r, a) >= 0:
        x, y, p = u16(r, a), u16(r, a + 2), u32(r, a + 4)
        out += text5(x, y, bytes(r[p:p + 64]))
        a += 8
    return out


def num2(v):
    return blank(digits(v, 2), 1)


def expected_weapons(r):
    w = weapons_status(r)
    c = labels(r, 0x16E46)
    op = lambda ok: "OPERATIVE" if ok else "INOPERATIVE"
    c += text5(0x57, 0x18, op(w["laser"]["operative"]))
    c += text5(0x7B, 0x1E, w["laser"]["repair_text"])
    c += text5(0x57, 0x2E, op(w["turret"]["operative"]))
    c += text5(0x7B, 0x34, w["turret"]["repair_text"])
    if w["overheating"]:
        c += text5(0x2D, 0x3D, "LASER OVERHEATING")
    c += text5(0x114, 0x15, num2(w["flares_active"]))
    c += text5(0x114, 0x1B, num2(w["flares_stock"]))
    c += text5(0xD8, 0x2E, op(w["flare_launcher"]["operative"]))
    c += text5(0xFC, 0x34, w["flare_launcher"]["repair_text"])
    x, s = FLARE_LINE[w["flare_state"]]
    c += text5(x, 0x3D, s)
    c += text5(0x93, 0x56, num2(w["viewers_stock"]))
    c += text5(0x93, 0x5C, num2(w["missiles_stock"]))
    c += text5(0x57, 0x6F, op(w["ssm"]["operative"]))
    c += text5(0x7B, 0x75, w["ssm"]["repair_text"])
    if w["drone_active"]:
        c += text5(0x24, 0x7E, "VIEWING DRONE ACTIVE")
    c += text5(0x114, 0x56, num2(w["drones_active"]))
    c += text5(0x114, 0x5C, num2(w["drones_stock"]))
    x, s = DRONE_LINE[w["drone_state"]]
    c += text5(x, 0x7E, s)
    return group(c)


# ---- defence radar ------------------------------------------------------------------------

VIEW = 0x4744
FORMATIONS = 0x1065C


def defence_radar(r):
    cx, cy = s16(r, VIEW + 0xC), s16(r, VIEW + 0xE)
    radar = r[0xB02] != 0
    drones = []
    for k in range(4):
        a = STATIONS + 14 * k
        fl = r[a + 1]
        across, along = s16(r, a + 2), s16(r, a + 4)
        s_across, s_along = s16(r, a + 0xA), s16(r, a + 0xC)
        shown = bool(fl & 1) and radar
        drones.append({
            "active": bool(fl & 1), "on_station": bool(fl & 2), "flags": fl,
            "pos": (across, along), "station": (s_across, s_along),
            "marker": (tdiv(along, 60) + cx - 4, tdiv(across, 60) + cy - 8) if shown else None,
            "station_marker": (tdiv(s_along, 60) + cx - 4, tdiv(s_across, 60) + cy - 8)
            if shown and not fl & 2 else None,
        })
    f = u16(r, 0x698)
    pts = []
    for i in range(4):
        a = FORMATIONS + 16 * f + 4 * i
        pts.append((0xFE + tdiv(s16(r, a + 2), 0xC0), 0xB1 + tdiv(s16(r, a), 0xC0)))
    return {"radar": radar, "drones": drones, "formation": f, "formation_points": pts,
            "drones_active": u16(r, 0x65C), "drones_stock": u16(r, 0xC1A), "state": drone_state(r)}


def expected_radar(r):
    d = defence_radar(r)
    c = text5(0x28, 0xA0, "DRONES ACTIVE     " + num2(d["drones_active"]))
    c += text5(0x28, 0xA8, "DRONES IN STORAGE " + num2(d["drones_stock"]))
    x, s = DRONE_LINE_11[d["state"]]
    c += text5(x, 0xB8, s)
    return group(c)


# ---- game status / over -------------------------------------------------------------------

def score_digits(r):
    return bytes(r[0x918:0x924])


def score_text(r):
    """$A418: 12 digit bytes + '0', up to 11 leading '0' blanked (then control 6 skips them)."""
    s = "".join(chr((0x30 + b) & 0xFF) for b in score_digits(r))
    return blank(s, 11).lstrip(" ")


def clock(r):
    return u16(r, 0x8F4), u16(r, 0x8F6), u16(r, 0x8F8)


def clock_text(r):
    h, m, s = clock(r)
    return f"{digits(h, 2)}:{digits(m, 2)}:{digits(s, 2)}"


def game_status(r):
    return {"score": score_text(r), "time": clock_text(r), "friendly": u16(r, 0xD54), "enemy": u16(r, 0xD56)}


def expected_game_status(r):
    g = game_status(r)
    t = Text8(r)
    t.run(b"\x03\x0e\x10\x05\x08GAME STATUS\x03\x06 \x04\x05\tSCORE:  \x05\x08\0")
    t.run(b"\x06" + g["score"].encode() + b"\0")
    t.run(b"\r\r\x05\tTIME:   \x05\x08\0")
    t.run(g["time"].encode() + b"\0")
    t.run(b"\r\r\x05\tFRIENDLY ISLANDS:  \x05\x08\x06" + num2(g["friendly"]).encode()
          + b"\r\r\x05\tENEMY ISLANDS:     \x05\x08\x06" + num2(g["enemy"]).encode() + b"\0")
    return group(t.out)


RESULTS = {0: b"\x03\x0cP\x05\x08Game was aborted",
           1: b"\x03\x06P\x04\x05\x08Aircraft Carrier Destroyed!\r        All is lost",
           2: b"\x03\x05P\x04\x05\x08All islands captured by enemy\r        All is lost",
           3: b"\x03\x05P\x04\x05\x08     All islands captured!\r\rCongratulations on completing\r       Carrier Command",
           4: b"\x03\x05P\x04\x05\x08   Enemy Carrier Destroyed!\r\rCongratulations on completing\r       Carrier Command"}
RESULT_TEXT = {0: "Game was aborted", 1: "Aircraft Carrier Destroyed! All is lost",
               2: "All islands captured by enemy. All is lost", 3: "All islands captured!",
               4: "Enemy Carrier Destroyed!"}


def game_over(r):
    return {"result": u16(r, 0x466), "score": score_text(r), "time": clock_text(r),
            "islands_taken": u16(r, 0xD52)}


def expected_game_over(r):
    g = game_over(r)
    c = [("v", 10, 20, "GAME OVER")]
    t = Text8(r)
    t.run(RESULTS[g["result"]] + b"\0")
    t.run(b"\x03\x02x\x05\tTime:          \x05\x08\0")
    t.run(g["time"].encode() + b"\0")
    t.run(b"\x03\x02\x87\x05\tFinal Score:   \x05\x08\0")
    t.run(b"\x06" + g["score"].encode() + b"\0")
    t.run(b"\x03\x02\x96\x05\tIslands Taken: \x05\x08\0")
    t.run(b"\x06" + blank(digits(g["islands_taken"], 4), 3).encode() + b"\0")
    return group(c + t.out)


def game_options(r):
    """Screen 32 options panel ($EAEE): YES lit while the byte is 0."""
    return {name: r[0x1246 + i] == 0 for i, name in
            enumerate(("sea dots", "message ping", "background noise", "sound effects"))}


# ---- checks -------------------------------------------------------------------------------

class Hooked(Tracer):
    def __init__(self, ram):
        super().__init__(ram)
        self.sprites, self.boxes, self.buttons = [], [], []
        R = lambda reg: ((self.uc.reg_read(reg) & 0xFFFF) ^ 0x8000) - 0x8000
        self.uc.hook_add(UC_HOOK_CODE, lambda *a: self.sprites.append((R(UC_M68K_REG_D0), R(UC_M68K_REG_D1))),
                         begin=0xB292, end=0xB292)
        self.uc.hook_add(UC_HOOK_CODE, lambda *a: self.boxes.append(tuple(R(x) for x in (
            UC_M68K_REG_D0, UC_M68K_REG_D1, UC_M68K_REG_D2, UC_M68K_REG_D3, UC_M68K_REG_D4))),
            begin=0x5308, end=0x5308)
        self.uc.hook_add(UC_HOOK_CODE, lambda *a: self.buttons.append((R(UC_M68K_REG_D0) & 0x2000, R(UC_M68K_REG_D2), R(UC_M68K_REG_D3))),
                         begin=0xB034, end=0xB034)

    def ram(self):
        return bytes(self.uc.mem_read(0, 0x80000))


def run_case(ram, screen):
    """Returns list of mismatch lines."""
    ram = bytearray(ram)
    bad = []
    if screen == 4:
        ram[0x966:0x968] = b"\0\1"
        exp = expected_messages(ram)
        t = Hooked(ram); t.render(4); got = t.strings()
    elif screen == 12:
        exp = expected_weapons(ram)
        t = Hooked(ram); t.render(12); got = t.strings()
    elif screen == 11:
        exp = expected_radar(ram)
        d = defence_radar(ram)
        t = Hooked(ram); t.render(11); got = t.strings()
        after = t.ram()
        sprites = [m["station_marker"] for m in d["drones"] if m["station_marker"]]
        sprites += [m["marker"] for m in d["drones"] if m["marker"]]     # the panel's elements
        if t.sprites != sprites:
            bad.append(f"station sprites got {t.sprites} want {sprites}")
        for k, m in enumerate(d["drones"]):
            e = 0x42A4E + 0x16 * k
            hidden = bool(after[0x40EB2 + 2 * u16(after, e)] & 0x80)
            if hidden != (m["marker"] is None):
                bad.append(f"drone {k} hidden {hidden}")
            if m["marker"] and (s16(after, e + 4), s16(after, e + 6)) != m["marker"]:
                bad.append(f"drone {k} marker {(s16(after, e + 4), s16(after, e + 6))} want {m['marker']}")
        if ram[0x95B] or ram[0x95A]:
            pts = [(x - 1, y - 1, x + 1, y + 1, 2) for x, y in d["formation_points"]]
            got_pts = [b for b in t.boxes if b[4] == 2]
            if got_pts != pts:
                bad.append(f"formation got {got_pts} want {pts}")
    elif screen == 32:
        exp = expected_game_status(ram)
        t = Hooked(ram); t.call(0xCD5C); got = t.strings()
        t2 = Hooked(ram); t2.call(0xEAEE)
        o = game_options(ram)
        want = []
        for i, on in enumerate(o.values()):
            y = 0x1D + 16 * i
            want += [(0x2000 if on else 0, 0xD9, y), (0 if on else 0x2000, 0xFC, y)]
        if t2.buttons != want:
            bad.append(f"options got {t2.buttons} want {want}")
    elif screen == 34:
        exp = expected_game_over(ram)
        t = Hooked(ram); t.call(0xCF42); got = t.strings()
    if got != exp:
        bad.append("strings differ")
        for i in range(max(len(got), len(exp))):
            g = got[i] if i < len(got) else None
            e = exp[i] if i < len(exp) else None
            if g != e:
                bad.append(f"  #{i} got {g} want {e}")
    return bad


def fuzz_state(ram, screen, rng):
    r = bytearray(ram)
    w16 = lambda a, v: struct.pack_into(">H", r, a, v & 0xFFFF)
    w32 = lambda a, v: struct.pack_into(">I", r, a, v & 0xFFFFFFFF)
    if screen in (11, 12):
        for k in range(10):
            mx = rng.choice([1500, 2000, 3000, 4000, rng.randint(1, 5000)])
            cur = rng.randint(0, mx)
            w16(SYS + 8 * k, cur); w16(SYS + 8 * k + 2, mx)
            w16(SYS + 8 * k + 6, rng.choice([0, 0xFF00, 0x0100, 0x00FF, rng.randint(0, 0xFFFF)]))
        w16(0x6C8, rng.choice([0, 0x3000, 0x3001, rng.randint(0, 0xFFFF)]))
        for a in (0x1152, 0xC82, 0xC9C, 0xBB2, 0x65C, 0xC1A):
            w16(a, rng.choice([0, 1, 2, 9, 10, 99, rng.randint(0, 120)]))
        w16(0x1150, rng.choice([0, 0, 5]))
        r[0x122F] = rng.choice([0, 0, 0xFF])
        r[0x5EF9B] = rng.randint(0, 255)
        for k in range(4):
            a = STATIONS + 14 * k
            r[a + 1] = rng.randint(0, 15)
            for o in (2, 4, 6, 8, 0xA, 0xC):
                w16(a + o, rng.randint(-4000, 4000))
        w16(0x698, rng.randint(0, 8))
        w16(0x95A, rng.choice([0, 1, 2]))
    elif screen == 4:
        now = rng.randint(0, 100000)
        w32(GAME_SECONDS, now)
        n = rng.randint(0, 16)
        slots = rng.sample(range(16), 16)
        for i in range(16):
            r[MSG_POOL + MSG_SLOTS * 0 + MSG_SIZE * i] |= 0x80
        times = sorted(now - rng.randint(0, 600) for _ in range(n))
        prev = 0
        for i, t in enumerate(times):
            a = MSG_POOL + MSG_SIZE * slots[i]
            w32(a, 0); w32(a + 4, t)
            txt = "".join(rng.choice("ABCDEFGHIJ KLMNOP 0123456789-!") for _ in range(rng.randint(0, 39)))
            r[a + 8:a + 0x30] = (txt.encode() + b"\0").ljust(0x28, b"\0")
            if prev:
                w32(prev, a)
            else:
                w32(MSG_HEAD, a)
            prev = a
        if n == 0:
            w32(MSG_HEAD, 0)
    elif screen in (32, 34):
        for i in range(12):
            r[0x918 + i] = rng.choice([0] * 3 + list(range(10))) if i < 6 else rng.randint(0, 9)
        if rng.random() < 0.3:
            r[0x918:0x924] = bytes(12)
        w16(0x8F4, rng.randint(0, 23)); w16(0x8F6, rng.randint(0, 59)); w16(0x8F8, rng.randint(0, 59))
        w16(0xD54, rng.randint(0, 64)); w16(0xD56, rng.randint(0, 64))
        w16(0xD52, rng.choice([0, 7, 64, 100, 1234, rng.randint(0, 9999)]))
        w16(0x466, rng.randint(0, 4))
        for i in range(4):
            r[0x1246 + i] = rng.choice([0, 0xFF])
    return bytes(r)


CAPTURES = ["units-frame/ram_3_3.bin", "units-frame/ram_4_4.bin", "units-frame/ram_8_11.bin",
            "units-frame/ram_9_12.bin", "units-frame/ram_20_32.bin", "units-frame/ram_22_34.bin"]


def check(paths=None, fuzz=40, seed=1):
    paths = paths or ([ROOT / "emu/out" / p for p in CAPTURES]
                      + sorted((ROOT / "emu/out/play").glob("pass_*_0_in.bin"))
                      + [ROOT / "emu/out/action/pass_1500_0_in.bin", ROOT / "emu/out/action/pass_300_0_in.bin"])
    rng = random.Random(seed)
    total = fails = 0
    for p in paths:
        ram = p.read_bytes()
        for screen in (4, 11, 12, 32, 34):
            cases = [ram] + [fuzz_state(ram, screen, rng) for _ in range(fuzz)]
            for i, c in enumerate(cases):
                total += 1
                bad = run_case(c, screen)
                if bad:
                    fails += 1
                    if fails <= 8:
                        print(f"MISMATCH {p.name} screen {screen} case {i}")
                        for line in bad[:12]:
                            print("   ", line)
    print(f"group B: {total} cases, {fails} mismatches")
    return fails == 0


if __name__ == "__main__":
    n = int(sys.argv[sys.argv.index("--fuzz") + 1]) if "--fuzz" in sys.argv else 40
    sys.exit(0 if check(fuzz=n) else 1)
