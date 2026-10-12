#!/usr/bin/env python3
"""Export test vectors for the TypeScript port of the simulation model (web/src/sim).

Runs the Python model's own checks (tools/check_sim.py and tools/check_units.py, which
verify it against Hatari and the original code under Unicorn) with a recorder attached, so
every model state those checks create becomes a vector: the state as from_ram built it, then
each top-level call made on it with
  - the memory bytes changed from outside since the previous call (scenario pokes),
  - the method name and arguments,
  - what the call returned, the events it recorded and the exception it raised, if any,
  - the CRC-32 of the state regions afterwards (STATE_REGIONS then UNIT_REGIONS, in order).
web/scripts/check-sim.ts replays them on the TypeScript model (npm run check-sim).

Output (git-ignored, regenerable; it holds game memory): extracted/sim_vectors/
  index.json             regions, suites and counts
  bases/<n>.bin.z        zlib'd 512 KB states that vectors are stored relative to (XOR)
  <suite>.jsonl.gz       one vector per line

  uv run python tools/export_sim_vectors.py [--fuzz 300] [--scenarios 160]
  uv run python tools/export_sim_vectors.py --replay units 1234 --dump state.bin
      replay one vector in Python, and with --dump compare its state after the failing call
      (check-sim.ts writes it) region by region.
"""
import argparse
import base64
import contextlib
import functools
import gzip
import io
import json
import sys
import zlib
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stsim
import stunits
from stgfx import ROOT
# check_sim / check_units are imported after Recorder.install(): they keep references to
# model methods (check_units.ROUTINES) that must be the recording wrappers.

OUT = ROOT / "extracted" / "sim_vectors"
SAMPLE = 4096                    # bytes compared to pick the closest base
NEW_BASE = 8192                  # differing bytes above which a state becomes a new base
PER_CALLER = 300                 # vectors kept per private helper (_carrier_local makes thousands)


def regions() -> list:
    return stsim.STATE_REGIONS + stunits.UNIT_REGIONS


def state_crc(buf) -> int:
    crc = 0
    for _, a, n in regions():
        crc = zlib.crc32(buf[a:a + n], crc)
    return crc


def plain(v):
    """JSON form of a return value or event: tuples as lists, bools as 0/1."""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (list, tuple)):
        return [plain(x) for x in v]
    if v is None or isinstance(v, (int, str)):
        return v
    return f"<{type(v).__name__}>"          # not compared


def error_name(e: BaseException) -> str:
    return "NotPorted" if isinstance(e, NotImplementedError) else type(e).__name__


def pokes(old: np.ndarray, new: np.ndarray) -> list:
    """Runs of changed bytes: [[addr, "hex"], ...]."""
    idx = np.flatnonzero(old != new)
    runs, start = [], None
    for k, a in enumerate(idx):
        if start is None:
            start = a
        if k + 1 == len(idx) or idx[k + 1] != a + 1:
            runs.append([int(start), bytes(new[start:a + 1]).hex()])
            start = None
    return runs


class Recorder:
    def __init__(self, out: Path):
        self.out = out
        (out / "bases").mkdir(parents=True, exist_ok=True)
        for old in (out / "bases").glob("*.bin.z"):
            old.unlink()
        rng = np.random.default_rng(1)
        live = np.concatenate([np.arange(a, a + n) for _, a, n in regions()])
        self.sample = np.sort(rng.choice(live, SAMPLE, replace=False))
        self.bases, self.samples = [], np.zeros((0, SAMPLE), np.uint8)
        self.vectors, self.suite, self.depth, self.building = [], None, 0, False
        self.per_caller = {}

    def base_for(self, buf: np.ndarray) -> int:
        if len(self.bases):
            k = int(np.argmin((self.samples != buf[self.sample]).sum(axis=1)))
            if np.count_nonzero(self.bases[k] != buf) <= NEW_BASE:
                return k
        k = len(self.bases)
        self.bases.append(buf.copy())
        self.samples = np.vstack([self.samples, buf[self.sample]])
        (self.out / "bases" / f"{k}.bin.z").write_bytes(zlib.compress(buf.tobytes(), 9))
        return k

    def created(self, state, cls_name: str) -> None:
        caller = sys._getframe(2).f_code.co_name
        self.per_caller[caller] = self.per_caller.get(caller, 0) + 1
        if caller.startswith("_") and self.per_caller[caller] > PER_CALLER:
            return
        buf = np.frombuffer(state.mem.buf, np.uint8)
        k = self.base_for(buf)
        vec = {"name": f"{caller} {len(self.vectors)}", "cls": cls_name, "base": k,
               "delta": base64.b64encode(zlib.compress((buf ^ self.bases[k]).tobytes(), 9)).decode(),
               "calls": []}
        self.vectors.append((self.suite, vec))
        state._vec, state._snap = vec, buf.copy()

    def call(self, fn, state, args, kwargs):
        vec = getattr(state, "_vec", None)
        if self.depth or vec is None or self.building:
            return fn(state, *args, **kwargs)
        buf = np.frombuffer(state.mem.buf, np.uint8)
        rec = {"f": fn.__name__, "a": plain(list(args)), "p": pokes(state._snap, buf)}
        if kwargs:
            rec["k"] = {k: plain(v) for k, v in kwargs.items()}
        n_events = len(state.events)
        self.depth += 1
        try:
            result = fn(state, *args, **kwargs)
            rec["r"] = plain(result)
            return result
        except Exception as e:
            rec["x"] = [error_name(e), str(e)]
            raise
        finally:
            self.depth -= 1
            rec["e"] = plain(state.events[n_events:])
            rec["c"] = state_crc(state.mem.buf)
            vec["calls"].append(rec)
            state._snap = np.frombuffer(state.mem.buf, np.uint8).copy()

    def install(self) -> None:
        rec = self

        def wrap(fn):
            @functools.wraps(fn)
            def inner(self, *args, **kwargs):
                return rec.call(fn, self, args, kwargs)
            return inner

        classes = {c for c in stunits.UnitState.__mro__ + stsim.WorldState.__mro__ if c is not object}
        for cls in classes:
            for name, attr in list(vars(cls).items()):
                if callable(attr) and not isinstance(attr, type) and name not in ("from_ram", "to_ram") \
                        and not name.startswith("__") and hasattr(attr, "__code__"):
                    setattr(cls, name, wrap(attr))

        for cls in (stsim.WorldState, stunits.UnitState):
            orig = cls.__dict__.get("from_ram") or getattr(cls, "from_ram")
            func = orig.__func__

            def from_ram(klass, ram, _func=func):
                outer = not rec.building
                rec.building = True
                try:
                    state = _func(klass, ram)
                finally:
                    if outer:
                        rec.building = False
                if outer:
                    rec.created(state, klass.__name__)
                return state
            setattr(cls, "from_ram", classmethod(from_ram))

    def write(self) -> None:
        counts = {}
        files = {}
        for suite, vec in self.vectors:
            if not vec["calls"]:
                continue
            if suite not in files:
                files[suite] = gzip.open(self.out / f"{suite}.jsonl.gz", "wt", compresslevel=6)
                counts[suite] = [0, 0]
            files[suite].write(json.dumps(vec, separators=(",", ":")) + "\n")
            counts[suite][0] += 1
            counts[suite][1] += len(vec["calls"])
        for f in files.values():
            f.close()
        index = {"regions": [[n, a, l] for n, a, l in regions()], "bases": len(self.bases),
                 "suites": {s: {"vectors": v, "calls": c} for s, (v, c) in counts.items()}}
        (self.out / "index.json").write_text(json.dumps(index, indent=1))
        for s, (v, c) in counts.items():
            print(f"{s}: {v} vectors, {c} calls")
        print(f"{len(self.bases)} bases -> {self.out}")


def run_suite(rec: Recorder, suite: str, main, argv: list) -> None:
    rec.suite = suite
    sys.argv = [suite, *argv]
    with contextlib.redirect_stdout(io.StringIO()) as log:
        status = main()
    if status:
        print(log.getvalue())
        sys.exit(f"{suite}: the Python model's own checks failed; fix those first")
    print(f"{suite}: checks passed")


def load_vector(suite: str, n: int) -> tuple:
    with gzip.open(OUT / f"{suite}.jsonl.gz", "rt") as f:
        for k, line in enumerate(f):
            if k == n:
                vec = json.loads(line)
                base = np.frombuffer(zlib.decompress((OUT / "bases" / f"{vec['base']}.bin.z").read_bytes()), np.uint8)
                delta = np.frombuffer(zlib.decompress(base64.b64decode(vec["delta"])), np.uint8)
                return vec, (base ^ delta).tobytes()
    sys.exit(f"{suite} has no vector {n}")


def replay(suite: str, n: int, dump: Path | None) -> int:
    """Run one vector in Python; with a dump from check-sim.ts, diff it after its failing call."""
    import check_units
    vec, ram = load_vector(suite, n)
    cls = stunits.UnitState if vec["cls"] == "UnitState" else stsim.WorldState
    state = cls.from_ram(ram)
    mine = dump.read_bytes() if dump else None
    fail = int(dump.with_suffix(".call").read_text()) if dump and dump.with_suffix(".call").exists() else None
    print(f"{vec['name']}: {vec['cls']}, {len(vec['calls'])} calls")
    for k, c in enumerate(vec["calls"]):
        for a, hx in c["p"]:
            state.mem.buf[a:a + len(hx) // 2] = bytes.fromhex(hx)
        try:
            getattr(state, c["f"])(*c["a"], **c.get("k", {}))
        except Exception as e:
            if "x" not in c:
                raise
            assert error_name(e) == c["x"][0], (e, c["x"])
        assert state_crc(state.mem.buf) == c["c"], f"call {k} {c['f']}: Python no longer matches the vector"
        if fail is not None and k == fail:
            ref = bytearray(state.mem.buf)
            print(f"call {k} {c['f']}{tuple(c['a'])}: TypeScript state vs Python")
            for line in check_units.diff(bytearray(mine), ref, limit=8) or ["  (regions identical)"]:
                print("  " + line)
            return 0
    print("replayed OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fuzz", type=int, default=300, help="check_units: perturbed states per routine")
    ap.add_argument("--scenarios", type=int, default=160, help="check_units: perturbed multi-pass runs")
    ap.add_argument("--replay", nargs=2, metavar=("SUITE", "N"))
    ap.add_argument("--dump", type=Path, help="with --replay: the TypeScript state after the failing call")
    args = ap.parse_args()
    if args.replay:
        return replay(args.replay[0], int(args.replay[1]), args.dump)
    rec = Recorder(OUT)
    rec.install()
    import check_sim
    import check_units  # noqa: F401
    run_suite(rec, "world", check_sim.main, [])
    run_suite(rec, "units", check_units.main, ["--fuzz", str(args.fuzz), "--scenarios", str(args.scenarios)])
    rec.write()
    return 0


if __name__ == "__main__":
    sys.exit(main())
