"""The icon set is generated code, so its container formats need proving, not eyeballing."""
import struct
import unittest
import xml.etree.ElementTree as ET

import hbicons


class PngTest(unittest.TestCase):
    def test_png_header_and_dimensions(self):
        for size in (32, 180, 512):
            data = hbicons.png_bytes(size)
            self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack(">II", data[16:24]), (size, size))
            self.assertEqual(data[-8:-4], b"IEND", "the stream must end with IEND")

    def test_png_is_deterministic(self):
        self.assertEqual(hbicons.png_bytes(64), hbicons.png_bytes(64))

    def test_the_mark_is_drawn_inside_the_tile(self):
        size = 64
        raw = hbicons.render_rgba(size)
        alpha = [raw[index] for index in range(3, len(raw), 4)]
        self.assertEqual(alpha[0], 0, "the rounded corner must be transparent")
        centre = (size // 2) * size + size // 2
        self.assertEqual(alpha[centre], 255, "the middle of the tile must be opaque")
        colours = {raw[index:index + 3] for index in range(0, len(raw), 4)
                   if raw[index + 3] == 255}
        self.assertIn(bytes(bytearray(hbicons.INK)), colours, "the white bars must be visible")
        self.assertIn(bytes(bytearray(hbicons.ACCENT)), colours,
                      "the accent bar must be visible")

    def test_small_sizes_still_render(self):
        self.assertEqual(len(hbicons.png_bytes(8)), len(hbicons.png_bytes(8)))
        self.assertGreater(len(hbicons.render_rgba(8)), 0)


class IcoTest(unittest.TestCase):
    def test_ico_header_lists_every_frame(self):
        data = hbicons.ico_bytes((16, 32, 48, 256))
        reserved, kind, count = struct.unpack("<HHH", data[:6])
        self.assertEqual((reserved, kind, count), (0, 1, 4))
        # 256px entries store 0 in the single-byte dimension field.
        dimensions = [data[6 + 16 * index] for index in range(4)]
        self.assertEqual(dimensions, [16, 32, 48, 0])

    def test_every_ico_frame_is_a_png(self):
        data = hbicons.ico_bytes((32, 256))
        offset = 6 + 16 * 2
        self.assertEqual(data[offset:offset + 8], b"\x89PNG\r\n\x1a\n")


class IcnsTest(unittest.TestCase):
    def test_icns_container_length_matches_the_declared_size(self):
        data = hbicons.icns_bytes()
        self.assertEqual(data[:4], b"icns")
        declared = struct.unpack(">I", data[4:8])[0]
        self.assertEqual(declared, len(data), "the icns header must state the real length")
        self.assertIn(b"ic09", data[8:], "a 512px frame is required by Finder")

    def test_each_icns_entry_declares_its_own_length(self):
        data = hbicons.icns_bytes()
        offset = 8
        seen = 0
        while offset < len(data):
            length = struct.unpack(">I", data[offset + 4:offset + 8])[0]
            self.assertGreaterEqual(length, 8)
            offset += length
            seen += 1
        self.assertEqual(offset, len(data))
        self.assertGreaterEqual(seen, 5)


class SvgTest(unittest.TestCase):
    def test_svg_is_well_formed_and_has_one_line_per_bar(self):
        root = ET.fromstring(hbicons.svg_text())
        namespace = "{http://www.w3.org/2000/svg}"
        lines = root.findall(f"{namespace}line")
        self.assertEqual(len(lines), len(hbicons._BARS))
        self.assertIsNotNone(root.find(f"{namespace}rect"))


if __name__ == "__main__":
    unittest.main()
