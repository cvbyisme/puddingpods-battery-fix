#!/usr/bin/env python3
"""Reproduce the PuddingPods battery bug (and the fix) offline.

Mirrors the two pieces of decompiled logic that matter:

  Llc1;->b([B)   GAIA frame slicer   (reads  FF 04 <len> ... )
  hm1;->a([B)    payload handler     (reads  [seq][vendor][feature][cmd][id][level]...)

Captured traffic (btsnoop, MOONDROP Link v2.23.1c) from
roxyyn0304/moondrop-gaia-protocol/docs/MOONDROP_Pudding_逆向结果.md:

    -> FF 04 00 02 00 1D 1A 01 01 02
    <- FF 04 00 06 00 1D 1B 01 01 64 02 64 03 5E
"""
from __future__ import annotations

BATTERY_RX = bytes.fromhex("FF040006001D" + "1B0101640264035E")

ID_LEFT, ID_RIGHT, ID_CASE = 1, 2, 3


def framer(buf: bytes, fixed: bool) -> tuple[list[bytes], int]:
    """Return (frames, consumed) exactly like the smali implementation."""
    flags = buf[2]
    two_byte_len = bool(flags & 2)
    header = 5 if two_byte_len else 4
    if len(buf) < header:
        return [], 0
    if two_byte_len:
        length = (buf[3] << 8) | buf[4]
    else:
        length = buf[3]
    trailing = 1 if (flags & 1) else 0
    if fixed:
        # THE FIX: the length field counts only the payload that follows the
        # 4-byte [seq][vendor][feature][cmd] prefix of this firmware.
        length += 4
    consumed = header + length + trailing
    if length < 0 or consumed > 0x10004 or len(buf) < consumed:
        return [], 0
    return [buf[header : header + length]], consumed


def handler(frame: bytes) -> dict[str, int]:
    """Parse [seq][vendor][feature][cmd][id][level]... into battery slots."""
    out: dict[str, int] = {}
    if len(frame) < 6 or frame[0] != 0 or frame[1] != 0x1D:
        return out
    if frame[2] != 0x1B or frame[3] != 1:
        return out
    i = 4
    while i + 1 < len(frame):
        dev, level = frame[i], frame[i + 1]
        if 0 <= level <= 100:
            if dev == ID_LEFT:
                out["left"] = level
            elif dev == ID_RIGHT:
                out["right"] = level
            elif dev == ID_CASE:
                out["case"] = level
        i += 2
    return out


def run(fixed: bool) -> None:
    frames, consumed = framer(BATTERY_RX, fixed)
    label = "after fix " if fixed else "before fix"
    print(f"[{label}] frame sliced: {frames[0].hex(' ').upper() if frames else '(none)'}")
    print(f"[{label}] consumed {consumed}/{len(BATTERY_RX)} bytes, leftover {BATTERY_RX[consumed:].hex(' ').upper() or '-'}")
    print(f"[{label}] parsed battery: {handler(frames[0]) if frames else {}}")
    print()


if __name__ == "__main__":
    print(f"device frame: {BATTERY_RX.hex(' ').upper()}  (len field = 0x{BATTERY_RX[3]:02X})\n")
    run(fixed=False)
    run(fixed=True)
