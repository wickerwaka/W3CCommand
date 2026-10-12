"""Group C mirror: Walrus/Manta arming (screens 16/24) and status (20/28).

Readers return what the original shows; expected_strings() turns them into the strings the
original draws (font, x, y, text) so check() can compare against the Tracer (stscreens.py).

  uv run python tools/stscreens_vehicles.py [--fuzz N] [captures...]
"""
import random
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stscreens import Tracer  # noqa: E402

from stgfx import ROOT  # noqa: E402
OBJECTS, OBJ_SIZE = 0x5DCDA, 0x140
MANTA_RECS, WALRUS_RECS, REC = 0x496, 0x570, 0x34
LOADOUTS, LOADOUT_SIZE = 0xCAC4, 0x1A
MANTA_LIST, WALRUS_LIST = 0xC770, 0xC242      # list cursor -> loadout code
NAMES = 0x4AD20
HEALTH_MAX = 4000


def u8(r, a): return r[a]
def u16(r, a): return struct.unpack_from(">H", r, a)[0]
def s16(r, a): return struct.unpack_from(">h", r, a)[0]
def u32(r, a): return struct.unpack_from(">I", r, a)[0]
def s32(r, a): return struct.unpack_from(">i", r, a)[0]


def cstr(r, a, n=128):
    out = bytearray()
    while n and r[a]:
        out.append(r[a]); a += 1; n -= 1
    return out.decode("latin-1")


def text5(raw: str) -> list:
    """$9A56 semantics on a raw string: colour codes dropped, \\x06 skips spaces, any other
    control byte ends the draw. Returns the visible text."""
    out, i = "", 0
    while i < len(raw):
        c = raw[i]; i += 1
        if c >= " ":
            out += c
        elif c == "\x05":
            i += 1
        elif c == "\x06":
            while i < len(raw) and raw[i] == " ":
                i += 1
        else:
            break
    return out


def lines(r, a) -> list:
    """$C6E4: draw a \\r-separated string line by line (each line a $9A56 call)."""
    out = []
    while True:
        raw = ""
        while r[a] >= 0x20 or r[a] in (5, 6):
            if r[a] == 5:
                raw += chr(5) + chr(r[a + 1]); a += 2
            else:
                raw += chr(r[a]); a += 1
        out.append(text5(raw))
        term = r[a]; a += 1
        if term == 0:
            return out


# --- number formatting ($9FAA..$9FCE, $9FFE, $EC4A) -------------------------------------

def digits(v: int, n: int) -> str:
    """$9FAA (5), $9FB6 (4), $9FC2 (3), $9FCE (2): unsigned word, the leading digit unbounded."""
    v &= 0xFFFF
    s = ""
    for d in (10000, 1000, 100, 10)[5 - n:]:
        q = 0
        while v >= d:
            v -= d; q += 1
        s += chr(0x30 + q)
    return s + chr(0x30 + v)


def blank(s: str, n: int) -> str:
    """$9FFE: up to n leading '0' -> ' '."""
    s = list(s)
    for i in range(n):
        if s[i] != "0":
            break
        s[i] = " "
    return "".join(s)


def percent_digits(cur: int, mx: int) -> str:
    """$EC4A: 3 characters of (cur & $FFFF)*100 / max (divu, low word)."""
    p = (((cur & 0xFFFF) * 100) // mx) & 0xFFFF
    if p == 100:
        return "100"
    # emulate exactly: d4 counts how many times 10 could be subtracted
    p2, t = p, -1
    while True:
        t += 1
        borrow = p2 < 10
        p2 = (p2 - 10) & 0xFFFF
        if borrow:
            break
    units = ((p2 + 0x3A) & 0xFFFF)
    tch = 0x20 if t == 0 else (0x30 + t) & 0xFF
    return " " + chr(tch) + chr(units & 0xFF)


def percent_text(cur, mx=HEALTH_MAX):
    return percent_digits(cur, mx) + "%"


def bar(value: int, div: int, cap: int) -> int:
    """divs.w value/div (value a long), capped (signed) at cap."""
    q = int(value / div)          # truncation toward zero
    if not -32768 <= q <= 32767:
        return None               # divs overflow: d3 unchanged (not modelled)
    return min(q, cap)


# --- readers -----------------------------------------------------------------------------

def loadout(r, code: int, harbingers=None, accb_type=None) -> dict:
    """Loadout table $CAC4 entry: weight.w, stock ptr.l, qty.w, kind.w, icon.l, list text.l,
    description.l, fit handler.l."""
    a = LOADOUTS + LOADOUT_SIZE * code
    stock = u32(r, a + 2)
    weight = u16(r, a)
    if code == 7 and harbingers is not None:
        weight = (100 + 150 * harbingers) & 0xFFFF       # $C038: patched by the Walrus panel
    desc = []
    if u32(r, a + 0x12):
        rr = bytearray(r)
        if code == 9 and accb_type is not None:          # $C0F0: "POD - " + building type
            src = 0xCAAC + ((accb_type << 3) & 0xFFFF)
            rr[0xCA6F:0xCA77] = r[src:src + 8]
        desc = lines(rr, u32(r, a + 0x12))
    return {"code": code, "weight": weight, "stock": u16(r, stock) if stock else 0,
            "quantity": u16(r, a + 6), "kind": u16(r, a + 8),
            "mounting": lines(r, u32(r, a + 0xE)) if u32(r, a + 0xE) else [],
            "description": desc}


def subpanel(status: int) -> int:
    """$11738 / $11E60: 0 in the hangar, 1 under repair or preparing, 2 otherwise."""
    return (0, 1, 1, 2)[min(status, 3)]


def arming(r, manta: bool) -> dict:
    bay = u16(r, 0xDB4 if manta else 0xDB6)
    rec = u32(r, 0x48E if manta else 0x568)
    status = r[rec + 1]
    cursor = u16(r, 0xDB8 if manta else 0xDBA)
    code = u16(r, (MANTA_LIST if manta else WALRUS_LIST) + 2 * cursor)
    harb = u16(r, 0xCB80)
    out = {
        "bay": bay, "status": status, "subpanel": u16(r, 0xDC0 if manta else 0xDC2),
        "health": u16(r, rec + 0x14), "fuel": u16(r, rec + (0xA if manta else 8)),
        "weight": u16(r, rec + 0x12), "max_weight": 750 if manta else 2000,
        "cursor": loadout(r, code, None if manta else harb, None if manta else u16(r, 0xDC6)),
        "harbingers_to_load": harb, "accb_type": u16(r, 0xDC6),
    }
    if manta:
        slots = [u16(r, rec + 0x10), u16(r, rec + 0xC), u16(r, rec + 0xE)]   # fuselage, wings
        out["slots"] = slots
        wl = u16(r, LOADOUTS + LOADOUT_SIZE * slots[1])
        wr = u16(r, LOADOUTS + LOADOUT_SIZE * slots[2])
        d = (wl - wr) & 0xFFFF
        if d & 0x8000:
            d = (-d) & 0xFFFF
        out["unbalanced"] = d > 80
    else:
        out["slots"] = [u16(r, rec + 0x26), u16(r, rec + 0x28)]              # payload, weapon
        out["slot_missiles"] = u16(r, rec + 0xC)
    return out


def tank_bar(r, manta: bool):
    """$15200: the carrier's aircraft ($D00) / AAV ($D04) tank, min(tank / $3482, 78) px."""
    return bar(s32(r, 0xD00 if manta else 0xD04), 0x3482, 0x4E)


def fuel_bar(r, manta: bool):
    """$7A46/$7A56: the selected vehicle's fuel, min(fuel / $38E, 36) px (36 = full $7FFF)."""
    rec = u32(r, 0x48E if manta else 0x568)
    return bar(u16(r, rec + (0xA if manta else 8)), 0x38E, 0x24)


BAY_WORDS_M = ["........ DESTROYED", "........ IN HANGER", "..... UNDER REPAIR", "...... FLIGHT PREP",
               ".......... IN LIFT", "........... ACTIVE"]
BAY_WORDS_W = ["........ DESTROYED", ".......... IN DOCK", "..... UNDER REPAIR", "...... LAUNCH PREP",
               ".......... IN LIFT", "........... ACTIVE"]


def bay_summary(r, manta: bool) -> list:
    """$14F2C/$150D2: one line per bay, index = (status byte sign-extended + 1), >= 6 -> 5."""
    out = []
    for k in range(4):
        st = r[(MANTA_RECS if manta else WALRUS_RECS) + REC * k + 1]
        i = ((st - 256 if st & 0x80 else st) + 1) & 0xFFFF
        out.append((BAY_WORDS_M if manta else BAY_WORDS_W)[i if i < 6 else 5])
    return out


def position_text(v: int) -> str:
    """$BCFC: divs #$68E (1678 units), sign then the last four of five digits."""
    q = int(v / 0x68E)
    if not -32768 <= q <= 32767:
        q = None
    q &= 0xFFFF
    neg = bool(q & 0x8000)
    d = (-q) & 0xFFFF if neg else q
    return ("-" if neg else "+") + digits(d, 5)[1:]


PAYLOAD_NAMES = {4: "CLUSTER BOMB", 8: "VIRUS BOMB  ", 9: "ACCB POD    ", 10: "COMMS POD   ", 11: "FUEL POD    "}


def status_page(r, manta: bool) -> list:
    """Screens 28/20 ($16750/$169FE): four bays."""
    out = []
    for k in range(4):
        rec = (MANTA_RECS if manta else WALRUS_RECS) + REC * k
        obj = OBJECTS + OBJ_SIZE * ((2 if manta else 6) + k)
        st = r[rec + 1]
        b = {"bay": k, "status": st,
             "state": "DESTROYED" if st & 0x80 else ("ACTIVE" if st >= 8 else ("IN HANGER" if manta else "IN DOCK"))}
        if b["state"] == "ACTIVE":
            buf = u32(r, obj + 0x3C)
            b["x"], b["z"] = s32(r, obj + 0xA), s32(r, obj + 0x12)
            b["island"] = cstr(r, NAMES + u16(r, NAMES + 2 * r[buf + 1])) if buf else None
            b["health"] = u16(r, rec + 0x14)
            b["weight"] = u16(r, rec + 0x12)
            items = []
            if manta:
                if r[rec + 0x10]:
                    items.append("laser")
            else:
                if s16(r, rec + 0xA) >= 0:
                    items.append(("laser", u16(r, rec + 0xA)))
            if u16(r, rec + 0xC):
                items.append(("missiles", u16(r, rec + 0xC)))
            p = u16(r, rec + 0xE)
            if p and (p & 0xFF) in PAYLOAD_NAMES:
                items.append(("payload", p & 0xFF))
            b["items"] = items
        out.append(b)
    return out


# --- expected strings --------------------------------------------------------------------

BASE = [(33, 7), (162, 7), (33, 72), (162, 72)]


def expected_status(r, manta: bool) -> list:
    out = []
    for b, (x, y) in zip(status_page(r, manta), BASE):
        out.append(("5", x, y, ("      MANTA " if manta else "     WALRUS ") + str(b["bay"] + 1)))
        out.append(("5", x, y + 6, "STATUS:   " + b["state"]))
        if b["state"] != "ACTIVE":
            continue
        out.append(("5", x, y + 12, "POSITION: " + position_text(b["x"]) + "," + position_text(b["z"])))
        out.append(("5", x, y + 18, "ISLAND:   " + (b["island"] or "NONE")))
        out.append(("5", x, y + 24, "REPAIR STATE: " + percent_text(b["health"]).lstrip(" ")))
        out.append(("5", x, y + 30, "PAYLOAD:      " + blank(digits(b["weight"], 4), 3).lstrip(" ") + "KG"))
        yy = y + 36
        for it in b["items"]:
            if it == "laser":
                t = "  LASER   "
            elif it[0] == "laser":
                d = digits(it[1], 2)
                t = "  LASER (" + ((" " + d[1]) if d[0] == "0" else d).lstrip(" ") + ")"
            elif it[0] == "missiles":
                t = "  MISSILES (" + chr((0x30 + it[1]) & 0xFF) + ")"
            else:
                t = "  " + PAYLOAD_NAMES[it[1]]
            out.append(("5", x, yy, t))
            yy += 6
    return out


def expected_arming(r, manta: bool) -> list:
    a = arming(r, manta)
    out = [("5", 205 if manta else 202, 8, ("MANTA " if manta else "WALRUS ") + chr((0x31 + a["bay"]) & 0xFF))]
    sp = subpanel(a["status"])
    if sp == 0:
        c = a["cursor"]
        if manta:
            for i, t in enumerate(c["mounting"]):
                out.append(("5", 35, 99 + 6 * i, t))
            out.append(("5", 35, 91, "WEIGHT   " + blank(digits(c["weight"], 3), 2) + "KG"))
            out.append(("5", 35, 85, "QUANTITY " + blank(digits(c["stock"], 3), 2)))
            out.append(("5", 254, 63, percent_text(a["health"])))
            out.append(("5", 166, 34, "CURRENT        " + blank(digits(a["weight"], 5), 4)))
            if a["unbalanced"]:
                out.append(("5", 172, 42, "UNBALANCED PAYLOAD"))
        else:
            out.append(("5", 35, 105, "WEIGHT   " + blank(digits(c["weight"], 4), 3) + "KG"))
            out.append(("5", 35, 99, "QUANTITY  " + blank(digits(c["stock"], 3), 2)))
            out.append(("5", 254, 66, percent_text(a["health"])))
            out.append(("5", 166, 40, "CURRENT        " + blank(digits(a["weight"], 5), 4)))
            # kind-11 elements ($416B8 at (35,86), $415AA at (201,96)): a digit beside the icon
            if c["kind"] == 11:
                out.append(("5", 81, 89, chr((0x30 + a["harbingers_to_load"]) & 0xFF)))
            if u16(r, LOADOUTS + LOADOUT_SIZE * a["slots"][1] + 8) == 11:
                out.append(("5", 219, 99, chr((0x30 + a["slot_missiles"]) & 0xFF)))
        for i, t in enumerate(c["description"]):
            out.append(("5", 35, 114 + 6 * i, t))
    elif sp == 1:
        if a["status"] == 1:
            out.append(("5", 190, 33 if not manta else 30, "UNDER REPAIR"))
        else:
            out.append(("5", 196, 30 if not manta else 27, "UNDERGOING"))
            out.append(("5", 169, 36 if not manta else 33,
                        "LAUNCH PREPERATIONS" if not manta else "FLIGHT PREPERATIONS"))
        out.append(("5", 256, 48, percent_text(a["health"])))
        # type 79 flag ($40F50) = 0 under repair, $8000 (hidden) while preparing
        out.append(("5", 185, 84, "CANCEL REPAIRS") if a["status"] == 1 else ("5", 185, 84, None))
    else:                                   # $C440 / $CCBA
        st = a["status"]
        if manta:
            out += ([("5", 172, 27, "AIRCRAFT DESTROYED")] if st & 0x80 else
                    [("5", 172, 27, "AIRCRAFT PREPARING"), ("5", 199, 33, "TO LAUNCH")] if st == 3 else
                    [("5", 181, 27, "AIRCRAFT ACTIVE")])
        else:
            out += ([("5", 187, 33, "AAV DESTROYED")] if st & 0x80 else
                    [("5", 187, 30, "AAV PREPARING"), ("5", 214, 36, "TO LAUNCH")] if st == 3 else
                    [("5", 196, 33, "AAV ACTIVE")])
    return out


def expected_bays(r, manta: bool) -> list:
    return [("5", 118, 168 + 6 * k, t) for k, t in enumerate(bay_summary(r, manta))]


# --- check -------------------------------------------------------------------------------

def run(r: bytes, screen: int):
    t = Tracer(r)
    bars = []
    from unicorn import UC_HOOK_CODE
    from unicorn.m68k_const import UC_M68K_REG_D0, UC_M68K_REG_D1, UC_M68K_REG_D2

    def hook(addr):
        def f(u, a, s, d):
            bars.append((addr, u.reg_read(UC_M68K_REG_D0) & 0xFFFF, u.reg_read(UC_M68K_REG_D1) & 0xFFFF,
                         u.reg_read(UC_M68K_REG_D2) & 0xFFFF, int.from_bytes(u.mem_read(0x9DA, 2), "big")))
        return f
    for a in (0x546C, 0x5488):
        t.uc.hook_add(UC_HOOK_CODE, hook(a), begin=a, end=a)
    for a in (0x7BF6, 0x6402, 0x91EC, 0x62D4):      # the hangar's 3D view: not text
        t.uc.mem_write(a, b"\x4e\x75")
    t.render(screen)
    return t, bars


def prepare(r: bytes, screen: int, rng=None) -> bytearray:
    r = bytearray(r[:0x80000])
    struct.pack_into(">H", r, 0x718, screen)
    if screen in (16, 24):
        manta = screen == 24
        bay = u16(r, 0xDB4 if manta else 0xDB6) & 3
        struct.pack_into(">H", r, 0xDB4 if manta else 0xDB6, bay)
        struct.pack_into(">I", r, 0x48E if manta else 0x568, (MANTA_RECS if manta else WALRUS_RECS) + REC * bay)
        struct.pack_into(">H", r, 0x95E if manta else 0x960, 1)     # redraw the panels
        struct.pack_into(">H", r, 0x95C, 2)                          # draw the bay summary
    return r


def fuzz(r: bytes, screen: int, rng: random.Random) -> bytearray:
    r = bytearray(r)
    manta = screen in (24, 28)
    for k in range(4):
        rec = (MANTA_RECS if manta else WALRUS_RECS) + REC * k
        r[rec + 1] = rng.choice([0, 0, 1, 2, 3, 8, 8, 0xFF, 4, 5])
        struct.pack_into(">H", r, rec + 0x14, rng.choice([0, 4000, 3999, 40, 4100, rng.randrange(0, 4001), rng.randrange(65536)]))
        struct.pack_into(">H", r, rec + 0x12, rng.choice([0, 60, 750, 2000, rng.randrange(0, 3000), rng.randrange(65536)]))
        struct.pack_into(">H", r, rec + (0xA if manta else 8), rng.choice([0, 0x7FFF, rng.randrange(0, 0x8000), rng.randrange(65536)]))
        if manta:
            if r[rec + 1] == 0:
                for o, ch in ((0xC, [0, 1, 2]), (0xE, [0, 1, 2]), (0x10, [0, 1, 2, 3, 4, 5, 10])):
                    struct.pack_into(">H", r, rec + o, rng.choice(ch))
            else:
                struct.pack_into(">H", r, rec + 0xC, rng.choice([0, 1, 2, 3, 6, 12]))
                struct.pack_into(">H", r, rec + 0xE, rng.choice([0, 4, 10, 0x10A, 7]))
                r[rec + 0x10] = rng.choice([0, 0xFF])
        else:
            struct.pack_into(">H", r, rec + 0x26, rng.choice([0, 8, 9, 11]))
            struct.pack_into(">H", r, rec + 0x28, rng.choice([0, 6, 7]))
            struct.pack_into(">H", r, rec + 0xC, rng.choice([0, 1, 5, 9]))
            struct.pack_into(">H", r, rec + 0xA, rng.choice([0, 40, 7, 0xFFFF, 100]))
            struct.pack_into(">H", r, rec + 0xE, rng.choice([0, 8, 9, 11, 3]))
            struct.pack_into(">H", r, rec + 0x10, rng.choice([0, 1, 2]))
        obj = OBJECTS + OBJ_SIZE * ((2 if manta else 6) + k)
        struct.pack_into(">i", r, obj + 0xA, rng.choice([0, -1, 1677, 1678, -1678, rng.randrange(-8_000_000, 8_000_000)]))
        struct.pack_into(">i", r, obj + 0x12, rng.choice([0, -1677, rng.randrange(-8_000_000, 8_000_000)]))
        struct.pack_into(">I", r, obj + 0x3C, rng.choice([0, 0x62CDA, 0x63CEC]))
    for b in (0x62CDA, 0x63CEC):
        r[b + 1] = rng.randrange(64)
    struct.pack_into(">H", r, 0xDB4, rng.randrange(4)); struct.pack_into(">H", r, 0xDB6, rng.randrange(4))
    struct.pack_into(">H", r, 0xDB8, rng.randrange(6)); struct.pack_into(">H", r, 0xDBA, rng.randrange(5))
    struct.pack_into(">H", r, 0xCB80, rng.randrange(10)); struct.pack_into(">H", r, 0xDC6, rng.randrange(3))
    for k in range(15):
        struct.pack_into(">H", r, 0xB76 + 0x1A * k + 8, rng.choice([0, 1, 9, 10, 99, 100, 999, rng.randrange(1000)]))
    for a in (0xD00, 0xD04):
        struct.pack_into(">i", r, a, rng.choice([0, 0x7FFFFF, rng.randrange(0, 0x200000), rng.randrange(0, 2_000_000)]))
    return r


def compare(r: bytes, screen: int) -> list:
    manta = screen in (24, 28)
    t, bars = run(r, screen)
    got = t.strings()
    errs = []
    if screen in (20, 28):
        exp = expected_status(r, manta)
        if [g for g in got] != exp:
            errs.append(f"status: got {got} want {exp}")
        return errs
    # arming: the render recomputed the sub-panel; mirror reads RAM after the run
    after = bytes(t.uc.mem_read(0, 0x80000))
    exp = expected_arming(after, manta) + expected_bays(after, manta)
    gs = set(got)
    absent = {(e[1], e[2]) for e in exp if e[3] is None}
    exp = [e for e in exp if e[3] is not None]
    errs += [f"unexpected {g}" for g in got if (g[1], g[2]) in absent]
    for e in exp:
        if e not in gs:
            near = [g for g in got if g[2] == e[2] and abs(g[1] - e[1]) < 40]
            errs.append(f"missing {e}; drawn there: {near}")
    # dynamic positions must not show anything unexpected
    dyn = {(e[1], e[2]) for e in exp}
    for g in got:
        if (g[1], g[2]) in dyn and g not in exp:
            errs.append(f"unexpected {g}")
    # bars: tank (line $5488 colour 2: x from 192, end 236 + len), fuel ($546C colour 2: y2 = 88 - len)
    tank = [b for b in bars if b[0] == 0x5488 and b[4] == 2]
    tb = tank_bar(after, manta)
    if tb is not None and (not tank or tank[-1][3] != (0xEC + tb) & 0xFFFF):
        errs.append(f"tank bar {tank} want {tb}")
    if subpanel(after[u32(after, 0x48E if manta else 0x568) + 1]) == 0:
        fb = fuel_bar(after, manta)
        fl = [b for b in bars if b[0] == 0x546C and b[4] == 2]
        if not fl or fl[-1][3] != (0x58 - fb) & 0xFFFF:
            errs.append(f"fuel bar {fl} want {fb}")
    if manta and subpanel(after[u32(after, 0x48E) + 1]) == 0:
        rec = u32(after, 0x48E)
        if (after[rec + 0x1C] != 0) != arming(after, True)["unbalanced"]:
            errs.append("balance flag +$1C")
    return errs


DEFAULT_CAPTURES = ["units-frame/ram_10_16.bin", "units-frame/ram_14_20.bin", "units-frame/ram_15_24.bin",
                    "units-frame/ram_19_28.bin", "play/pass_flight_0_in.bin", "play/pass_combat_0_in.bin",
                    "play/pass_launch_10_in.bin", "play/pass_late_0_in.bin", "play/pass_takeoff_0_in.bin",
                    "views/pass_walrus_0_in.bin", "views/pass_manta_0_in.bin", "action/pass_1500_5_in.bin"]


def check(paths=None, nfuzz=40, seed=1):
    paths = paths or [ROOT / "emu/out" / p for p in DEFAULT_CAPTURES]
    ok = bad = 0
    cov = {}
    rng = random.Random(seed)
    for p in paths:
        base = Path(p).read_bytes()
        for screen in (16, 24, 20, 28):
            states = [prepare(base, screen)] + [prepare(fuzz(base, screen, rng), screen) for _ in range(nfuzz)]
            for i, r in enumerate(states):
                errs = compare(bytes(r), screen)
                if errs:
                    bad += 1
                    if bad <= 12:
                        print(f"MISMATCH {Path(p).name} screen {screen} state {i}:")
                        for e in errs[:6]:
                            print("   ", e)
                else:
                    ok += 1
                    cov[(screen, subpanel(r[u32(r, 0x48E if screen == 24 else 0x568) + 1]) if screen in (16, 24) else 0)] = cov.get((screen, subpanel(r[u32(r, 0x48E if screen == 24 else 0x568) + 1]) if screen in (16, 24) else 0), 0) + 1
    print("coverage (screen, sub-panel):", sorted(cov.items()))
    print(f"{ok} OK, {bad} MISMATCH")
    return bad == 0


if __name__ == "__main__":
    args = sys.argv[1:]
    n = 40
    if "--fuzz" in args:
        i = args.index("--fuzz"); n = int(args[i + 1]); del args[i:i + 2]
    paths = args or None
    sys.exit(0 if check(paths, n) else 1)
