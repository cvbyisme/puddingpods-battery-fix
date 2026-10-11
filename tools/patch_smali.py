#!/usr/bin/env python3
"""PuddingPods GAIA framer fix.

Bug
---
`Llc1;->b([B)` (the GAIA frame slicer used on every RFCOMM read; the class name
differs per build — Coolapk 3.47 uses `Llc1;`, the GitHub release build `Lmc1;`,
so this script locates it by method signature + code fingerprint) treats the
device's length field as the *whole* frame body:

    prefix "FF 04 <len>"  ->  slice = <len> bytes  ->  consumed = 4 + <len>

but MOONDROP PUDDING (GAIA v4, vendor 0x1D) puts the payload length in that
field, i.e. the body is 4 bytes longer:

    body = [seq][vendor][feature][cmd] + payload(<len>)   ->  consumed = 8 + <len>

Verified by btsnoop captures of the official app (see docs/MOONDROP_Pudding_逆向结果.md
in roxyyn0304/moondrop-gaia-protocol):

    -> FF 04 00 02 00 1D 1A 01 01 02          len=2, body=6
    <- FF 04 00 06 00 1D 1B 01 01 64 02 64 03 5E  len=6, body=10

Consequence of the wrong slice: the parser (`hm1;->a([B)`) receives
`00 1D 1B 01 01 64` instead of `00 1D 1B 01 01 64 02 64 03 5E`, so the
`[id][level]` loop ends after the *first* pair.  Left bud = correct,
right bud and charging case stay 0.  Every other notification
(ANC/gain/LED) is truncated the same way and never matches.

Fix
---
Both length paths (1-byte and 2-byte length) converge on the label right
before `and-int/lit8 v0, v0, 0x1`; after that point `v2` holds the declared
length and everything else (slice size, copy loop, consumed byte count) is
derived from it.  Adding 4 there fixes all of them at once.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

METHOD_HEADER = ".method public b([B)Ljava/util/ArrayList;"
FIX_INSN = "    add-int/lit8 v2, v2, 0x4"
FIX_COMMENT = "    # [puddingpods-battery-fix] length field excludes the 4-byte [seq][vendor][feature][cmd] prefix"
ANCHOR_INSN = "    and-int/lit8 v0, v0, 0x1"
LABEL_RE = re.compile(r"^    :[A-Za-z0-9_]+$")
# sanity fingerprint of the pre-patch code paths
NEEDLES = ("and-int/2addr v2, v3", "or-int/2addr v2, v4")


def patch(path: Path) -> bool:
    text = path.read_text()
    lines = text.splitlines(keepends=True)

    start = next((i for i, l in enumerate(lines) if l.strip() == METHOD_HEADER.strip()), None)
    if start is None:
        raise SystemExit(f"{path}: method {METHOD_HEADER} not found")
    end = next((i for i in range(start, len(lines)) if lines[i].startswith(".end method")), None)
    if end is None:
        raise SystemExit(f"{path}: .end method not found")

    body = "".join(lines[start:end])
    for needle in NEEDLES:
        if needle not in body:
            raise SystemExit(f"{path}: unexpected framer shape (missing {needle!r})")

    if FIX_INSN in body:
        print(f"{path}: already patched, nothing to do")
        return False

    targets = [
        i
        for i in range(start, end - 1)
        if LABEL_RE.match(lines[i].rstrip("\n")) and lines[i + 1].rstrip("\n") == ANCHOR_INSN
    ]
    if len(targets) != 1:
        raise SystemExit(f"{path}: expected exactly 1 convergence point, found {len(targets)}")

    at = targets[0]
    lines[at + 1 : at + 1] = [FIX_COMMENT + "\n", FIX_INSN + "\n", "\n"]
    path.write_text("".join(lines))
    print(f"{path}: patched at line {at + 2}")
    return True


def locate(root: Path) -> Path:
    """Find the one class holding the framer.

    The class name depends on the build (Coolapk 3.47 ships `Llc1;`, the GitHub release
    build ships `Lmc1;`), so match on the method signature plus the code fingerprint
    instead of a hard-coded path.
    """
    hits = []
    for path in sorted(root.rglob("*.smali")):
        text = path.read_text(errors="replace")
        if METHOD_HEADER in text and all(n in text for n in NEEDLES):
            hits.append(path)
    if not hits:
        raise SystemExit(f"{root}: no framer class (method {METHOD_HEADER} + fingerprint) found")
    if len(hits) > 1:
        raise SystemExit(f"{root}: {len(hits)} framer candidates, refusing to guess: "
                         + ", ".join(str(p) for p in hits))
    return hits[0]


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <smali-output-dir>")
    root = Path(sys.argv[1])
    if not root.is_dir():
        raise SystemExit(f"{root} is not a directory")
    patch(locate(root))
