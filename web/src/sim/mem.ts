// Memory and 68000 arithmetic for the simulation model (a TypeScript port of tools/stsim.py
// and the tools/stunits*.py modules; those are the verified reference, see web/src/sim/README.md).
//
// JavaScript numbers are doubles: integers are exact up to 2^53, but the bitwise operators
// work on signed 32 bits. Use these helpers rather than `>>`, `&` or `|` on values that may be
// outside 32 bits, and remember Python's `//` and `%` floor (use pyDiv / pyMod), while
// `int(a / b)` truncates (use Math.trunc).

export const RAM_SIZE = 0x80000;

/** A 16-bit value as unsigned (0..65535). */
export function w16(v: number): number {
  return ((v % 0x10000) + 0x10000) % 0x10000;
}

/** A 16-bit value as signed (-32768..32767). */
export function s16(v: number): number {
  const u = w16(v);
  return u & 0x8000 ? u - 0x10000 : u;
}

/** An 8-bit value as signed. */
export function s8(v: number): number {
  const u = ((v % 0x100) + 0x100) % 0x100;
  return u & 0x80 ? u - 0x100 : u;
}

/** A 32-bit value as unsigned (0..2^32-1). */
export function u32(v: number): number {
  return ((v % 0x100000000) + 0x100000000) % 0x100000000;
}

/** A 32-bit value as signed. */
export function s32(v: number): number {
  const u = u32(v);
  return u >= 0x80000000 ? u - 0x100000000 : u;
}

/** Python's floor division `a // b`. */
export function pyDiv(a: number, b: number): number {
  return Math.floor(a / b);
}

/** Python's modulo `a % b` (sign of the divisor). */
export function pyMod(a: number, b: number): number {
  return a - Math.floor(a / b) * b;
}

/** Python's arithmetic shift `a >> n` for any integer (floor). */
export function shr(a: number, n: number): number {
  return Math.floor(a / 2 ** n);
}

/** Python's `a << n` for any integer. */
export function shl(a: number, n: number): number {
  return a * 2 ** n;
}

/** The low 32 bits of a product, unsigned (Python `(a * b) & 0xFFFFFFFF`): exact for any
 * integer operands, where a plain `*` loses bits above 2^53. */
export function mulLo32(a: number, b: number): number {
  return Math.imul(a, b) >>> 0;
}

/** `(a * b) >> n` with Python's floor, exact even when the product exceeds 2^53. */
export function mulShr(a: number, b: number, n: number): number {
  const p = a * b;
  if (Math.abs(p) < 2 ** 53) return Math.floor(p / 2 ** n);
  return Number((BigInt(a) * BigInt(b)) >> BigInt(n));
}

/** 68000 divu.w: 32/16 -> remainder:quotient; on overflow the destination is unchanged. */
export function divu(d: number, s: number): number {
  d = u32(d);
  const q = Math.floor(d / s);
  const r = d - q * s;
  return q > 0xffff ? d : u32(r * 0x10000 + q);
}

/** Big-endian accessors over the 512 KB image (addresses are absolute). */
export class Mem {
  buf: Uint8Array;

  constructor(buf: Uint8Array) {
    this.buf = buf;
  }

  u8(a: number): number {
    return this.buf[a];
  }

  s8(a: number): number {
    return s8(this.buf[a]);
  }

  u16(a: number): number {
    return (this.buf[a] << 8) | this.buf[a + 1];
  }

  s16(a: number): number {
    return s16(this.u16(a));
  }

  u32(a: number): number {
    return this.buf[a] * 0x1000000 + ((this.buf[a + 1] << 16) | (this.buf[a + 2] << 8) | this.buf[a + 3]);
  }

  s32(a: number): number {
    return s32(this.u32(a));
  }

  set8(a: number, v: number): void {
    this.buf[a] = ((v % 0x100) + 0x100) % 0x100;
  }

  set16(a: number, v: number): void {
    const u = w16(v);
    this.buf[a] = u >> 8;
    this.buf[a + 1] = u & 0xff;
  }

  set32(a: number, v: number): void {
    const u = u32(v);
    this.buf[a] = Math.floor(u / 0x1000000);
    this.buf[a + 1] = (u >> 16) & 0xff;
    this.buf[a + 2] = (u >> 8) & 0xff;
    this.buf[a + 3] = u & 0xff;
  }
}

/** Raised where the reference model raises NotImplementedError: code not ported (or with no
 * defined behaviour in the original). */
export class NotPorted extends Error {
  constructor(message: string) {
    super(message);
    this.name = "NotPorted";
  }
}

/** A state region: [name, start, length]. */
export type Region = [string, number, number];

/** A recorded event (messages, captures, ...), as the reference model's tuples. */
export type SimEvent = (string | number)[];
