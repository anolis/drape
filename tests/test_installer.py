import io
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from drape import installer


def make_tar(path, files, links=()):
    with tarfile.open(path, "w:gz") as t:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
        for name, target in links:
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            t.addfile(info)


def make_zip(path, files):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)


ICON_INDEX = b"[Icon Theme]\nName=Test\nDirectories=48x48/apps\n"


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.src = root / "src"
        self.src.mkdir()
        home = root / "home"
        paths = {
            "ICONS_DIR": home / ".local/share/icons",
            "CURSORS_DIR": home / ".icons",
            "THEMES_DIR": home / ".themes",
            "WALLPAPER_DIR": home / ".local/share/backgrounds/drape",
            "MANIFEST": home / ".local/share/drape/installed.json",
            "CINNAMON_BG_FOLDERS": home / ".config/cinnamon/backgrounds/user-folders.lst",
        }
        for k, v in paths.items():
            p = mock.patch.object(installer, k, v)
            p.start()
            self.addCleanup(p.stop)
        self.p = paths

    def tearDown(self):
        self.tmp.cleanup()

    def test_icon_theme(self):
        a = self.src / "icons.tar.gz"
        make_tar(a, {"MyIcons/index.theme": ICON_INDEX, "MyIcons/48x48/apps/foo.png": b"x"},
                 links=[("MyIcons/48x48/apps/bar.png", "foo.png")])
        e = installer.install_file(a, "1", "My Icons")
        dest = self.p["ICONS_DIR"] / "MyIcons"
        self.assertTrue((dest / "48x48/apps/foo.png").is_file())
        self.assertTrue((dest / "48x48/apps/bar.png").is_symlink())
        self.assertEqual(e["components"][0]["provides"], ["icons"])

    def test_icon_theme_with_gtk_extras_is_still_icons(self):
        a = self.src / "i.tar.gz"
        make_tar(a, {"I/index.theme": ICON_INDEX, "I/gtk-3.0/gtk.css": b"", "I/48x48/apps/a.png": b""})
        e = installer.install_file(a, "12", "I")
        self.assertEqual(e["components"][0]["provides"], ["icons"])
        self.assertTrue((self.p["ICONS_DIR"] / "I").is_dir())

    def test_cursor_theme_goes_to_dot_icons(self):
        a = self.src / "cur.tar.gz"
        make_tar(a, {"Cur/index.theme": b"[Icon Theme]\nName=Cur\n", "Cur/cursors/left_ptr": b"x"})
        e = installer.install_file(a, "2", "Cur")
        self.assertTrue((self.p["CURSORS_DIR"] / "Cur/cursors/left_ptr").is_file())
        self.assertEqual(e["components"][0]["provides"], ["cursors"])

    def test_full_theme_with_variants_and_nested_archive(self):
        inner = self.src / "inner.zip"
        make_zip(inner, {"Dark/gtk-3.0/gtk.css": b"", "Dark/metacity-1/metacity-theme-3.xml": b""})
        a = self.src / "pack.tar.xz"
        with tarfile.open(a, "w:xz") as t:
            info = tarfile.TarInfo("Light/gtk-3.0/gtk.css")
            t.addfile(info, io.BytesIO(b""))
            info = tarfile.TarInfo("Light/cinnamon/cinnamon.css")
            t.addfile(info, io.BytesIO(b""))
            t.add(inner, "variants/dark.zip")
        e = installer.install_file(a, "3", "Pack")
        comps = {c["name"]: c["provides"] for c in e["components"]}
        self.assertEqual(comps, {"Light": ["desktop", "gtk"], "Dark": ["gtk", "wm"]})
        self.assertTrue((self.p["THEMES_DIR"] / "Dark/metacity-1").is_dir())

    def test_theme_at_archive_root_uses_title(self):
        a = self.src / "flat.zip"
        make_zip(a, {"gtk-3.0/gtk.css": b""})
        e = installer.install_file(a, "4", "Flat/Theme")
        self.assertEqual(e["components"][0]["name"], "Flat_Theme")

    def test_wallpapers_and_folder_registration(self):
        a = self.src / "walls.zip"
        make_zip(a, {"walls/one.jpg": b"x", "walls/two.png": b"y", "readme.txt": b""})
        e = installer.install_file(a, "5", "Nice Walls")
        folder = self.p["WALLPAPER_DIR"] / "Nice Walls"
        self.assertTrue((folder / "one.jpg").is_file())
        self.assertIn(str(folder), self.p["CINNAMON_BG_FOLDERS"].read_text())
        self.assertEqual(len(e["components"]), 2)

    def test_single_image_download(self):
        img = self.src / "sunset.png"
        img.write_bytes(b"\x89PNG")
        installer.install_file(img, "6", "Sunset")
        self.assertTrue((self.p["WALLPAPER_DIR"] / "Sunset/sunset.png").is_file())

    def test_remove_and_conflicts(self):
        a = self.src / "t.zip"
        make_zip(a, {"T/gtk-3.0/gtk.css": b""})
        installer.install_file(a, "7", "T")
        # reinstalling the same item replaces it
        installer.install_file(a, "7", "T")
        # another item cannot clobber it
        with self.assertRaises(installer.InstallError):
            installer.install_file(a, "8", "T other")
        installer.remove("7")
        self.assertFalse((self.p["THEMES_DIR"] / "T").exists())
        self.assertEqual(installer.load_manifest(), {})

    def test_refuses_to_overwrite_foreign_theme(self):
        (self.p["THEMES_DIR"] / "T").mkdir(parents=True)
        a = self.src / "t.zip"
        make_zip(a, {"T/gtk-3.0/gtk.css": b""})
        with self.assertRaises(installer.InstallError):
            installer.install_file(a, "9", "T")
        installer.install_file(a, "9", "T", replace_foreign=True)

    def test_path_traversal_rejected(self):
        a = self.src / "evil.tar.gz"
        make_tar(a, {"../../escape.txt": b"x", "T/gtk-3.0/gtk.css": b""})
        with self.assertRaises(Exception):
            installer.install_file(a, "10", "Evil")
        self.assertFalse((Path(self.tmp.name) / "escape.txt").exists())

    def test_nothing_recognisable(self):
        a = self.src / "junk.zip"
        make_zip(a, {"README": b"hi"})
        with self.assertRaises(installer.InstallError):
            installer.install_file(a, "11", "Junk")

    def test_remove_only_touches_install_dirs(self):
        outside = Path(self.tmp.name) / "precious"
        outside.mkdir()
        m = {"x": {"title": "x", "paths": [str(outside)], "components": []}}
        installer.save_manifest(m)
        installer.remove("x")
        self.assertTrue(outside.exists())


if __name__ == "__main__":
    unittest.main()
