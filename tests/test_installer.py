import io
import struct
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from PIL import Image

from drape import installer, wincursors


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
XCURSOR = b"Xcur" + bytes(12)


def make_cur(color, hot=(3, 5), size=32):
    """A real Windows .cur: Pillow writes an .ico, which differs only in type and hotspot fields."""
    buf = io.BytesIO()
    Image.new("RGBA", (size, size), color).save(buf, "ICO", sizes=[(size, size)])
    data = bytearray(buf.getvalue())
    struct.pack_into("<H", data, 2, 2)
    struct.pack_into("<HH", data, 10, *hot)
    return bytes(data)


def make_ani(frames, rate=10):
    def chunk(cid, body):
        return cid + struct.pack("<I", len(body)) + body + (b"\0" if len(body) % 2 else b"")
    anih = struct.pack("<9I", 36, len(frames), len(frames), 0, 0, 0, 0, rate, 1)
    fram = b"fram" + b"".join(chunk(b"icon", f) for f in frames)
    body = b"ACON" + chunk(b"anih", anih) + chunk(b"LIST", fram)
    return b"RIFF" + struct.pack("<I", len(body)) + body


INF = rb"""[Scheme.Reg]
HKCU,"Control Panel\Cursors\Schemes","%SCHEME_NAME%",,"%10%\%CUR_DIR%\%pointer%,%10%\%CUR_DIR%\%help%"

[Wreg]
HKCU,"Control Panel\Cursors",Wait,0x00020000,"%10%\%CUR_DIR%\%busy%"
HKCU,"Control Panel\Cursors",Hand,0x00020000,"%10%\%CUR_DIR%\%link%"

[Strings]
CUR_DIR = "Cursors\Fox"
pointer = "a1.cur"
help    = "a2.cur"
busy    = "a3.ani"
link    = "a4.cur"
"""


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
        make_tar(a, {"Cur/index.theme": b"[Icon Theme]\nName=Cur\n", "Cur/cursors/left_ptr": XCURSOR})
        e = installer.install_file(a, "2", "Cur")
        self.assertTrue((self.p["CURSORS_DIR"] / "Cur/cursors/left_ptr").is_file())
        self.assertEqual(e["components"][0]["provides"], ["cursors"])

    def _load_xcursor(self, path):
        data = path.read_bytes()
        self.assertEqual(data[:4], b"Xcur")
        n = struct.unpack_from("<I", data, 12)[0]
        images = []
        for i in range(n):
            _t, _s, pos = struct.unpack_from("<III", data, 16 + 12 * i)
            _h, _t, _s, _v, w, h, xhot, yhot, delay = struct.unpack_from("<9I", data, pos)
            argb = struct.unpack_from("<I", data, pos + 36)[0]
            images.append((w, h, xhot, yhot, delay, argb))
        return images

    def test_windows_cursor_pack_is_converted_using_install_inf(self):
        # names deliberately meaningless so only Install.inf can map them
        a = self.src / "fox.zip"
        make_zip(a, {"Fox/cursors/Install.inf": INF,
                     "Fox/cursors/a1.cur": make_cur((255, 0, 0, 255), hot=(3, 5)),
                     "Fox/cursors/a2.cur": make_cur((0, 255, 0, 255)),
                     "Fox/cursors/a3.ani": make_ani([make_cur((0, 0, 255, 255)), make_cur((0, 0, 128, 255))]),
                     "Fox/cursors/a4.cur": make_cur((255, 255, 0, 255), hot=(10, 2))})
        e = installer.install_file(a, "20", "Fox pack")
        self.assertEqual(e["components"][0]["name"], "Fox")
        self.assertEqual(e["components"][0]["provides"], ["cursors"])
        cur = self.p["CURSORS_DIR"] / "Fox" / "cursors"

        arrow = self._load_xcursor(cur / "left_ptr")
        self.assertEqual(arrow[0][:4], (32, 32, 3, 5))
        self.assertEqual(arrow[0][5], 0xFFFF0000)  # opaque red, premultiplied ARGB
        self.assertEqual(self._load_xcursor(cur / "pointer")[0][2:4], (10, 2))
        self.assertEqual(self._load_xcursor(cur / "help")[0][5], 0xFF00FF00)
        wait = self._load_xcursor(cur / "watch")  # alias symlink
        self.assertEqual(len(wait), 2)
        self.assertEqual(wait[0][4], 167)  # 10 jiffies = 167 ms
        self.assertTrue((cur / "default").is_symlink())
        self.assertIn("Inherits=Adwaita", (self.p["CURSORS_DIR"] / "Fox/index.theme").read_text())

    def test_windows_cursor_pack_without_inf_uses_filenames(self):
        a = self.src / "plain.zip"
        make_zip(a, {"Normal Select.cur": make_cur((1, 2, 3, 255)), "Busy.ani": make_ani([make_cur((9, 9, 9, 255))]),
                     "Text Select.cur": make_cur((4, 5, 6, 255)), "Link Select.cur": make_cur((7, 8, 9, 255))})
        e = installer.install_file(a, "21", "Plain Pack")
        cur = self.p["CURSORS_DIR"] / "Plain Pack" / "cursors"
        self.assertEqual(e["components"][0]["name"], "Plain Pack")
        for name in ("left_ptr", "wait", "text", "pointer"):
            self.assertTrue((cur / name).exists(), name)

    def test_windows_pack_without_a_pointer_fails_cleanly(self):
        a = self.src / "odd.zip"
        make_zip(a, {"x/zzz1.cur": make_cur((0, 0, 0, 255)), "x/zzz2.cur": make_cur((0, 0, 0, 255)),
                     "x/zzz3.cur": make_cur((0, 0, 0, 255))})
        with self.assertRaises(installer.InstallError):
            installer.install_file(a, "22", "Odd")
        self.assertFalse((self.p["CURSORS_DIR"] / "x").exists())

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

    def test_remove_single_wallpaper_then_last_one_removes_entry(self):
        a = self.src / "walls.zip"
        make_zip(a, {"one.jpg": b"x", "two.jpg": b"y"})
        e = installer.install_file(a, "40", "Two Walls")
        one, two = (c["path"] for c in e["components"])
        installer.remove_component("40", one)
        self.assertFalse(Path(one).exists())
        self.assertTrue(Path(two).exists())
        self.assertEqual(len(installer.load_manifest()["40"]["components"]), 1)
        installer.remove_component("40", two)
        self.assertNotIn("40", installer.load_manifest())
        self.assertFalse((self.p["WALLPAPER_DIR"] / "Two Walls").exists())

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

    def test_absolute_symlink_is_skipped_not_fatal(self):
        a = self.src / "abs.tar.gz"
        make_tar(a, {"C/index.theme": b"[Icon Theme]\nName=C\n", "C/cursors/left_ptr": XCURSOR},
                 links=[("C/cursors/pointer", "/home/author/cursors/left_ptr")])
        installer.install_file(a, "30", "C")
        self.assertTrue((self.p["CURSORS_DIR"] / "C/cursors/left_ptr").is_file())
        self.assertFalse((self.p["CURSORS_DIR"] / "C/cursors/pointer").is_symlink())

    def test_best_file_skips_drafts_and_non_archives(self):
        from drape.pling import Download, Item
        files = [Download(1, "full-drafts-FOR-MODIFICATION.tar.gz", "u", 1, ""),
                 Download(2, "phainon", "u", 1, ""),
                 Download(3, "theme-v1.tar.gz", "u", 1, "")]
        item = Item("1", "x", "a", "", "cursors", "", 0, 0, "", files=files)
        self.assertEqual(item.best_file().index, 3)

    def test_system_theme_types_are_recognised_and_staged(self):
        staging = Path(self.tmp.name) / "staging"
        with mock.patch.object(installer, "STAGING_DIR", staging):
            a = self.src / "splash.tar.gz"
            make_tar(a, {"glow/glow.plymouth": b"[Plymouth Theme]\nName=glow\n", "glow/glow.script": b""})
            e = installer.install_file(a, "50", "Glow splash")
            self.assertEqual(e["components"][0]["system"], "plymouth")
            self.assertTrue((staging / "plymouth/glow/glow.plymouth").is_file())

            a = self.src / "sddm.zip"
            make_zip(a, {"sugar-candy/metadata.desktop": b"[SddmGreeterTheme]\nName=Sugar\n",
                         "sugar-candy/Main.qml": b"", "sugar-candy/theme.conf": b""})
            self.assertEqual(installer.install_file(a, "51", "Sugar")["components"][0]["system"], "sddm")

            a = self.src / "web.zip"
            make_zip(a, {"Neon Glow!/index.html": b"<script src=js/app.js></script>",
                         "Neon Glow!/index.yml": b"name: neon", "Neon Glow!/app.js": b"lightdm.login()"})
            c = installer.install_file(a, "52", "Neon")["components"][0]
            self.assertEqual((c["system"], c["name"]), ("webgreeter", "Neon Glow"))

            a = self.src / "gdm.zip"
            make_zip(a, {"gdm-dark/gnome-shell-theme.gresource": b""})
            with self.assertRaises(installer.InstallError):
                installer.install_file(a, "53", "GDM dark")

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
