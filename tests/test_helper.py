import argparse
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from drape import helper


def ns(**kw):
    return argparse.Namespace(**kw)


class HelperTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.sys = root / "sys"
        self.src = root / "src"
        self.src.mkdir()
        dirs = {k: self.sys / k for k in helper.DIRS}
        for k, v in dirs.items():
            v.mkdir(parents=True)
        patches = [
            mock.patch.dict(helper.DIRS, dirs),
            mock.patch.object(helper, "GREETER_CONF", {
                "slick-greeter": (self.sys / "slick-greeter.conf", "Greeter")}),
            mock.patch.object(helper, "SDDM_DROPIN", self.sys / "sddm.conf.d/90-drape.conf"),
            mock.patch.object(helper, "PLYMOUTHD_CONF", self.sys / "plymouthd.conf"),
            mock.patch.object(helper, "uses_alternatives", lambda: False),
            mock.patch.object(helper, "which", lambda name: None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def theme(self, name, files, links=()):
        d = self.src / name
        for rel, data in files.items():
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            (d / rel).write_text(data)
        for rel, to in links:
            os.symlink(to, d / rel)
        return d

    def test_install_marks_and_uninstall_removes(self):
        src = self.theme("Neat", {"index.theme": "[Icon Theme]\n", "48/a.png": "x"}, links=[("48/b.png", "a.png")])
        helper.cmd_install(ns(kind="icons", source=str(src), name=None))
        dest = self.sys / "icons/Neat"
        self.assertTrue((dest / helper.MARKER).is_file())
        self.assertTrue((dest / "48/b.png").is_symlink())
        helper.cmd_uninstall(ns(kind="icons", name="Neat"))
        self.assertFalse(dest.exists())

    def test_never_touches_system_themes_it_did_not_install(self):
        (self.sys / "gtk/Adwaita").mkdir()
        src = self.theme("Adwaita", {"gtk-3.0/gtk.css": ""})
        with self.assertRaises(helper.HelperError):
            helper.cmd_install(ns(kind="gtk", source=str(src), name=None))
        with self.assertRaises(helper.HelperError):
            helper.cmd_uninstall(ns(kind="gtk", name="Adwaita"))
        self.assertTrue((self.sys / "gtk/Adwaita").is_dir())

    def test_links_escaping_the_theme_are_dropped(self):
        src = self.theme("T", {"gtk-3.0/gtk.css": ""}, links=[("evil", "/etc/shadow"), ("up", "../../x")])
        helper.cmd_install(ns(kind="gtk", source=str(src), name=None))
        self.assertFalse((self.sys / "gtk/T/evil").is_symlink())
        self.assertFalse((self.sys / "gtk/T/up").is_symlink())

    def test_links_rejected_for_plymouth_and_login_themes(self):
        src = self.theme("S", {"Main.qml": "", "metadata.desktop": ""}, links=[("x", "Main.qml")])
        with self.assertRaises(helper.HelperError):
            helper.cmd_install(ns(kind="sddm", source=str(src), name=None))

    def test_invalid_names_rejected(self):
        for bad in ("../etc", ".", "a/b", "-rf"):
            with self.assertRaises(helper.HelperError):
                helper.validate_name(bad)

    def test_plymouth_paths_rewritten_to_install_location(self):
        src = self.theme("glow", {
            "glow.plymouth": "[Plymouth Theme]\nName=glow\nModuleName=script\n\n[script]\n"
                             "ImageDir=/usr/share/plymouth/themes/glow\nScriptFile=/home/me/glow/glow.script\n",
            "glow.script": "", "progress-0.png": ""})
        helper.cmd_install(ns(kind="plymouth", source=str(src), name=None))
        text = (self.sys / "plymouth/glow/glow.plymouth").read_text()
        self.assertIn(f"ImageDir={self.sys / 'plymouth/glow'}", text)
        self.assertIn(f"ScriptFile={self.sys / 'plymouth/glow/glow.script'}", text)

    def test_greeter_settings_keep_other_lines(self):
        conf = self.sys / "slick-greeter.conf"
        conf.write_text("[Greeter]\n# mine\ndraw-grid=false\ntheme-name=Old\n")
        bg = self.sys / "background/sky.jpg"
        helper.cmd_greeter_set(ns(greeter="slick-greeter",
                                  values=["theme-name=Midnight-Gray", f"background={bg}"]))
        text = conf.read_text()
        self.assertIn("# mine\ndraw-grid=false\ntheme-name=Midnight-Gray", text)
        self.assertIn(f"background={bg}", text)
        self.assertNotIn("Old", text)

    def test_greeter_settings_validated(self):
        with self.assertRaises(helper.HelperError):
            helper.cmd_greeter_set(ns(greeter="slick-greeter", values=["background=/home/me/x.jpg"]))
        with self.assertRaises(helper.HelperError):
            helper.cmd_greeter_set(ns(greeter="slick-greeter", values=["exec=/bin/sh"]))
        with self.assertRaises(helper.HelperError):
            helper.cmd_greeter_set(ns(greeter="evil-greeter", values=["theme-name=x"]))

    def test_sddm_theme_dropin(self):
        helper.cmd_install(ns(kind="sddm", source=str(self.theme("sugar", {"Main.qml": ""})), name=None))
        helper.cmd_sddm_theme(ns(name="sugar"))
        self.assertIn("[Theme]\nCurrent=sugar", (self.sys / "sddm.conf.d/90-drape.conf").read_text())

    def test_only_whitelisted_packages(self):
        with self.assertRaises(helper.HelperError):
            helper.cmd_apt_install(ns(packages=["sddm", "openssh-server"]))

    def test_background_file_install_and_remove(self):
        img = self.src / "sky.jpg"
        img.write_bytes(b"jpg")
        helper.cmd_install(ns(kind="background", source=str(img), name=None))
        self.assertEqual((self.sys / "background/sky.jpg").read_bytes(), b"jpg")
        helper.cmd_uninstall(ns(kind="background", name="sky.jpg"))
        self.assertFalse((self.sys / "background/sky.jpg").exists())


if __name__ == "__main__":
    unittest.main()
