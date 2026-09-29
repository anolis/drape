import unittest
from types import SimpleNamespace
from unittest import mock

from drape import app, cli


class CompatibilityUiTest(unittest.TestCase):
    def test_sidebar_follows_capabilities_and_show_all_override(self):
        with mock.patch.object(app.desktop, "supported", side_effect=lambda kind: kind in ("gtk", "icons", "cursors", "wm")):
            self.assertTrue(app.desktop.category_visible("wm"))
            self.assertFalse(app.desktop.category_visible("desktop"))
            self.assertFalse(app.desktop.category_visible("lookandfeel"))
            self.assertTrue(app.desktop.category_visible("lookandfeel", False))
            self.assertTrue(app.desktop.category_visible("login"))
        with mock.patch.object(app.desktop, "supported", return_value=False):
            self.assertFalse(app.desktop.category_visible("wm"))

    def test_apply_control_never_falls_back_to_another_theme_kind(self):
        control = SimpleNamespace(key="pack", kind="gtk")
        entry = {"components": [{"name": "Icons", "provides": ["icons"], "path": "/tmp/icons"}]}
        with mock.patch.object(app.installer, "load_manifest", return_value={"pack": entry}):
            self.assertEqual(app.ApplyControl._components(control), [])

    def test_apply_control_selects_only_the_active_border_format(self):
        control = SimpleNamespace(key="pack", kind="wm")
        marco = {"name": "Marco", "provides": ["wm"], "path": "/tmp/marco"}
        xfwm = {"name": "Xfwm", "provides": ["xfwm"], "path": "/tmp/xfwm"}
        with mock.patch.object(app.installer, "load_manifest", return_value={"pack": {"components": [marco, xfwm]}}), \
                mock.patch.object(app.desktop, "border_part", return_value="xfwm"), \
                mock.patch.object(app.desktop, "compatible_parts", side_effect=lambda c: ["xfwm"]):
            self.assertEqual(app.ApplyControl._components(control), [xfwm])

    def test_gui_and_cli_skip_unsupported_category_requests(self):
        with mock.patch.object(app.settings, "get", return_value=True), \
                mock.patch.object(app.desktop, "scope", return_value=("", "Unsupported here")), \
                mock.patch.object(cli.pling, "search") as search, mock.patch("sys.stderr"):
            self.assertEqual(app.BrowsePage._scope(SimpleNamespace(kind="desktop")), ("", "Unsupported here"))
            cli.main(["search", "desktop"])
            search.assert_not_called()

    def test_cli_uses_window_manager_category(self):
        with mock.patch.object(cli.settings, "get", return_value=True), \
                mock.patch.object(cli.desktop, "scope", return_value=("138", "Xfwm4")), \
                mock.patch.object(cli.pling, "search", return_value=([], 0)) as search, mock.patch("sys.stderr"):
            cli.main(["search", "wm"])
            self.assertEqual(search.call_args.args[-1], "138")
