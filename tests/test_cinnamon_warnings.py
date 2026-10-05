import io
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from drape import cli, configurations, desktop, installer, peek, settings
from drape.ui import cinnamon_warnings
from drape.ui.gtk import Gtk


class CinnamonWarningsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for patch in (
            mock.patch.object(installer, "MANIFEST", self.root / "installed.json"),
            mock.patch.object(installer, "THEMES_DIR", self.root / "themes"),
            mock.patch.object(desktop, "current_desktop", return_value="cinnamon"),
            mock.patch.object(desktop, "cinnamon_version", return_value=(6, 4)),
            mock.patch.object(desktop, "running_wm", return_value="Muffin"),
            mock.patch.object(desktop, "supported", side_effect=lambda p: p in {"desktop", "gtk"}),
            mock.patch.object(settings, "get", side_effect=lambda key: settings.DEFAULTS.get(key)),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.archive = self.root / "legacy.zip"
        with zipfile.ZipFile(self.archive, "w") as z:
            z.writestr("Legacy/cinnamon/cinnamon.css", ".modal-dialog {}")
            z.writestr("WrongDesktop/xfwm4/themerc", "")

    def install(self, **kwargs):
        return installer.install_file(
            self.archive, "test", "Legacy", only_applicable=True, **kwargs
        )

    def test_declining_install_creates_no_installation_and_does_not_try_to_apply(self):
        confirm = mock.Mock(return_value=False)
        with (
            self.assertRaises(installer.InstallCancelled),
            mock.patch.object(desktop, "set_") as apply,
        ):
            self.install(confirm_cinnamon=confirm)
        self.assertFalse(installer.MANIFEST.exists())
        self.assertFalse(installer.THEMES_DIR.exists())
        apply.assert_not_called()
        self.assertEqual(confirm.call_args.args[0][0]["name"], "Legacy")

    def test_consent_survives_reopen_without_bypassing_wrong_formats_or_poisoning_evidence(self):
        confirm = mock.Mock(return_value=True)
        entry = self.install(confirm_cinnamon=confirm)
        confirm.assert_called_once()
        self.assertEqual(entry["skipped"], ["WrongDesktop"])
        component = installer.load_manifest()["test"]["components"][0]
        self.assertTrue(component["allow_incomplete_cinnamon"])
        self.assertEqual(desktop.compatible_parts(component), ["desktop"])
        parts = peek.installed_parts(entry)
        self.assertIn("cinnamon-legacy", parts)
        self.assertNotIn("cinnamon-modern", parts)
        with mock.patch.object(desktop, "set_", return_value=True) as apply:
            self.assertEqual(desktop.apply_component(component, ["desktop"]), ["desktop"])
        apply.assert_called_once_with("desktop", "Legacy")

    def test_explicit_override_cannot_enable_missing_css_or_unsupported_desktop(self):
        component = {
            "name": "Absent",
            "path": str(self.root),
            "provides": ["desktop"],
            "allow_incomplete_cinnamon": True,
        }
        self.assertEqual(desktop.compatible_parts(component), [])
        with mock.patch.object(desktop, "supported", return_value=False):
            self.assertEqual(desktop.selectable_parts(component), [])

    def test_existing_install_apply_consent_is_explicit_and_persisted(self):
        entry = self.install(allow_incomplete_cinnamon=True)
        component = entry["components"][0]
        component.pop("allow_incomplete_cinnamon")
        installer.save_manifest({"test": entry})
        self.assertEqual(desktop.compatible_parts(component), [])
        self.assertEqual(desktop.selectable_parts(component), ["desktop"])
        with mock.patch.object(cinnamon_warnings, "confirm", return_value=False):
            self.assertFalse(cinnamon_warnings.approve_application(None, component))
        self.assertNotIn(
            "allow_incomplete_cinnamon", installer.load_manifest()["test"]["components"][0]
        )
        with mock.patch.object(cinnamon_warnings, "confirm", return_value=True) as confirm:
            self.assertTrue(cinnamon_warnings.approve_application(None, component))
        self.assertEqual(confirm.call_args.args[2], "Apply anyway")
        self.assertTrue(
            installer.load_manifest()["test"]["components"][0]["allow_incomplete_cinnamon"]
        )
        with mock.patch.object(cinnamon_warnings, "confirm") as confirm:
            self.assertTrue(cinnamon_warnings.approve_application(None, component))
            confirm.assert_not_called()

    def test_saved_configuration_reuses_installed_component_consent(self):
        entry = self.install(allow_incomplete_cinnamon=True)
        component = entry["components"][0]
        with mock.patch.object(configurations, "locate", return_value=Path(component["path"])):
            self.assertEqual(configurations.unavailable({"part": "desktop", "value": "Legacy"}), "")
        restored = configurations._appearance_component("desktop", "Legacy", component["path"])
        self.assertTrue(restored["allow_incomplete_cinnamon"])

    def test_install_dialog_offers_explicit_choice_and_default_cancel(self):
        with mock.patch.object(cinnamon_warnings.Gtk, "MessageDialog") as factory:
            factory.return_value.run.return_value = Gtk.ResponseType.CANCEL
            self.assertFalse(
                cinnamon_warnings.confirm(
                    None, [{"name": "Legacy", "reason": "Missing dialog styles"}]
                )
            )
            factory.return_value.add_buttons.assert_called_once_with(
                "Cancel",
                Gtk.ResponseType.CANCEL,
                "Install anyway",
                Gtk.ResponseType.ACCEPT,
            )
            factory.return_value.set_default_response.assert_called_once_with(
                Gtk.ResponseType.CANCEL
            )
            self.assertIn(
                "switch back", factory.return_value.format_secondary_text.call_args.args[0]
            )

    def test_worker_confirmation_is_dispatched_to_main_thread(self):
        window = SimpleNamespace(_closing=threading.Event())
        queued, scheduled, result = [], threading.Event(), []

        def enqueue(callback):
            queued.append(callback)
            scheduled.set()

        main = threading.get_ident()

        def consent(*args):
            self.assertEqual(threading.get_ident(), main)
            return True

        with (
            mock.patch.object(cinnamon_warnings.GLib, "idle_add", side_effect=enqueue),
            mock.patch.object(cinnamon_warnings, "confirm", side_effect=consent),
        ):
            thread = threading.Thread(
                target=lambda: result.append(cinnamon_warnings.confirm_install(window, []))
            )
            thread.start()
            self.assertTrue(scheduled.wait(2))
            self.assertTrue(thread.is_alive())
            queued[0]()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, [True])

    def test_close_cancels_queued_confirmation_without_stale_dialog(self):
        window = SimpleNamespace(_closing=threading.Event())
        queued, scheduled, result = [], threading.Event(), []

        def enqueue(callback):
            queued.append(callback)
            scheduled.set()

        with (
            mock.patch.object(cinnamon_warnings.GLib, "idle_add", side_effect=enqueue),
            mock.patch.object(cinnamon_warnings, "confirm") as confirm,
        ):
            thread = threading.Thread(
                target=lambda: result.append(cinnamon_warnings.confirm_install(window, []))
            )
            thread.start()
            self.assertTrue(scheduled.wait(2))
            window._closing.set()
            thread.join(2)
            queued[0]()
            confirm.assert_not_called()
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, [False])

    def test_terminal_confirmation_accepts_only_yes(self):
        warnings = [{"name": "Legacy", "reason": "Missing dialog styles"}]
        with mock.patch("sys.stderr", io.StringIO()):
            for reply, expected in [("y", True), ("yes", True), ("", False), ("n", False)]:
                with self.subTest(reply=reply), mock.patch("builtins.input", return_value=reply):
                    self.assertEqual(cli._confirm_cinnamon(warnings), expected)
