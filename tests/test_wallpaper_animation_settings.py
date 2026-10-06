"""Animation metadata, isolated persistence and validated subprocess arguments."""

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from drape import video_wallpapers as videos
from drape import wallpaper_animation_settings as settings
from drape import wallpaper_xscreensaver as saver

XML = """<screensaver name="flakes">
  <command arg="--root"/>
  <number id="delay" low="0" high="100000" default="10000" arg="--delay %"/>
  <number id="speed" _label="Speed" low="1" high="50" default="10" arg="--speed %"/>
  <number id="random" low="1" high="100" default="-1" arg="--count %"/>
  <number id="fraction" low="0.0" high="1.0" default="0.5" step="0.01" arg="--scale %"/>
  <boolean id="fps" arg-set="--fps"/>
  <boolean id="wander" arg-unset="--no-wander"/>
  <select id="color"><option id="pink"/><option id="red" arg-set="--color '#FF0000'"/></select>
  <string id="text" arg="--text %"/>
  <file id="image" arg="--image %"/>
  <boolean id="escape" arg-set="--root"/>
  <number id="target" low="1" high="99" default="10" arg="--window-id %"/>
  <string id="command" arg="--program %"/>
</screensaver>"""


class AnimationSettingsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        patch = mock.patch.object(settings, "PATH", Path(temp.name) / "settings.json")
        patch.start()
        self.addCleanup(patch.stop)
        self.controls = settings.schema(ET.fromstring(XML))

    def test_controls_exclude_commands_delay_and_surface_overrides(self):
        self.assertEqual(
            [c["id"] for c in self.controls],
            ["speed", "random", "fraction", "fps", "wander", "color", "text", "image"],
        )
        entry = {"executable": "/installed/flakes", "delay": "--delay", "settings": self.controls}
        args = saver.command(
            entry,
            123,
            30,
            {
                "speed": 20,
                "fps": True,
                "wander": False,
                "color": "red",
                "text": "hello; $(evil)",
                "image": "/images/a b.png",
            },
        )
        self.assertEqual(
            args,
            [
                "/installed/flakes",
                "--window-id",
                "123",
                "--delay",
                "33333",
                "--speed",
                "20",
                "--fps",
                "--no-wander",
                "--color",
                "#FF0000",
                "--text",
                "hello; $(evil)",
                "--image",
                "/images/a b.png",
            ],
        )

    def test_values_are_strict_and_defaults_preserve_native_behavior(self):
        for values in (
            {"escape": True},
            {"speed": 51},
            {"speed": True},
            {"speed": 2.5},
            {"fraction": float("nan")},
            {"fps": 1},
            {"color": "purple"},
            {"text": "x\x00y"},
            {"random": -2},
        ):
            with self.subTest(values=values), self.assertRaises(videos.VideoError):
                settings.arguments(self.controls, values)
        self.assertEqual(settings.arguments(self.controls, {}), [])
        self.assertEqual(
            settings.arguments(self.controls, {"random": -1, "fraction": 0.25}),
            ["--count", "-1", "--scale", "0.25"],
        )

    def test_settings_survive_source_switches_and_reset_only_one_animation(self):
        settings.remember("flakes", self.controls, {"speed": 20})
        settings.remember("other", self.controls, {"color": "red"})
        self.assertEqual(settings.saved("flakes", self.controls), {"speed": 20})
        settings.remember("flakes", self.controls, {})
        self.assertEqual(settings.saved("flakes", self.controls), {})
        self.assertEqual(settings.saved("other", self.controls), {"color": "red"})

    def test_package_schema_changes_drop_obsolete_settings_without_losing_valid_ones(self):
        settings.remember("flakes", self.controls, {"speed": 20, "color": "red"})
        changed = [dict(c, high=15) if c["id"] == "speed" else c for c in self.controls]
        self.assertEqual(settings.saved("flakes", changed), {"color": "red"})

    def test_corrupt_settings_are_not_overwritten(self):
        settings.PATH.write_text("broken")
        with self.assertRaises(videos.VideoError):
            settings.remember("flakes", self.controls, {"speed": 20})
        self.assertEqual(settings.PATH.read_text(), "broken")

    def test_ipc_revalidates_settings_using_installed_schema(self):
        entry = {"name": "flakes", "settings": self.controls}
        with mock.patch.object(saver, "validate", return_value=entry):
            path, values = videos.selection("flakes", source="xscreensaver", settings={"speed": 20})
            self.assertEqual(path, "flakes")
            self.assertEqual(values["settings"], {"speed": 20})
            with self.assertRaises(videos.VideoError):
                videos.selection("flakes", source="xscreensaver", settings={"root": True})
