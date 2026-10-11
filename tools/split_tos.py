#!/usr/bin/env python3
"""Split CARRIER.TOS into the GEMDOS loader and the absolute-addressed game payload.

The TOS text segment is a loader that copies file bytes from offset $400 onward
to absolute address $400 and jumps there, so the payload's file offset equals its
runtime address.
"""
import struct
import sys
from pathlib import Path

PAYLOAD_ADDR = 0x400
HEADER_SIZE = 0x1C


def main(src: Path, out_dir: Path) -> None:
    data = src.read_bytes()
    magic, tlen, dlen, blen, slen = struct.unpack_from(">HIIII", data, 0)
    if magic != 0x601A:
        sys.exit(f"{src}: not a GEMDOS executable (magic {magic:#06x})")
    print(f"text={tlen} data={dlen} bss={blen} sym={slen}")

    loader = data[HEADER_SIZE:PAYLOAD_ADDR]
    payload = data[PAYLOAD_ADDR:]
    if payload[:4] != bytes.fromhex("4ef8405a"):
        sys.exit(f"unexpected payload start: {payload[:4].hex()}")

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "loader.bin").write_bytes(loader)
    (out_dir / "carrier_payload.bin").write_bytes(payload)
    print(f"loader.bin: {len(loader):#x} bytes")
    print(f"carrier_payload.bin: {len(payload):#x} bytes at ${PAYLOAD_ADDR:X}-${PAYLOAD_ADDR + len(payload) - 1:X}")


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    main(root / "roms" / "CARRIER.TOS", root / "extracted")
