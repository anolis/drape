import subprocess
import sys
import unittest
from unittest import mock

from drape import dependencies as deps


class DependenciesTest(unittest.TestCase):
    def test_cachyos_uses_arch_packages(self):
        with mock.patch.object(deps.platform, "freedesktop_os_release", return_value={"ID": "cachyos", "ID_LIKE": "arch"}), \
                mock.patch.object(deps.shutil, "which", return_value="/usr/bin/pacman"):
            self.assertEqual(deps.install_command(["pillow", "gi", "gtk"]),
                             ["pacman", "-S", "--needed", "--noconfirm", "python-pillow", "python-gobject", "gtk3"])

    def test_debian_and_unknown_distribution(self):
        with mock.patch.object(deps.platform, "freedesktop_os_release", return_value={"ID": "ubuntu", "ID_LIKE": "debian"}), \
                mock.patch.object(deps.shutil, "which", return_value="/usr/bin/apt-get"):
            self.assertEqual(deps.install_command(["pillow"]), ["apt-get", "install", "-y", "python3-pil"])
        with mock.patch.object(deps.platform, "freedesktop_os_release", return_value={"ID": "unknown"}):
            self.assertIsNone(deps.install_command(["pillow"]))

    def setup_prompt(self, terminal=True):
        patches = [mock.patch.object(deps, "missing", return_value=["pillow"]),
                   mock.patch.object(deps, "install_command", return_value=["pacman", "-S", "python-pillow"]),
                   mock.patch.object(deps.sys.stdin, "isatty", return_value=terminal),
                   mock.patch.object(deps.sys.stderr, "isatty", return_value=terminal),
                   mock.patch.object(deps.os, "geteuid", return_value=1000),
                   mock.patch.object(deps.shutil, "which", side_effect=lambda name: "/usr/bin/" + name)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_declining_in_terminal_never_installs(self):
        self.setup_prompt()
        with mock.patch("builtins.input", return_value="n"), mock.patch.object(deps, "run_install") as install:
            self.assertFalse(deps.ensure())
            install.assert_not_called()

    def test_terminal_acceptance_installs_and_restarts(self):
        self.setup_prompt()
        with mock.patch("builtins.input", return_value="y"), \
                mock.patch.object(deps, "run_install", return_value=(True, "")) as install, \
                mock.patch.object(deps.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")), \
                mock.patch.object(deps.os, "execv") as restart:
            deps.ensure()
            install.assert_called_once_with(["sudo", "pacman", "-S", "python-pillow"], terminal=True)
            restart.assert_called_once_with(sys.executable, [sys.executable, *sys.orig_argv[1:]])

    def test_graphical_acceptance_uses_pkexec(self):
        self.setup_prompt(terminal=False)
        with mock.patch.object(deps, "Dialogs") as dialogs, \
                mock.patch.object(deps.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")), \
                mock.patch.object(deps.os, "execv") as restart:
            dialogs.return_value.message.return_value = True
            dialogs.return_value.install.return_value = (True, "")
            deps.ensure()
            dialogs.return_value.install.assert_called_once_with(["pkexec", "pacman", "-S", "python-pillow"])
            restart.assert_called_once()

    def test_graphical_cancel_never_installs(self):
        self.setup_prompt(terminal=False)
        with mock.patch.object(deps, "Dialogs") as dialogs:
            dialogs.return_value.message.return_value = False
            self.assertFalse(deps.ensure())
            dialogs.return_value.install.assert_not_called()

    def test_failed_install_or_unavailable_python_package_never_restarts(self):
        self.setup_prompt()
        for installed, check_code in ((False, 0), (True, 1)):
            with mock.patch("builtins.input", return_value="y"), \
                    mock.patch.object(deps, "run_install", return_value=(installed, "canceled")), \
                    mock.patch.object(deps.subprocess, "run", return_value=subprocess.CompletedProcess([], check_code, "", "Missing PIL")), \
                    mock.patch.object(deps.os, "execv") as restart:
                self.assertFalse(deps.ensure())
                restart.assert_not_called()

    def test_real_launcher_handles_missing_pillow_before_app_import(self):
        code = '''
import importlib.abc, runpy, sys
from unittest.mock import patch
class MissingPillow(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'PIL' or fullname.startswith('PIL.'):
            raise ModuleNotFoundError("No module named 'PIL'", name='PIL')
sys.meta_path.insert(0, MissingPillow())
from drape import dependencies
with patch.object(dependencies, 'install_command', return_value=None):
    sys.argv = ['bin/drape']
    runpy.run_path('bin/drape', run_name='__main__')
'''
        proc = subprocess.run([sys.executable, "-c", code], text=True, capture_output=True,
                              env={"PATH": "/usr/bin:/bin"}, timeout=10)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Pillow (PIL)", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)


if __name__ == "__main__":
    unittest.main()
