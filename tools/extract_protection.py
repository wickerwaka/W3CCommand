#!/usr/bin/env python3
"""Decode the manual-word copy protection table.

$A2C0 picks entry i = language ($127E) * 64 + random(0..63). With key k = ((i + 1) & $FF) ^ $61:
  $4A1F6 + 9*i: page, paragraph, word as three 3-character ASCII fields, each byte ^ k
  $4A8B6:       answers, length-prefixed; walk i entries; byte j (1-based) = plain ^ j ^ k.
The entered word (uppercased at $928) is compared against the decoded answer ($A37E).
Writes extracted/protection/protection.json and protection.txt.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stgfx import ROOT, Payload

QUESTIONS = 0x4A1F6
ANSWERS = 0x4A8B6
LANGUAGES = ["english", "deutsch", "francais"]
PER_LANGUAGE = 64
OUT = ROOT / "extracted" / "protection"


def main() -> None:
    p = Payload()
    OUT.mkdir(parents=True, exist_ok=True)
    entries, a = [], ANSWERS
    for i in range(len(LANGUAGES) * PER_LANGUAGE):
        k = ((i + 1) & 0xFF) ^ 0x61
        q = bytes(b ^ k for b in p.bytes(QUESTIONS + 9 * i, 9)).decode("latin1")
        n = p.data[a - 0x400]
        enc = p.bytes(a + 1, n)
        a += 1 + n
        answer = bytes(b ^ (j + 1) ^ k for j, b in enumerate(enc)).decode("latin1")
        entries.append({"language": LANGUAGES[i // PER_LANGUAGE], "index": i % PER_LANGUAGE,
                        "page": q[0:3].strip(), "paragraph": q[3:6].strip(), "word": q[6:9].strip(),
                        "answer": answer})
    (OUT / "protection.json").write_text(json.dumps(entries, indent=1, ensure_ascii=False) + "\n")
    (OUT / "protection.txt").write_text("\n".join(
        f"{e['language']:8s} {e['index']:2d}  page {e['page']:>3} paragraph {e['paragraph']:>3} "
        f"word {e['word']:>3}  {e['answer']}" for e in entries) + "\n")
    print(f"{len(entries)} entries, answers end at ${a:05X} -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
