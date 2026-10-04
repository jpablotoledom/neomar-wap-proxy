#!/usr/bin/env python3
"""
png2wbmp.py - Convert a PNG (or any Pillow-readable image) into a 1-bit WBMP
file using Floyd-Steinberg dithering, suitable for serving to WAP 1.x clients.

Pre-shrinking and converting images offline with this tool gives better
results than resizing WBMP files on the fly in the gateway (see
docs/PROTOCOL.md), because the dithering can be computed once against the
full-quality source image instead of an already-quantized one.

Usage:
    python3 png2wbmp.py input.png output.wbmp [--width 100]

Requires Pillow (`pip install Pillow`).
"""

import argparse
import sys

try:
    from PIL import Image
except ImportError:
    print("This tool requires Pillow: pip install Pillow", file=sys.stderr)
    sys.exit(1)


def wbmp_write_mbuint(val: int) -> bytes:
    out = [val & 0x7f]
    val >>= 7
    while val:
        out.insert(0, (val & 0x7f) | 0x80)
        val >>= 7
    return bytes(out)


def wbmp_encode(width: int, height: int, pixels) -> bytes:
    """pixels: 2D list, 1 = white, 0 = black."""
    out = bytearray([0, 0]) + bytearray(wbmp_write_mbuint(width)) + bytearray(wbmp_write_mbuint(height))
    row_bytes = (width + 7) // 8
    for y in range(height):
        for xb in range(row_bytes):
            byte = 0
            for bit in range(8):
                x = xb * 8 + bit
                v = pixels[y][x] if x < width else 1
                byte = (byte << 1) | v
            out.append(byte)
    return bytes(out)


def convert(src_path: str, dst_path: str, width: int | None = None) -> tuple[int, int, int]:
    im = Image.open(src_path).convert("L")
    if width:
        ratio = width / im.width
        im = im.resize((width, max(1, round(im.height * ratio))))
    im1 = im.convert("1")  # Pillow applies Floyd-Steinberg dithering here by default
    w, h = im1.size
    src = im1.load()
    px = [[1 if src[x, y] else 0 for x in range(w)] for y in range(h)]
    encoded = wbmp_encode(w, h, px)
    with open(dst_path, "wb") as f:
        f.write(encoded)
    return w, h, len(encoded)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="Source image (PNG, GIF, JPEG, ...)")
    parser.add_argument("output", help="Destination .wbmp file")
    parser.add_argument("--width", type=int, default=None,
                         help="Resize to this width before dithering (keeps aspect ratio)")
    args = parser.parse_args()

    w, h, size = convert(args.input, args.output, args.width)
    print(f"{args.output}: {w}x{h}, {size} bytes")


if __name__ == "__main__":
    main()
