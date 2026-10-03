import unittest
import tempfile
from pathlib import Path
from unittest import mock

from drape import peek
from drape.peek import classify_names


class ClassifyTest(unittest.TestCase):
    def test_parts_from_paths(self):
        self.assertEqual(
            classify_names(
                [
                    "T/gtk-3.0/gtk.css",
                    "T/metacity-1/metacity-theme-3.xml",
                    "T/cinnamon/cinnamon.css",
                ]
            ),
            {"gtk", "gtk-3.0", "wm", "desktop"},
        )
        self.assertEqual(classify_names(["C/index.theme", "C/cursors/left_ptr"]), {"cursors"})
        self.assertEqual(classify_names(["pack/Normal.cur", "pack/Busy.ani"]), {"cursors"})
        self.assertEqual(classify_names(["s/s.plymouth", "s/s.script"]), {"plymouth"})
        self.assertEqual(classify_names(["x/metadata.desktop", "x/Main.qml"]), {"login"})

    def test_icon_theme_layouts(self):
        std = [f"Frost/32x32/places/f{i}.png" for i in range(12)]
        self.assertEqual(classify_names(std), {"icons"})
        reversed_layout = [f"Slot/status/scalable/s{i}.svg" for i in range(12)]
        self.assertEqual(classify_names(reversed_layout), {"icons"})
        hidpi = ["P/index.theme", "P/24x24@2x/apps/a.svg"]
        self.assertEqual(classify_names(hidpi), {"icons"})

    def test_loose_pictures_are_wallpapers_not_icons(self):
        self.assertEqual(
            classify_names(["pack/red-folder.png", "pack/blue-folder.png", "pack/x.png"]),
            {"wallpapers"},
        )

    def test_theme_assets_and_previews_are_not_wallpapers(self):
        names = ["T/gtk-3.0/assets/check.png", "T/preview.png", "T/gtk-3.0/gtk.css"]
        self.assertEqual(classify_names(names), {"gtk", "gtk-3.0"})

    def test_window_manager_formats_are_distinct(self):
        self.assertEqual(classify_names(["T/xfwm4/themerc"]), {"xfwm"})
        self.assertEqual(classify_names(["T/metacity-1/metacity-theme-1.xml"]), {"wm"})
        self.assertEqual(
            classify_names(["T/gnome-shell/gnome-shell.css", "T/gnome-shell/a.png"]),
            {"gnome-shell"},
        )

    def test_nested_archive_is_not_proof_of_incompatibility(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(peek, "CACHE", Path(tmp) / "peek.json"),
            mock.patch.object(
                peek, "list_archive", return_value=(["theme.tar.gz", "preview.png"], True)
            ),
        ):
            _parts, complete = peek.contents("1", "https://example.invalid/theme.zip", "theme.zip")
            self.assertFalse(complete)


if __name__ == "__main__":
    unittest.main()
