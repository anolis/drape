import unittest

from drape.peek import classify_names


class ClassifyTest(unittest.TestCase):
    def test_parts_from_paths(self):
        self.assertEqual(classify_names(["T/gtk-3.0/gtk.css", "T/metacity-1/metacity-theme-3.xml",
                                         "T/cinnamon/cinnamon.css"]), {"gtk", "wm", "desktop"})
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
        self.assertEqual(classify_names(["pack/red-folder.png", "pack/blue-folder.png", "pack/x.png"]),
                         {"wallpapers"})

    def test_theme_assets_and_previews_are_not_wallpapers(self):
        names = ["T/gtk-3.0/assets/check.png", "T/preview.png", "T/gtk-3.0/gtk.css"]
        self.assertEqual(classify_names(names), {"gtk"})


if __name__ == "__main__":
    unittest.main()
