"""Screen 3 (Resources) readouts: Python mirror of what the original draws, and a check.

Render $14C6C (by sub-page [$D14]: 0 $14C96 carrier fuel bars, 2 $1118A keypad field, 5 $14D28
stockpile/queue fuel bars, others rts) and panel callbacks $D188 (bottom icons), $D264 (0),
$D2C6 (1), $DEAA (2), $E0BA (3), $E1B6 (4), $E392 (5).

  uv run python tools/stscreens_resources.py [--fuzz N]
"""
import random
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stscreens import Tracer  # noqa: E402
from unicorn import UC_HOOK_CODE  # noqa: E402
from unicorn.m68k_const import (UC_M68K_REG_A7, UC_M68K_REG_D0, UC_M68K_REG_D1,  # noqa: E402
                                UC_M68K_REG_D2, UC_M68K_REG_D3)

from stgfx import ROOT  # noqa: E402

ITEMS, ITEM_SIZE, N_ITEMS = 0xB76, 0x1A, 15
SUB = 0xD14              # sub-page 0-5
PICTURE_ITEM = 0xD16     # item shown on sub-page 1
EDIT_ROW = 0xD18         # sub-page 2: row being typed (0-2)
EDITING = 0xD1A          # byte: keypad entry active; text at $D1B
STORES_SCROLL = 0xD38    # sub-page 2 first item (0-12)
TRANSFER_SCROLL = 0xD3A  # sub-page 4 first item (0-12)
SHIP_FUEL, AIR_FUEL, AAV_FUEL = 0xCFC, 0xD00, 0xD04
STOCK_FUEL = 0xD60       # 3 longs: ship, aircraft, AAV
QUEUE_FUEL = 0xD6C       # 3 longs
STOCK_STORES = 0xD78     # 15 words
QUEUE_STORES = 0xD96     # 15 words
FUEL_CLASS, FUEL_CLASS_WORK = 0xD08, 0xD0E
BAR = 78                 # bar length in pixels
CARRIER_BAR_DIV, STOCK_BAR_DIV = 0x3482, 0x6905


def u8(r, a): return r[a]
def u16(r, a): return struct.unpack_from(">H", r, a)[0]
def s16(r, a): return struct.unpack_from(">h", r, a)[0]
def u32(r, a): return struct.unpack_from(">I", r, a)[0]
def s32(r, a): return struct.unpack_from(">i", r, a)[0]


def sx16(v): v &= 0xFFFF; return v - 0x10000 if v & 0x8000 else v


# ---- formatting -------------------------------------------------------------------------

def digits(v: int, n: int) -> str:
    """$9FAA/$9FB6/$9FC2/$9FCE: n digits (5/4/3/2) of the unsigned word v by repeated
    subtraction; a leading 'digit' over 9 is printed as the byte $30 + q."""
    v &= 0xFFFF
    divs = [10000, 1000, 100, 10][5 - n:]
    s = ""
    for d in divs:
        q, v = divmod(v, d)
        s += chr((0x30 + q) & 0xFF)
    return s + chr(0x30 + v)


def blank(s: str, n: int) -> str:
    """$9FFE with d0 = n: up to n leading '0' become spaces."""
    i = 0
    while i < n and i < len(s) and s[i] == "0":
        i += 1
    return " " * i + s[i:]


def qty3(v: int) -> str:
    return blank(digits(v, 3), 2)


def bar(value: int, div: int) -> int:
    """$15234 / $14E2C: divs.w (overflow leaves d0), then min(low word, 78) signed."""
    v = value & 0xFFFFFFFF
    sv = v - (1 << 32) if v & 0x80000000 else v
    q = int(sv / div)  # truncation towards zero
    lo = q if -0x8000 <= q <= 0x7FFF else sx16(v)
    return min(lo, BAR)


def text8_segments(r, a):
    """8x8 control-coded text ($9BE2): returns [(x, y, text)], and the address after the NUL."""
    out, x, y, cur, cx = [], 0, 0, "", None
    def flush():
        nonlocal cur, cx
        if cur:
            out.append((cx, y, cur))
        cur, cx = "", None
    while True:
        c = r[a]; a += 1
        if c == 0:
            flush(); return out, a
        if c & 0x80:
            flush(); continue      # token (table $9C8A); not used by item names
        if c <= 6:
            flush()
            if c == 1: x = r[a]; a += 1
            elif c == 2: y = r[a]; a += 1
            elif c == 3: x, y = r[a], r[a + 1]; a += 2
            elif c == 5: a += 1
            elif c == 6:
                while r[a] == 0x20: a += 1
            continue
        if c == 0x0D:
            flush(); y += 8; continue
        if cx is None: cx = x
        cur += chr(c); x += 1


def text5_lines(r, a):
    """The description after an item name: 5x5 lines ($9A56) until a NUL ends a line."""
    lines = []
    while True:
        cur = ""
        while True:
            c = r[a]; a += 1
            if c >= 0x20: cur += chr(c); continue
            if c == 5: a += 1; continue
            if c == 6:
                while r[a] == 0x20: a += 1
                continue
            break
        lines.append(cur)
        if c == 0:
            return lines


# ---- structured readouts (same names as the TS) -----------------------------------------

def item_name(r, k):
    segs, _ = text8_segments(r, u32(r, ITEMS + ITEM_SIZE * k + 0xA))
    return " - ".join(s for _, _, s in segs)


def resources_stores_on_board(r):
    out = []
    for k in range(N_ITEMS):
        a = ITEMS + ITEM_SIZE * k
        out.append(dict(item=k, name=item_name(r, k), x=s16(r, a), y=s16(r, a + 2),
                        on_board=u16(r, a + 8), capacity=u16(r, a + 0x18), picture=u16(r, a + 0xE),
                        cls=u16(r, a + 0x10), target=u16(r, a + 0x12),
                        cls_edit=u16(r, a + 0x14), target_edit=u16(r, a + 0x16)))
    return out


def resources_carrier_fuel(r):
    return dict(ship=u32(r, SHIP_FUEL), aircraft=u32(r, AIR_FUEL), aav=u32(r, AAV_FUEL),
                bars=[bar(s32(r, a), CARRIER_BAR_DIV) for a in (SHIP_FUEL, AIR_FUEL, AAV_FUEL)])


def resources_item_page(r):
    k = u16(r, PICTURE_ITEM)
    a = ITEMS + ITEM_SIZE * k
    segs, after = text8_segments(r, u32(r, a + 0xA))
    return dict(item=k, units=u16(r, a + 8), picture=u16(r, a + 0xE), name_segments=segs,
                description=text5_lines(r, after))


def class_box(cls):
    """$DF84: which third of the 3-level box is lit; 2 (top) = most urgent."""
    return 0 if cls == 0 else 1 if cls == 1 else 2


def resources_stores_priorities(r):
    top = u16(r, STORES_SCROLL)
    rows = []
    for i in range(3):
        a = ITEMS + ITEM_SIZE * (top + i)
        rows.append(dict(item=top + i, cls=u16(r, a + 0x14), target=u16(r, a + 0x16)))
    edit = None
    if r[EDITING]:
        edit = dict(row=u16(r, EDIT_ROW), text=cstr(r, 0xD1B, 8), digits=u16(r, 0xD2A),
                    max_digits=u16(r, 0xD2C))
    return dict(first=top, rows=rows, edit=edit)


def resources_fuel_priorities(r):
    return dict(edit=[u16(r, FUEL_CLASS_WORK + 2 * i) for i in range(3)],
                committed=[u16(r, FUEL_CLASS + 2 * i) for i in range(3)])


def resources_stores_transfer(r):
    top = u16(r, TRANSFER_SCROLL)
    return dict(first=top, rows=[dict(item=top + i, stockpile=u16(r, STOCK_STORES + 2 * (top + i)),
                                      queued=u16(r, QUEUE_STORES + 2 * (top + i))) for i in range(3)])


def resources_fuel_transfer(r):
    st = [s32(r, STOCK_FUEL + 4 * i) for i in range(3)]
    q = [s32(r, QUEUE_FUEL + 4 * i) for i in range(3)]
    return dict(stockpile=st, queued=q, stockpile_bars=[bar(v, STOCK_BAR_DIV) for v in st],
                queued_bars=[bar(v, CARRIER_BAR_DIV) for v in q])


def cstr(r, a, n):
    s = bytes(r[a:a + n]).split(b"\0")[0]
    return s.decode("latin1")


def resources_gate(r):
    """$177DE (every pass on screen 3) and the sub-page buttons: sub-pages >= 2 need comms
    ($B0A); 4 and 5 also no transfer running ($D3E) and the carrier at the stockpile island."""
    comms = r[0xB0A] != 0
    busy = r[0xD3E] != 0
    buf = u32(r, 0x5E996)          # the carrier's island buffer (object 10 +$3C)
    at_stockpile = buf != 0 and r[buf + 1] == u16(r, 0xD4A)
    reason = None if comms else "Communications are out"
    transfer_reason = reason or ("Resupply operation in progress" if busy else
                                 None if at_stockpile else "Stockpile island out of range")
    return dict(comms=comms, transfer_running=busy, at_stockpile=at_stockpile,
                priorities_blocked=reason, transfers_blocked=transfer_reason)


# ---- expected draw lists ------------------------------------------------------------------

STATIC = {
    1: {("5", 150, 120, "EXIT")},
    2: {("8", 9, 8, "Set Supply Priorities"), ("5", 122, 33, "MAXIMUM"), ("5", 122, 69, "MAXIMUM"),
        ("5", 122, 105, "MAXIMUM"), ("5", 230, 27, "CANCEL"), ("5", 236, 119, "EXIT")},
    3: {("8", 10, 8, "Set Fuel Priorities"), ("5", 230, 27, "CANCEL"), ("5", 236, 119, "EXIT")},
    4: {("8", 12, 8, "Supply Transfer"), ("5", 79, 26, "STOCKPILE"), ("5", 173, 26, "RESUPPLY DRONE"),
        ("5", 252, 121, "EXIT")} | {("5", x, y, "QUANTITY") for x in (82, 191) for y in (39, 73, 107)},
    5: {("8", 13, 8, "Fuel Transfer"), ("5", 57, 26, "STOCKPILE"), ("5", 194, 26, "RESUPPLY DRONE"),
        ("5", 148, 121, "EXIT")},
}


def expected(r, sub):
    """(strings, bars, boxes) the original draws for sub-page `sub` (render + panels)."""
    strings, bars, boxes = [], [], []
    if sub == 0:
        for it in resources_stores_on_board(r):
            strings.append(("5", (it["x"] + 7) & 0xFFFF, (it["y"] + 34) & 0xFFFF, qty3(it["on_board"])))
        f = resources_carrier_fuel(r)
        for x0, n in zip((60, 148, 236), f["bars"]):
            bars.append((192, x0, x0 + 78, 0))
        for x0, n in zip((60, 148, 236), f["bars"]):
            bars.append((192, x0, (x0 + n) & 0xFFFF, 2))
    elif sub == 1:
        p = resources_item_page(r)
        strings.append(("8", 14, 96, "UNITS : " + qty3(p["units"])))
        strings += [("8", x, y, s) for x, y, s in p["name_segments"]]
        strings += [("5", 115, 14 + 8 * i, s) for i, s in enumerate(p["description"]) if s]
    elif sub == 2:
        sp = resources_stores_priorities(r)
        e = sp["edit"]
        for i, row in enumerate(sp["rows"]):
            strings.append(("5", 134, 41 + 36 * i, qty3(row["target"])))
            boxes.append((24 + 36 * i, class_box(row["cls"])))
        if e:
            # $112BA: the typed digits, right-aligned in the field opened by $10B02
            x0 = struct.unpack_from(">h", r, 0xD22)[0]
            d3 = e["digits"] or 1
            x = (x0 + 6 * (e["max_digits"] - d3)) & 0xFFFF
            y = u16(r, 0xD24)
            if e["text"]:
                strings.append(("5", x, y, "".join(c for c in e["text"] if ord(c) >= 0x20)))
    elif sub == 3:
        for i, c in enumerate(resources_fuel_priorities(r)["edit"]):
            boxes.append((24 + 36 * i, class_box(c)))
    elif sub == 4:
        t = resources_stores_transfer(r)
        for i, row in enumerate(t["rows"]):
            strings.append(("5", 97, 47 + 34 * i, qty3(row["stockpile"])))
        for i, row in enumerate(t["rows"]):
            strings.append(("5", 206, 47 + 34 * i, qty3(row["queued"])))
    elif sub == 5:
        f = resources_fuel_transfer(r)
        for y in (49, 74, 99):
            bars.append((y, 44, 122, 0))
        for y in (49, 74, 99):
            bars.append((y, 196, 274, 0))
        for y, n in zip((49, 74, 99), f["stockpile_bars"]):
            bars.append((y, 44, (44 + n) & 0xFFFF, 2))
        for y, n in zip((49, 74, 99), f["queued_bars"]):
            bars.append((y, 196, (196 + n) & 0xFFFF, 2))
    return strings, bars, boxes


# ---- the original under Unicorn -----------------------------------------------------------

class ResTracer(Tracer):
    BOX_SITES = {0xDF9C: 2, 0xDFAA: 1, 0xDFB8: 0}

    def __init__(self, ram):
        super().__init__(ram)
        self.bars, self.boxes = [], []
        self.uc.hook_add(UC_HOOK_CODE, lambda u, a, s, d: self._bar(), begin=0x5488, end=0x5488)
        for site in self.BOX_SITES:
            self.uc.hook_add(UC_HOOK_CODE, lambda u, a, s, d: self._box(a), begin=site, end=site)

    def _bar(self):
        ret = self.u32(self.r(UC_M68K_REG_A7))
        if 0x14C96 <= ret <= 0x14E2A:   # the render's fuel bars (not the static ones of $E0BA)
            self.bars.append((self.r(UC_M68K_REG_D0) & 0xFFFF, self.r(UC_M68K_REG_D1) & 0xFFFF,
                              self.r(UC_M68K_REG_D2) & 0xFFFF, self.u16(0x9DA)))

    def _box(self, a):
        y = (self.r(UC_M68K_REG_D1) & 0xFFFF) - {2: 1, 1: 11, 0: 22}[self.BOX_SITES[a]]
        self.boxes.append((y, self.BOX_SITES[a]))


def run_original(ram, sub):
    r = bytearray(ram)
    r[0x718:0x71A] = b"\0\3"
    r[SUB:SUB + 2] = sub.to_bytes(2, "big")
    t = ResTracer(r)
    t.render(3)
    t.panels(3)
    got = [s for s in t.strings() if s not in STATIC.get(sub, set())]
    return got, t.bars, t.boxes


def perturb(ram, rng):
    r = bytearray(ram)
    def w16(a, v): r[a:a + 2] = (v & 0xFFFF).to_bytes(2, "big")
    def w32(a, v): r[a:a + 4] = (v & 0xFFFFFFFF).to_bytes(4, "big")
    def num():
        return rng.choice([0, 1, 9, 10, 99, 100, 999, rng.randrange(1000), rng.randrange(1000)])
    for k in range(N_ITEMS):
        a = ITEMS + ITEM_SIZE * k
        w16(a + 8, num()); w16(a + 0x16, num())
        w16(a + 0x14, rng.choice([0, 1, 2, 2, 3, 0xFFFF]))
        w16(STOCK_STORES + 2 * k, num()); w16(QUEUE_STORES + 2 * k, num())
    def fuel():
        return rng.choice([0, 1, 0x3481, 0x3482, 0x6905, 0x100000, 0x1FFFC0,
                           rng.randrange(0x200000), rng.randrange(0x200000), 0x7FFFFFFF, -5])
    for a in (SHIP_FUEL, AIR_FUEL, AAV_FUEL):
        w32(a, fuel())
    for i in range(3):
        w32(STOCK_FUEL + 4 * i, fuel()); w32(QUEUE_FUEL + 4 * i, fuel())
        w16(FUEL_CLASS_WORK + 2 * i, rng.choice([0, 1, 2, 5]))
    w16(PICTURE_ITEM, rng.randrange(15))
    w16(STORES_SCROLL, rng.randrange(13)); w16(TRANSFER_SCROLL, rng.randrange(13))
    r[EDITING] = 0
    if rng.random() < 0.4:
        # open the keypad field as $10B02 does for row `row`, then type some digits
        row = rng.randrange(3)
        tx, ty = s16(r, 0xE026 + 8 * row), s16(r, 0xE026 + 8 * row + 2)
        r[EDITING] = 0xFF
        w16(EDIT_ROW, row)
        w16(0xD22, tx + 14); w16(0xD24, ty + 10); w16(0xD2C, 3); w16(0xD2E, 10); w16(0xD30, 8)
        w16(0xD26, tx + 14 + 17); w16(0xD28, ty + 15)
        n = rng.randrange(4)
        w16(0xD2A, n)
        txt = "".join(rng.choice("0123456789") for _ in range(n)) or "0"
        r[0xD1B:0xD1B + len(txt) + 1] = txt.encode() + b"\0"
    return bytes(r)


def captures():
    out = [ROOT / "emu/out/units-frame/ram_3_3.bin"]
    for d, pat in (("play", "pass_*_0_in.bin"), ("action", "pass_*_0_in.bin"), ("sim", "tick_*_0_in.bin")):
        out += sorted((ROOT / "emu/out" / d).glob(pat))
    return out


def check(paths=None, fuzz=10, seed=1, verbose=False):
    rng = random.Random(seed)
    ok, bad = 0, []
    for path in paths or captures():
        base = path.read_bytes()[:0x80000]
        states = [("capture", base)] + [(f"fuzz{i}", perturb(base, rng)) for i in range(fuzz)]
        for label, ram in states:
            for sub in range(6):
                got = run_original(ram, sub)
                want = expected(ram, sub)
                if (sorted(got[0]), got[1], sorted(got[2])) == (sorted(want[0]), want[1], sorted(want[2])):
                    ok += 1
                else:
                    bad.append((path.name, label, sub, got, want))
                    if verbose:
                        print("MISMATCH", path.name, label, sub)
                        print("  got ", got)
                        print("  want", want)
    return ok, bad


if __name__ == "__main__":
    fz = int(sys.argv[sys.argv.index("--fuzz") + 1]) if "--fuzz" in sys.argv else 10
    ok, bad = check(fuzz=fz, verbose="-v" in sys.argv)
    print(f"screen 3 resources: {ok} sub-page draws OK, {len(bad)} MISMATCH")
    for b in bad[:5]:
        print("MISMATCH", b[0], b[1], "sub", b[2])
        print("  got ", b[3])
        print("  want", b[4])
