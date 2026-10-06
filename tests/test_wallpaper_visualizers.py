"""Color persistence, animation timing and bounded spectrum rendering."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cairo

from drape import video_wallpapers as videos
from drape import wallpaper_audio as audio
from drape import wallpaper_sources as sources
from drape.wallpaper_colors import Palette


class VisualizerTests(unittest.TestCase):
    def frame(self, style, **options):
        image = cairo.ImageSurface(cairo.FORMAT_ARGB32, 400, 300)
        audio.draw(
            cairo.Context(image),
            400,
            300,
            [0.2 + 0.6 * (i % 8) / 7 for i in range(64)],
            style,
            "#65d6ce",
            **options,
        )
        image.flush()
        return bytes(image.get_data())

    def test_old_preferences_load_with_new_defaults_and_round_trip_custom_choices(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            mock.patch.object(sources, "PATH", Path(temp) / "sources.json"),
        ):
            old = {
                k: v
                for k, v in sources.DEFAULTS.items()
                if k not in sources.COLOR_OPTIONS or k == "color"
            }
            sources.PATH.write_text(json.dumps(old))
            self.assertEqual(sources.preferences(), sources.DEFAULTS)
            sources.remember(
                source="audio",
                style="spiral",
                color_mode="gradient",
                color2="#ffd166",
                cycle_colors=True,
                color_speed="fast",
            )
            saved = sources.preferences()
            self.assertEqual(saved["color2"], "#ffd166")
            self.assertTrue(saved["cycle_colors"])
            self.assertEqual(saved["style"], "spiral")
            sources.remember(source="xscreensaver")
            self.assertEqual(sources.preferences()["color_mode"], "gradient")

    def test_invalid_color_options_fail_before_playback(self):
        for options in (
            {"color_mode": []},
            {"color_mode": "evil"},
            {"color2": "red"},
            {"cycle_colors": 1},
            {"color_speed": "superfast"},
            {"color_speed": {}},
        ):
            with self.subTest(options=options), self.assertRaises(videos.VideoError):
                videos.selection("bars", source="audio", **options)

    def test_palettes_offer_multiple_colors_and_cycle_smoothly(self):
        for mode in sources.COLOR_MODES:
            with self.subTest(mode=mode):
                static = Palette("#65d6ce", mode=mode)
                later = Palette("#65d6ce", mode=mode, elapsed=10)
                moving = Palette("#65d6ce", mode=mode, cycle=True, elapsed=10)
                self.assertEqual(static.at(0.3), later.at(0.3))
                self.assertNotEqual(static.at(0.3), moving.at(0.3))
                self.assertTrue(all(0 <= v <= 1 for v in moving.at(0.3)))
                if mode != "single":
                    self.assertNotEqual(static.at(0.1), static.at(0.9))
        self.assertEqual(Palette("#ff0000", "#0000ff", "gradient").at(0), (1, 0, 0))
        self.assertEqual(Palette("#ff0000", "#0000ff", "gradient").at(1), (0, 0, 1))
        a = Palette("#65d6ce", mode="rainbow", cycle=True, elapsed=24 - 0.001).at(0.4)
        b = Palette("#65d6ce", mode="rainbow", cycle=True, elapsed=24 + 0.001).at(0.4)
        self.assertLess(max(abs(x - y) for x, y in zip(a, b)), 0.001)

    def test_every_style_renders_all_palettes_and_changed_colors(self):
        for style in sources.STYLES:
            for mode in sources.COLOR_MODES:
                with self.subTest(style=style, mode=mode):
                    a = self.frame(style, color_mode=mode, cycle_colors=True, elapsed=0)
                    b = self.frame(style, color_mode=mode, cycle_colors=True, elapsed=10)
                    self.assertNotEqual(a, b)
        self.assertNotEqual(
            self.frame("bars", color_mode="gradient", color2="#ff0000"),
            self.frame("bars", color_mode="gradient", color2="#0000ff"),
        )

    def test_dynamic_geometry_moves_even_without_color_cycling(self):
        for style in ("particles", "ribbons", "spiral"):
            self.assertNotEqual(self.frame(style, elapsed=0), self.frame(style, elapsed=1))

    def test_shared_animation_clock_freezes_on_pause_without_a_resume_jump(self):
        now = [10.0]
        with (
            mock.patch.object(audio, "inputs", return_value={"desktop": "speakers"}),
            mock.patch.object(audio, "Feed"),
            mock.patch.object(audio.time, "monotonic", side_effect=lambda: now[0]),
        ):
            session = audio.Session(True, False)
            session.feeds[0].frames.values = [0.5] * 64
            now[0] += 0.1
            session.advance()
            elapsed = session.elapsed
            session.pause(True)
            now[0] += 60
            session.advance()
            self.assertEqual(session.elapsed, elapsed)
            session.pause(False)
            now[0] += 0.1
            session.advance()
            self.assertAlmostEqual(session.elapsed, elapsed + 0.1)
            session.close()
