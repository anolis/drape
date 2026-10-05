"""Cross-desktop and bidirectional version regressions for the shared filter."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from drape import desktop, peek, settings, system


class ContextCompatibilityTest(unittest.TestCase):
    def setUp(self):
        for patch in (
            mock.patch.object(settings, "get", side_effect=lambda key: settings.DEFAULTS.get(key)),
            mock.patch.object(desktop, "current_desktop", return_value="cinnamon"),
            mock.patch.object(
                desktop,
                "supported",
                side_effect=lambda part: (
                    part in {"gtk", "desktop", "icons", "cursors", "wallpapers"}
                ),
            ),
            mock.patch.object(desktop, "cinnamon_version", return_value=(6, 4)),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def test_css_generation_mismatches_keep_cinnamon_archives_visible(self):
        name = "Theme/cinnamon/cinnamon.css"
        fixtures = (
            (".modal-dialog {}", True, True),
            (".dialog {}", True, True),
            (".modal-dialog {} .dialog {}", True, True),
        )
        for css, old_ok, new_ok in fixtures:
            parts = peek.classify_names([name] + peek._cinnamon_markers([name], {name: css}, True))
            with self.subTest(css=css):
                for version, expected in (((5, 2), old_ok), ((6, 4), new_ok)):
                    with mock.patch.object(desktop, "cinnamon_version", return_value=version):
                        self.assertEqual(
                            desktop.archive_compatible(parts, True, "desktop"), expected
                        )

    def test_unpacked_theme_checks_both_version_directions(self):
        with tempfile.TemporaryDirectory() as temp:
            css = Path(temp) / "cinnamon/cinnamon.css"
            css.parent.mkdir()
            css.write_text(".dialog {}")
            with mock.patch.object(desktop, "cinnamon_version", return_value=(5, 2)):
                self.assertTrue(desktop.cinnamon_theme_incompatible(temp))
                css.write_text(".dialog {} .modal-dialog {}")
                self.assertFalse(desktop.cinnamon_theme_incompatible(temp))

    def test_mixed_archive_keeps_cinnamon_format_but_requires_consent_for_apply(self):
        parts = {"gtk", "gtk-3.0", "icons", "desktop", "cinnamon-legacy"}
        self.assertTrue(desktop.archive_compatible(parts, True, "desktop"))
        for kind in ("gtk", "icons", "packs"):
            self.assertTrue(desktop.archive_compatible(parts, True, kind), kind)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "gtk-3.0").mkdir()
            (root / "cinnamon").mkdir()
            (root / "cinnamon/cinnamon.css").write_text(".modal-dialog {}")
            self.assertEqual(
                desktop.compatible_parts(dict(path=temp, provides=["gtk", "desktop", "icons"])),
                ["gtk", "icons"],
            )

    def test_window_manager_controls_decoration_format(self):
        with mock.patch.object(desktop, "supported", return_value=True):
            for target in ("wm", "xfwm", "aurorae"):
                with mock.patch.object(desktop, "border_part", return_value=target):
                    for archive_part in ("wm", "xfwm", "aurorae"):
                        self.assertEqual(
                            desktop.archive_compatible({archive_part}, True, "wm"),
                            target == archive_part,
                        )

    def test_unknown_is_distinct_from_compatible_and_bad_variants(self):
        self.assertEqual(desktop.archive_status(set(), False, "gtk"), "unknown")
        self.assertEqual(desktop.archive_status({"gtk", "gtk-4.0"}, True, "gtk"), "incompatible")
        bad = ({"gtk", "gtk-4.0"}, True)
        good = ({"gtk", "gtk-3.0"}, True)
        self.assertEqual(desktop.download_status([bad, good], "gtk"), "compatible")
        self.assertEqual(desktop.download_status([bad, None], "gtk"), "unknown")
        self.assertEqual(desktop.download_status([bad], "gtk"), "incompatible")

    def test_login_filter_uses_the_display_manager(self):
        for manager, expected in (("sddm", True), ("gdm", False), ("lightdm", False)):
            with mock.patch.object(system, "display_manager", return_value=manager):
                self.assertEqual(
                    desktop.archive_compatible({"login", "sddm"}, True, "login"), expected
                )

    def test_showing_incompatible_items_does_not_enable_applying_them(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "cinnamon").mkdir()
            (root / "cinnamon/cinnamon.css").write_text(".dialog {}")
            component = dict(path=temp, name="Modern only", provides=["desktop"])
            with (
                mock.patch.object(desktop, "cinnamon_version", return_value=(5, 2)),
                mock.patch.object(settings, "get", return_value=False),
                mock.patch.object(desktop, "running_wm"),
                mock.patch.object(desktop, "set_") as apply,
            ):
                self.assertEqual(desktop.apply_component(component), [])
                apply.assert_not_called()


if __name__ == "__main__":
    unittest.main()
