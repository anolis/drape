"""Installed animation discovery, spectrum boundaries and source persistence."""

import json
import os
import shutil
import signal
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cairo

from drape import video_wallpapers as videos
from drape import wallpaper_audio as audio
from drape import wallpaper_process as processes
from drape import wallpaper_sources as sources
from drape import wallpaper_xscreensaver as saver


class SourceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.configs = self.root / "config"
        self.bins = self.root / "bin"
        self.configs.mkdir()
        self.bins.mkdir()
        for patch in (
            mock.patch.object(sources, "PATH", self.root / "preferences.json"),
            mock.patch.object(saver, "CONFIG_DIRS", (self.configs,)),
            mock.patch.object(saver, "BIN_DIRS", (self.bins,)),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def animation(self, name="flakes", delay="--delay %"):
        (self.configs / (name + ".xml")).write_text(
            f'<screensaver name="{name}" _label="Pretty Flakes"><command arg="--root; evil"/>'
            f'<number id="delay" arg="{delay}"/><_description> A snowy\n scene. </_description></screensaver>'
        )
        path = self.bins / name
        path.touch()
        path.chmod(0o755)
        return path

    def test_catalog_requires_executable_and_metadata_and_ignores_commands(self):
        self.animation()
        (self.bins / "helper-without-metadata").touch()
        (self.configs / "broken.xml").write_text("broken")
        (self.configs / "missing.xml").write_text('<screensaver name="absent"/>')
        entries = saver.catalog()
        self.assertEqual([entry["name"] for entry in entries], ["flakes"])
        self.assertEqual(entries[0]["description"], "A snowy scene.")
        args = saver.command(entries[0], 123, 30)
        self.assertEqual(
            args, [str(self.bins / "flakes"), "--window-id", "123", "--delay", "33333"]
        )
        self.assertNotIn("--root", args)

    def test_untrusted_delay_argument_and_removed_animation(self):
        executable = self.animation(delay="--command evil %")
        entry = saver.validate("flakes")
        self.assertEqual(saver.command(entry, 12), [str(executable), "--window-id", "12"])
        executable.unlink()
        with self.assertRaisesRegex(videos.VideoError, "no longer installed"):
            saver.validate("flakes")
        for name in ("/bin/sh", "../flakes", "flakes;evil", None):
            with self.subTest(name=name), self.assertRaises(videos.VideoError):
                saver.validate(name)

    def test_hacks_can_find_packaged_helpers_without_changing_the_session_path(self):
        helper = self.bins / "xscreensaver-getimage-file"
        helper.write_text("#!/bin/sh\nexit 0\n")
        helper.chmod(0o755)
        with mock.patch.dict(os.environ, PATH="/usr/bin"):
            environment = saver.environment()
            self.assertEqual(environment["PATH"], str(self.bins) + os.pathsep + "/usr/bin")
            self.assertEqual(os.environ["PATH"], "/usr/bin")
            self.assertEqual(
                shutil.which("xscreensaver-getimage-file", path=environment["PATH"]), str(helper)
            )

    def test_source_preferences_merge_and_keep_video_library_independent(self):
        self.assertEqual(sources.preferences()["source"], "video")
        sources.remember(source="xscreensaver", animation="flakes", fps=15)
        sources.remember(source="audio", style="rings", microphone=True)
        saved = sources.preferences()
        self.assertEqual(saved["animation"], "flakes")
        self.assertEqual(saved["fps"], 15)
        self.assertEqual(saved["style"], "rings")
        self.assertTrue(saved["microphone"])

    def test_corrupt_preferences_are_not_overwritten(self):
        sources.PATH.write_text('{"version": 200}')
        with self.assertRaises(videos.VideoError):
            sources.remember(source="video")
        self.assertEqual(sources.PATH.read_text(), '{"version": 200}')
        for change in (
            {"source": []},
            {"style": {}},
            {"microphone": "yes"},
            {"fps": True},
            {"color": "red\n[x]"},
        ):
            with self.subTest(change=change), self.assertRaises(videos.VideoError):
                sources.validate({**sources.DEFAULTS, **change})

    def test_invalid_source_options_are_rejected_before_launch(self):
        for path, source, options in (
            ("bars", "audio", {"desktop_audio": False, "microphone": False}),
            ("evil", "audio", {}),
            ("flakes", "xscreensaver", {"command": "/bin/sh"}),
            ("x", [], {}),
        ):
            with self.subTest(source=source), self.assertRaises(videos.VideoError):
                videos.selection(path, source=source, **options)


class AudioTests(unittest.TestCase):
    def test_fragmented_raw_stream_retains_only_latest_complete_frame(self):
        frames = audio.Frames()
        frames.push(bytes([255] * 20))
        self.assertFalse(frames.received)
        frames.push(bytes([255] * 44 + [128] * 64 + [1] * 5))
        self.assertTrue(frames.received)
        self.assertEqual(len(frames.pending), 5)
        self.assertEqual(frames.values, [128 / 255] * 64)
        frames.push(bytes([64] * 10000))
        self.assertLess(len(frames.pending), 64)
        self.assertEqual(len(frames.values), 64)

    def test_desktop_capture_never_uses_default_microphone(self):
        def pactl(*args):
            if args == ("get-default-sink",):
                return "speakers"
            if args == ("get-default-source",):
                self.fail("desktop-only accessed the microphone")
            return json.dumps([{"name": "speakers.monitor", "monitor_of_sink": 1}])

        with (
            mock.patch.object(audio, "available", return_value=True),
            mock.patch.object(audio, "_pactl", side_effect=pactl),
        ):
            self.assertEqual(audio.inputs(True, False), {"desktop": "speakers.monitor"})

    def test_microphone_and_both_inputs_are_explicit_and_monitor_is_rejected(self):
        def pactl(*args):
            if args == ("get-default-sink",):
                return "speakers"
            if args == ("get-default-source",):
                return "mic"
            return json.dumps(
                [
                    {"name": "mic", "monitor_of_sink": None},
                    {"name": "speakers.monitor", "monitor_of_sink": 1},
                ]
            )

        with (
            mock.patch.object(audio, "available", return_value=True),
            mock.patch.object(audio, "_pactl", side_effect=pactl),
        ):
            self.assertEqual(audio.inputs(False, True), {"microphone": "mic"})
            self.assertEqual(
                audio.inputs(True, True), {"desktop": "speakers.monitor", "microphone": "mic"}
            )
        with (
            mock.patch.object(audio, "available", return_value=True),
            mock.patch.object(
                audio,
                "_pactl",
                side_effect=["speakers.monitor", json.dumps([{"name": "speakers.monitor"}])],
            ),
            self.assertRaisesRegex(videos.VideoError, "not a microphone"),
        ):
            audio.inputs(False, True)
        with self.assertRaises(videos.VideoError):
            audio.inputs(False, False)

    def test_audio_config_is_isolated_unsigned_binary_and_cannot_inject_options(self):
        text = audio.config("speakers.monitor")
        self.assertIn("method = pulse", text)
        self.assertIn("bit_format = 8bit", text)
        self.assertIn("channels = mono", text)
        with self.assertRaises(videos.VideoError):
            audio.config("speakers\n[output]\nmethod = sdl")

    def test_one_session_combines_inputs_and_pause_preserves_levels(self):
        with (
            mock.patch.object(
                audio, "inputs", return_value={"desktop": "speakers", "microphone": "mic"}
            ),
            mock.patch.object(audio, "Feed") as feed,
        ):
            a, b = mock.Mock(), mock.Mock()
            a.frames.values, b.frames.values = [0.2] * 64, [0.7] * 64
            a.frames.received = b.frames.received = True
            feed.side_effect = [a, b]
            session = audio.Session(True, True)
            session.advance()
            self.assertEqual(session.values, [0.7] * 64)
            self.assertTrue(session.ready())
            session.pause(True)
            session.advance()
            a.advance.assert_called_once()
            b.pause.assert_called_once_with(True)
            session.close()
            a.close.assert_called_once()
            b.close.assert_called_once()

    def test_partial_capture_startup_cleans_up_prior_input(self):
        first = mock.Mock()
        with (
            mock.patch.object(audio, "inputs", return_value={"desktop": "a", "microphone": "b"}),
            mock.patch.object(audio, "Feed", side_effect=[first, OSError("failed")]),
            self.assertRaises(OSError),
        ):
            audio.Session(True, True)
        first.close.assert_called_once()

    def test_styles_render_spectrum_changes_to_cairo_without_plasma(self):
        for style in sources.STYLES:
            with self.subTest(style=style):
                image = cairo.ImageSurface(cairo.FORMAT_ARGB32, 400, 300)
                context = cairo.Context(image)
                audio.draw(context, 400, 300, [0.0] * 64, style, "#65d6ce")
                image.flush()
                idle = bytes(image.get_data())
                audio.draw(context, 400, 300, [0.8] * 64, style, "#65d6ce")
                image.flush()
                self.assertNotEqual(idle, bytes(image.get_data()))

    def test_stopped_child_group_is_resumed_before_termination(self):
        process = mock.Mock(pid=1000)
        with mock.patch.object(processes.os, "killpg") as kill:
            processes.stop(process, group=True)
        self.assertEqual(
            kill.call_args_list[:2],
            [mock.call(1000, signal.SIGCONT), mock.call(1000, signal.SIGTERM)],
        )
        process.terminate.assert_not_called()
