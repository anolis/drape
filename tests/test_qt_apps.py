import tempfile
import unittest
from pathlib import Path
from unittest import mock

from drape import qt, qt_apps, session


class QtApplicationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        patch = mock.patch.object(qt_apps, "CONFIG_HOME", self.root)
        patch.start()
        self.addCleanup(patch.stop)

    def test_opensnitch_reset_preserves_connection_and_firewall_settings(self):
        path = self.root / "opensnitch/settings.conf"
        path.parent.mkdir()
        path.write_text(
            "[global]\ntheme=dark_red.xml\nserver_address=unix:///tmp/test.sock\n[Rules]\nKeep=yes\n"
        )
        self.assertEqual(qt_apps.opensnitch_theme(), "dark_red.xml")
        qt_apps.opensnitch_system_theme()
        self.assertEqual(qt_apps.opensnitch_theme(), "")
        self.assertIn("server_address=unix:///tmp/test.sock", path.read_text())
        self.assertIn("Keep=yes", path.read_text())

    def test_process_matching_excludes_daemon_and_shell_commands(self):
        for pid, argv in (
            ("10", ["/usr/bin/python3", "/usr/bin/opensnitch-ui"]),
            ("11", ["/usr/bin/opensnitchd"]),
            ("12", ["sh", "-c", "opensnitch-ui"]),
            ("13", ["/usr/bin/opensnitch-ui", "--background"]),
        ):
            folder = self.root / pid
            folder.mkdir()
            (folder / "cmdline").write_bytes(b"\0".join(arg.encode() for arg in argv))
        with mock.patch.object(qt_apps, "PROC", self.root):
            self.assertEqual({pid for pid, _ in qt_apps.opensnitch_processes()}, {10, 13})

    def test_restart_preserves_arguments_and_passes_style_explicitly(self):
        argv = ["/usr/bin/python3", "/usr/bin/opensnitch-ui", "--socket", "unix:///tmp/test.sock"]
        with (
            mock.patch.object(qt_apps, "opensnitch_processes", side_effect=[[(10, argv)], []]),
            mock.patch.object(qt_apps.shutil, "which", return_value="/usr/bin/opensnitch-ui"),
            mock.patch.object(qt_apps.os, "pidfd_open", return_value=99),
            mock.patch.object(qt_apps.os, "close") as close,
            mock.patch.object(qt_apps.signal, "pidfd_send_signal") as send,
            mock.patch.object(qt_apps.subprocess, "Popen") as launch,
            mock.patch.object(qt_apps.time, "sleep"),
        ):
            launch.return_value.poll.return_value = None
            qt_apps.restart_opensnitch()
            launch.assert_called_once()
            self.assertEqual(launch.call_args.args[0], argv)
            self.assertEqual(launch.call_args.kwargs["env"]["QT_STYLE_OVERRIDE"], "kvantum")
            send.assert_called_once_with(99, qt_apps.signal.SIGTERM)
            close.assert_called_once_with(99)

    def test_missing_engine_keeps_existing_gui_running(self):
        process = self.root / "10"
        process.mkdir()
        (process / "maps").write_text("/usr/lib/libQt5Core.so\n")
        with (
            mock.patch.object(qt_apps, "PROC", self.root),
            mock.patch.object(
                qt_apps, "opensnitch_processes", return_value=[(10, ["/usr/bin/opensnitch-ui"])]
            ),
            mock.patch.object(qt_apps.shutil, "which", return_value="/usr/bin/opensnitch-ui"),
            mock.patch.object(qt, "engines", return_value={6}),
            mock.patch.object(qt_apps.os, "pidfd_open") as stop,
            self.assertRaisesRegex(ValueError, "Qt 5"),
        ):
            qt_apps.restart_opensnitch()
        stop.assert_not_called()

    def test_login_paths_cover_zsh_and_existing_bash_profile(self):
        with (
            mock.patch.object(
                session.pwd, "getpwuid", return_value=mock.Mock(pw_shell="/usr/bin/zsh")
            ),
            mock.patch.dict(session.os.environ, {"ZDOTDIR": str(self.root / "zsh")}),
        ):
            self.assertIn(self.root / "zsh/.zprofile", session.login_profiles(self.root))
            self.assertIn(self.root / ".xsessionrc", session.login_profiles(self.root))
        with mock.patch.object(
            session.pwd, "getpwuid", return_value=mock.Mock(pw_shell="/bin/bash")
        ):
            self.assertNotIn(self.root / ".bash_profile", session.login_profiles(self.root))
            (self.root / ".bash_profile").touch()
            self.assertIn(self.root / ".bash_profile", session.login_profiles(self.root))

    def test_configured_state_tracks_profile_setup_and_disable(self):
        with (
            mock.patch.object(qt, "HOME", self.root),
            mock.patch.object(qt, "CONFIG_HOME", self.root),
            mock.patch.object(qt, "THEMES_DIR", self.root / "Kvantum"),
            mock.patch.object(qt, "engines", return_value={5}),
            mock.patch.object(qt, "_activation_style"),
            mock.patch.dict(session.os.environ, {"ZDOTDIR": str(self.root / "zsh")}),
        ):
            self.assertFalse(qt.configured())
            self.assertTrue(qt.enable())
            self.assertTrue(qt.configured())
            self.assertIn(
                "export QT_STYLE_OVERRIDE=kvantum", (self.root / ".xsessionrc").read_text()
            )
            qt.disable()
            self.assertFalse(qt.configured())
            self.assertFalse((self.root / ".xsessionrc").exists())
