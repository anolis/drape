"""Offer to install missing runtime dependencies before importing the app."""

import importlib
import os
import platform
import shlex
import shutil
import subprocess
import sys
import threading


LABELS = {"pillow": "Pillow (PIL)", "requests": "Requests",
          "gi": "PyGObject", "gtk": "GTK 3 bindings"}
PACKAGES = {
    "arch": {"pillow": "python-pillow", "requests": "python-requests",
             "gi": "python-gobject", "gtk": "gtk3"},
    "debian": {"pillow": "python3-pil", "requests": "python3-requests",
               "gi": "python3-gi", "gtk": "gir1.2-gtk-3.0"},
}


def missing():
    result = []
    for key, module in (("pillow", "PIL.Image"), ("requests", "requests"), ("gi", "gi")):
        try:
            importlib.import_module(module)
        except ImportError:
            result.append(key)
    if "gi" not in result:
        try:
            gi = importlib.import_module("gi")
            for namespace, version in (("Gtk", "3.0"), ("Gdk", "3.0"),
                                       ("GdkPixbuf", "2.0"), ("Pango", "1.0"), ("Gio", "2.0")):
                gi.require_version(namespace, version)
                importlib.import_module("gi.repository." + namespace)
        except (ImportError, ValueError):
            result.append("gtk")
    else:
        # GTK's introspection data may also be absent when PyGObject is absent.
        result.append("gtk")
    return result


def install_command(keys):
    try:
        release = platform.freedesktop_os_release()
    except OSError:
        return None
    families = [release.get("ID", ""), *release.get("ID_LIKE", "").split()]
    family = next((f for f in families if f in PACKAGES), None)
    if family is None:
        return None
    manager = "pacman" if family == "arch" else "apt-get"
    if not shutil.which(manager):
        return None
    packages = list(dict.fromkeys(PACKAGES[family][key] for key in keys))
    # Use the installed repository database; don't refresh it without a full upgrade.
    options = ["-S", "--needed", "--noconfirm"] if family == "arch" else ["install", "-y"]
    return [manager, *options, *packages]


class Dialogs:
    """GTK when available; native standalone dialogs can bootstrap GTK itself."""

    def __init__(self):
        self.gtk = None
        self.external = None
        try:
            gi = importlib.import_module("gi")
            gi.require_version("Gtk", "3.0")
            gtk = importlib.import_module("gi.repository.Gtk")
            if gtk.init_check()[0]:
                self.gtk = gtk
        except (ImportError, ValueError, RuntimeError):
            pass
        if self.gtk is None and any(os.environ.get(key) for key in ("DISPLAY", "WAYLAND_DISPLAY", "BROADWAY_DISPLAY")):
            self.external = next((name for name in ("kdialog", "zenity") if shutil.which(name)), None)

    def message(self, text, question=False):
        if self.gtk:
            gtk = self.gtk
            dialog = gtk.MessageDialog(
                modal=True, message_type=gtk.MessageType.QUESTION if question else gtk.MessageType.ERROR,
                buttons=gtk.ButtonsType.YES_NO if question else gtk.ButtonsType.CLOSE,
                text="Drape needs additional packages" if question else "Drape could not start")
            dialog.format_secondary_text(text)
            response = dialog.run()
            dialog.destroy()
            return response == gtk.ResponseType.YES
        if self.external:
            if self.external == "kdialog":
                args = ["kdialog", "--title", "Drape", "--yesno" if question else "--error", text]
            else:
                args = ["zenity", "--title=Drape", "--question" if question else "--error", "--text=" + text]
            return subprocess.run(args).returncode == 0
        print(text, file=sys.stderr)
        return False

    def install(self, command):
        if self.gtk is None:
            # Zenity can provide progress even while GTK's Python bindings are missing.
            progress = None
            if shutil.which("zenity"):
                progress = subprocess.Popen(
                    ["zenity", "--progress", "--pulsate", "--auto-close", "--no-cancel",
                     "--title=Drape", "--text=Installing dependencies…"], stdin=subprocess.PIPE)
            try:
                return run_install(command)
            finally:
                if progress:
                    progress.stdin.close()
                    progress.wait()
        gtk = self.gtk
        glib = importlib.import_module("gi.repository.GLib")
        dialog = gtk.MessageDialog(modal=True, text="Installing dependencies…")
        dialog.format_secondary_text("The package manager is running. This can take a few minutes.")
        spinner = gtk.Spinner()
        dialog.get_content_area().pack_end(spinner, False, False, 12)
        spinner.start()
        dialog.connect("delete-event", lambda *_: True)
        result = []

        def work():
            result.append(run_install(command))
            glib.idle_add(dialog.response, gtk.ResponseType.OK)

        threading.Thread(target=work, daemon=True).start()
        dialog.show_all()
        dialog.run()
        while not result:
            # Escape must not dismiss the progress dialog while the installer runs.
            dialog.run()
        dialog.destroy()
        return result[0]


def run_install(command, terminal=False):
    try:
        proc = subprocess.run(command, text=True, stdout=None if terminal else subprocess.PIPE,
                              stderr=None if terminal else subprocess.STDOUT)
        return proc.returncode == 0, (proc.stdout or "See the package manager output above.")[-4000:]
    except OSError as exc:
        return False, str(exc)


def ensure():
    keys = missing()
    if not keys:
        return True
    terminal = sys.stdin.isatty() and sys.stderr.isatty()
    dialogs = None if terminal else Dialogs()

    def error(text):
        if terminal:
            print(text, file=sys.stderr)
        else:
            dialogs.message(text)

    description = "Missing: " + ", ".join(LABELS[key] for key in keys) + "."
    command = install_command(keys)
    if command is None:
        error(description + "\nInstall these through your distribution's package manager, then launch Drape again.")
        return False
    privilege = "sudo" if terminal else "pkexec"
    if os.geteuid() != 0:
        if not shutil.which(privilege):
            error(description + "\nAutomatic installation requires " + privilege +
                  ".\nRun as administrator: " + shlex.join(command))
            return False
        command = [privilege, *command]
    prompt = description + "\nInstall them now? Administrator authentication may be required.\n\n" + shlex.join(command)
    if terminal:
        print(prompt, file=sys.stderr)
        try:
            answer = input("Install dependencies? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        accepted = answer in ("y", "yes")
    else:
        accepted = dialogs.message(prompt, question=True)
    if not accepted:
        return False
    ok, output = run_install(command, terminal=True) if terminal else dialogs.install(command)
    if not ok:
        error("Dependency installation failed or was canceled.\n\n" + output)
        return False
    # Validate with a fresh interpreter, including when running inside a virtualenv.
    check = subprocess.run([sys.executable, "-m", "drape.dependencies", "--check"],
                           cwd=os.path.dirname(os.path.dirname(__file__)),
                           text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check.returncode:
        error("Packages were installed, but this Python interpreter still cannot load them.\n"
              "Check the interpreter or virtual environment used to launch Drape.\n\n" + check.stderr[-2000:])
        return False
    # Restart as the ordinary user, keeping the original launch mode and arguments.
    os.execv(sys.executable, [sys.executable, *sys.orig_argv[1:]])


if __name__ == "__main__":
    keys = missing()
    if keys:
        print("Missing: " + ", ".join(LABELS[key] for key in keys), file=sys.stderr)
    sys.exit(bool(keys))
