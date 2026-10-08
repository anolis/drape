"""GlitchPEG JPEG conversion preserves images and handles concurrent requests."""

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from drape import wallpaper_image_helper as helper


class ImageHelperTests(unittest.TestCase):
    def test_png_is_converted_once_without_modifying_the_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, cache = root / "original.png", root / "cache"
            Image.new("RGB", (600, 600), (100, 150, 200)).save(source)
            original = source.read_bytes()
            target = helper.jpeg_copy(source, cache)
            with Image.open(target) as image:
                self.assertEqual(image.format, "JPEG")
                self.assertEqual(image.size, (600, 600))
            stamp = target.stat().st_mtime_ns
            self.assertEqual(helper.jpeg_copy(source, cache), target)
            self.assertEqual(target.stat().st_mtime_ns, stamp)
            self.assertEqual(source.read_bytes(), original)

    def test_jpeg_is_used_in_place(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "original.jpg"
            Image.new("RGB", (600, 600)).save(source)
            self.assertEqual(helper.jpeg_copy(source, root / "cache"), source)
            self.assertFalse((root / "cache").exists())

    def test_concurrent_monitors_receive_a_complete_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "original.png"
            Image.new("RGB", (600, 600), "blue").save(source)
            with ThreadPoolExecutor(max_workers=3) as workers:
                results = list(
                    workers.map(lambda _: helper.jpeg_copy(source, root / "cache"), range(3))
                )
            self.assertEqual(len(set(results)), 1)
            with Image.open(results[0]) as image:
                image.load()
                self.assertGreater(image.getpixel((0, 0))[2], 245)
            self.assertFalse(list((root / "cache").glob("*.tmp")))
