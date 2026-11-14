"""HB Briefing icon: one geometric mark, rendered without any image library.

The same three-bar silhouette is drawn here as a PNG (rasterised from signed distance fields,
so the edges are smooth at every size), as an ICO for Windows shortcuts, and as SVG for Linux
desktop entries and the docs. Keeping it in one module means the PWA icon, the .deb icon and
the Windows installer icon cannot drift apart.
"""
import struct
import sys
import zlib
from pathlib import Path

BACKGROUND = (0x25, 0x2F, 0x5E)
INK = (0xFF, 0xFF, 0xFF)
ACCENT = (0xF5, 0x9E, 0x0B)

PNG_SIZES = (16, 32, 48, 64, 128, 180, 192, 256, 512)

# Geometry in unit coordinates: a rounded square holding three rising bars.
_CORNER = 0.22
_BARS = (
    (0.30, 0.58, 0.085, INK),
    (0.50, 0.42, 0.085, INK),
    (0.70, 0.24, 0.085, ACCENT),
)
_BASELINE = 0.76


def _rounded_box_distance(x, y, half, radius):
    """Signed distance from (x, y) to a rounded square centred on the origin."""
    dx = abs(x) - (half - radius)
    dy = abs(y) - (half - radius)
    outside = (max(dx, 0.0) ** 2 + max(dy, 0.0) ** 2) ** 0.5
    inside = min(max(dx, dy), 0.0)
    return outside + inside - radius


def _capsule_distance(x, y, x0, y0, x1, y1, radius):
    """Signed distance from (x, y) to a line segment thickened to `radius`."""
    ax, ay = x - x0, y - y0
    bx, by = x1 - x0, y1 - y0
    length = bx * bx + by * by
    t = 0.0 if not length else max(0.0, min(1.0, (ax * bx + ay * by) / length))
    dx, dy = ax - bx * t, ay - by * t
    return (dx * dx + dy * dy) ** 0.5 - radius


def _coverage(distance):
    """Turn a signed distance in pixels into 0..1 coverage (one pixel of antialiasing)."""
    if distance <= -0.5:
        return 1.0
    if distance >= 0.5:
        return 0.0
    return 0.5 - distance


def render_rgba(size):
    """Rasterise the mark into `size`x`size` straight-alpha RGBA bytes.

    The rounded square is the surface and each bar is painted over it, coverage blended from
    the signed distance, which antialiases both the tile edge and the bars.
    """
    size = max(8, int(size))
    half = size / 2.0
    corner = _CORNER * size
    bars = [(x * size - half, _BASELINE * size - half,
             (_BASELINE - top) * size, radius * size, colour)
            for x, top, radius, colour in _BARS]
    rows = []
    for py in range(size):
        y = py + 0.5 - half
        row = bytearray()
        for px in range(size):
            x = px + 0.5 - half
            cover = _coverage(_rounded_box_distance(x, y, half, corner))
            if cover <= 0.0:
                row.extend((0, 0, 0, 0))
                continue
            red, green, blue = BACKGROUND
            for bar_x, bar_bottom, length, radius, bar_colour in bars:
                alpha = min(cover, _coverage(_capsule_distance(
                    x, y, bar_x, bar_bottom, bar_x, bar_bottom - length, radius)))
                if alpha <= 0.0:
                    continue
                red = int(red * (1.0 - alpha) + bar_colour[0] * alpha)
                green = int(green * (1.0 - alpha) + bar_colour[1] * alpha)
                blue = int(blue * (1.0 - alpha) + bar_colour[2] * alpha)
            row.extend((red, green, blue, int(cover * 255)))
        rows.append(bytes(row))
    return b"".join(rows)


def _chunk(kind, payload):
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def png_bytes(size):
    """A complete PNG file for one icon size."""
    raw = render_rgba(size)
    stride = size * 4
    # PNG scanlines carry a filter byte; filter 0 (none) is enough for a flat-colour mark.
    filtered = b"".join(b"\x00" + raw[offset:offset + stride]
                        for offset in range(0, len(raw), stride))
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", header)
            + _chunk(b"IDAT", zlib.compress(filtered, 9))
            + _chunk(b"IEND", b""))


def ico_bytes(sizes=(16, 32, 48, 256)):
    """A Windows .ico holding PNG frames (Vista and newer read these directly)."""
    sizes = sorted({max(16, min(256, int(size))) for size in sizes})
    frames = [png_bytes(size) for size in sizes]
    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = 6 + 16 * len(frames)
    entries = b""
    for size, frame in zip(sizes, frames):
        dimension = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32,
                               len(frame), offset)
        offset += len(frame)
    return header + entries + b"".join(frames)


# macOS icon types, newest first. PNG payloads are read by every macOS since 10.13.
_ICNS_TYPES = ((b"ic10", 1024), (b"ic09", 512), (b"ic14", 512), (b"ic08", 256),
               (b"ic13", 256), (b"ic07", 128), (b"ic12", 64), (b"ic11", 32), (b"icp5", 32),
               (b"icp4", 16))


def icns_bytes():
    """A macOS .icns container, built from PNG frames so it can be produced on any OS."""
    frames = []
    for kind, size in _ICNS_TYPES:
        payload = png_bytes(size)
        frames.append(kind + struct.pack(">I", len(payload) + 8) + payload)
    body = b"".join(frames)
    return b"icns" + struct.pack(">I", len(body) + 8) + body


def svg_text():
    """The same mark as SVG, for desktop entries and documentation."""
    bars = "".join(
        f'\n  <line x1="{x}" y1="{_BASELINE}" x2="{x}" y2="{top}" stroke-width="{radius * 2}" '
        f'stroke-linecap="round" stroke="#{colour[0]:02x}{colour[1]:02x}{colour[2]:02x}"/>'
        for x, top, radius, colour in _BARS)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1" '
            f'width="256" height="256" role="img" aria-label="HB Briefing">\n'
            f'  <rect width="1" height="1" rx="{_CORNER}" ry="{_CORNER}" '
            f'fill="#{BACKGROUND[0]:02x}{BACKGROUND[1]:02x}{BACKGROUND[2]:02x}"/>'
            f'{bars}\n</svg>\n')


def write_icons(directory, sizes=PNG_SIZES, prefix="icon"):
    """Write `prefix-<size>.png` for every size plus `prefix.svg`; returns the paths."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for size in sizes:
        path = directory / f"{prefix}-{int(size)}.png"
        path.write_bytes(png_bytes(int(size)))
        written.append(path)
    svg = directory / f"{prefix}.svg"
    svg.write_text(svg_text(), encoding="utf-8")
    written.append(svg)
    return written


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Render the HB Briefing icon set.")
    parser.add_argument("directory", nargs="?", default="icons")
    parser.add_argument("--sizes", default=",".join(str(size) for size in PNG_SIZES))
    parser.add_argument("--prefix", default="icon")
    parser.add_argument("--ico", action="store_true", help="also write a multi-size .ico")
    parser.add_argument("--icns", action="store_true", help="also write a macOS .icns")
    args = parser.parse_args(argv)
    sizes = tuple(int(part) for part in args.sizes.split(",") if part.strip())
    written = write_icons(args.directory, sizes, args.prefix)
    if args.ico:
        path = Path(args.directory) / f"{args.prefix}.ico"
        path.write_bytes(ico_bytes(sizes))
        written.append(path)
    if args.icns:
        path = Path(args.directory) / f"{args.prefix}.icns"
        path.write_bytes(icns_bytes())
        written.append(path)
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
