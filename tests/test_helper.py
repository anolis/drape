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

    def test_batch_runs_commands_in_order_under_one_lock(self):
        src = self.theme("sugar", {"Main.qml": ""})
        with mock.patch.object(helper.os, "geteuid", lambda: 0), \
                mock.patch.object(helper, "LOCK", self.sys / "lock"):
            rc = helper.main(["batch", "install", "sddm", str(src), ";;", "sddm-theme", "sugar"])
        self.assertEqual(rc, 0)
        self.assertIn("Current=sugar", (self.sys / "sddm.conf.d/90-drape.conf").read_text())

    def test_batch_stops_at_first_failure(self):
        with mock.patch.object(helper.os, "geteuid", lambda: 0), \
                mock.patch.object(helper, "LOCK", self.sys / "lock"):
            rc = helper.main(["batch", "sddm-theme", "missing", ";;", "apt-install", "sddm"])
        self.assertEqual(rc, 1)

    def test_apt_status_lines_become_progress(self):
        self.assertEqual(helper.apt_progress("dlstatus:1:25.0:Retrieving file 3 of 47"), (12.5, "Retrieving file 3 of 47"))
        self.assertEqual(helper.apt_progress("pmstatus:compiz-core:60:Unpacking compiz-core (amd64)"),
                         (80.0, "Unpacking compiz-core (amd64)"))
        self.assertEqual(helper.apt_progress("pmstatus:x:100:Setting up x: done"), (100.0, "Setting up x: done"))
        self.assertIsNone(helper.apt_progress("Reading package lists..."))

    def test_apt_install_streams_progress(self):
        fake = self.src / "apt-get"
        fake.write_text("#!/bin/sh\necho 'Reading package lists...'\necho 'dlstatus:1:50:Retrieving file 1 of 2'\n"
                        "echo 'pmstatus:compiz:100:Setting up compiz'\n")
        fake.chmod(0o755)
        import io, contextlib
        out = io.StringIO()
        with mock.patch.object(helper, "which", lambda name: str(fake) if name == "apt-get" else None), \
                contextlib.redirect_stdout(out):
            helper.cmd_apt_install(ns(packages=["compiz"]))
        text = out.getvalue()
        self.assertIn("PROGRESS 25.0 Retrieving file 1 of 2", text)
        self.assertIn("PROGRESS 100.0 Setting up compiz", text)
        self.assertIn("Reading package lists...", text)  # everything else is still logged

    HISTORY = (
        "Start-Date: 2026-09-26  15:51:35\nCommandline: apt install compizconfig-settings-manager\n"
        "Install: compiz-core:amd64 (2:0.8.18-8, automatic), compizconfig-settings-manager:amd64 (2:0.8.18-5)\n"
        "End-Date: 2026-09-26  15:51:41\n\n"
        "Start-Date: 2026-09-26  16:36:57\nCommandline: /usr/bin/apt-get install -y --no-install-recommends compiz "
        "compiz-mate mate-panel\nInstall: compiz:amd64 (2:0.8.18-8), mate-panel:amd64 (1.26.3-1), "
        "libmate-panel-applet-4-1:amd64 (1.26.3-1, automatic)\nEnd-Date: 2026-09-26  16:37:24\n\n"
        "Start-Date: 2026-09-26  17:00:00\nCommandline: /usr/bin/apt-get install -y --no-install-recommends "
        "-o APT::Status-Fd=1 -o Drape::Install=1 sddm\nInstall: sddm:amd64 (0.21)\nEnd-Date: x\n")

    def _history(self, installed):
        (self.sys / "apt").mkdir()
        (self.sys / "apt" / "history.log").write_text(self.HISTORY)
        dpkg = lambda cmd, **kw: argparse.Namespace(stdout="".join(f"{p} ii \n" for p in cmd[4:] if p in installed))
        return [mock.patch.object(helper, "APT_HISTORY", self.sys / "apt"),
                mock.patch.object(helper.subprocess, "run", dpkg)]

    def test_only_drapes_own_installs_are_removable(self):
        patches = self._history({"compiz", "mate-panel", "libmate-panel-applet-4-1", "compiz-core",
                                 "compizconfig-settings-manager", "sddm"})
        with patches[0], patches[1]:
            ours = helper.drape_installed_packages(["compiz", "compiz-mate", "mate-panel"])
            self.assertEqual(ours, ["compiz", "mate-panel", "libmate-panel-applet-4-1"])  # not the user's ccsm
            self.assertIn("sddm", helper.drape_installed_packages())  # marked install
            with self.assertRaises(helper.HelperError):
                helper.cmd_remove_drape_packages(ns(of=["compiz"], packages=["compizconfig-settings-manager"]))

    def test_removal_refused_when_other_software_needs_it(self):
        patches = self._history({"compiz", "mate-panel", "libmate-panel-applet-4-1"})
        with patches[0], patches[1], \
                mock.patch.object(helper, "removal_plan", lambda pk: (pk + ["my-panel-applet"], ["my-panel-applet"])):
            with self.assertRaises(helper.HelperError) as cm:
                helper.cmd_remove_drape_packages(ns(of=["compiz"], packages=["mate-panel"]))
            self.assertIn("my-panel-applet", str(cm.exception))

    def test_background_file_install_and_remove(self):
        img = self.src / "sky.jpg"
        img.write_bytes(b"jpg")
        helper.cmd_install(ns(kind="background", source=str(img), name=None))
        self.assertEqual((self.sys / "background/sky.jpg").read_bytes(), b"jpg")
        helper.cmd_uninstall(ns(kind="background", name="sky.jpg"))
        self.assertFalse((self.sys / "background/sky.jpg").exists())


if __name__ == "__main__":
    unittest.main()
