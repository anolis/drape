"""Opt-in isolated GTK checks: python3 -m tests.ui_integration."""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SMOKE = r"""
import tempfile
import time
from pathlib import Path
from unittest import mock
from drape import desktop, installer, pling, settings
from drape.ui import browse, packs, profile, theme_actions
from drape.ui.gtk import Gtk, GLib
from drape.ui.scroll_state import ScrollState


def pump(duration=0.1):
    end = time.monotonic() + duration
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(0.005)


with tempfile.TemporaryDirectory() as temp:
    settings.PATH = Path(temp) / "settings.json"
    win = Gtk.Window()
    win.set_default_size(800, 500)
    win.busy = {}
    win.pages = {}
    win.query = lambda: ""
    win.sort = lambda: "downloads"
    win.notify = mock.Mock()
    win.show_details = mock.Mock()
    win.refresh_item = mock.Mock()
    win.stack = mock.Mock()
    with (
        mock.patch.object(installer, "load_manifest", return_value={}),
        mock.patch.object(desktop, "supported", return_value=True),
    ):
        page = browse.BrowsePage(win, "gtk")
        win.pages["gtk"] = page
        win.stack.get_visible_child.return_value = page
        win.add(page)
        win.show_all()
        item = pling.Item(
            "1",
            "Old controls",
            "author",
            "",
            "",
            "",
            0,
            0,
            "",
            files=[pling.Download(1, "theme.zip", "url", 1, "")],
        )
        with (
            mock.patch.object(browse._peeks, "submit") as jobs,
            mock.patch.object(
                browse.peek, "inspect_downloads", return_value={1: ({"gtk", "gtk-4.0"}, True)}
            ),
        ):
            page._preflight([item], lambda: page._add([item]), mock.Mock(), page.generation)
            pump(0.25)
            card = page.cards()[0]
            assert card.get_mapped() and card.compatibility_pending
            jobs.call_args.args[0]()
            pump(0.3)
            assert not card.get_child_visible() and not card.compatibility_pending
            assert page.flow.get_visible()
        win.remove(page)
        page.destroy()
    # Profile renders public fields, paginates and opens each upload's own category.
    callbacks = []
    with mock.patch.object(
        profile,
        "run_async",
        side_effect=lambda work, done, error: callbacks.append((work, done, error)),
    ):
        dialog = profile.ProfileDialog(win, "author", "gtk")
        callbacks.pop(0)[1]({"firstname": "Author", "description": "Public bio"})
        upload = pling.Item("2", "Icons", "author", "", "icons", "", 0, 0, "", category="132")
        callbacks.pop(0)[1](([upload], 2))
        assert "Public bio" in dialog.view.bio.get_text()
        card = dialog.view.flow.cards()[0]
        assert card.kind == "icons" and card.item.name == "Icons"
        assert card.image is not None
        dialog.view.all_button.clicked()
        assert dialog.view.busy and dialog.view.page == 1
        dialog.destroy()
        callbacks.pop(0)[1](([], 2))
    # Expansion reparents a live card view into the main window, X restores the page.
    stack = Gtk.Stack()
    previous = Gtk.Box()
    stack.add_named(previous, "previous")
    win.add(stack)
    win.stack = stack
    win.show_all()
    callbacks = []
    with mock.patch.object(
        profile,
        "run_async",
        side_effect=lambda work, done, error: callbacks.append((work, done, error)),
    ):
        dialog = profile.ProfileDialog(win, "author", "gtk")
        view = dialog.view
        dialog.expand()
        assert stack.get_visible_child() is view and not view.closed
        callbacks.pop(0)[1]({"firstname": "Author"})
        callbacks.pop(0)[1](([upload], 1))
        assert len(view.flow.cards()) == 1
        header = view.get_children()[0]
        close = next(button for button in header.get_children() if isinstance(button, Gtk.Button))
        close.clicked()
        assert stack.get_visible_child() is previous and view.closed
    win.remove(stack)
    stack.destroy()
    # Actual GTK scroll adjustment persists between widget instances.
    settings.set("scroll_positions", {"scroll-test": 1200})
    scroller = Gtk.ScrolledWindow()
    scroller.add(Gtk.Label(label="Tall content", height_request=2500))
    tracker = ScrollState(scroller, "scroll-test")
    win.add(scroller)
    win.show_all()
    pump(0.3)
    assert abs(scroller.get_vadjustment().get_value() - 1200) < 2
    scroller.get_vadjustment().set_value(1400)
    pump(0.5)
    assert abs(settings.get("scroll_positions")["scroll-test"] - 1400) < 2
    win.remove(scroller)
    scroller.destroy()
    second = Gtk.ScrolledWindow()
    second.add(Gtk.Label(label="Tall content", height_request=2500))
    tracker2 = ScrollState(second, "scroll-test")
    win.add(second)
    win.show_all()
    pump(0.3)
    assert abs(second.get_vadjustment().get_value() - 1400) < 2
    # Four real component groups stay visible in the variant chooser.
    folder = Path(temp) / "Theme"
    (folder / "gtk-3.0").mkdir(parents=True)
    (folder / "cinnamon").mkdir()
    (folder / "cinnamon/cinnamon.css").write_text(".dialog {} .modal-dialog {}")
    components = [
        dict(name="Theme", path=str(folder), provides=["gtk", "desktop"]),
        dict(name="Icons", path=str(folder), provides=["icons"]),
        dict(name="Wall", path=str(folder / "wall.png"), provides=["wallpapers"]),
    ]
    entry = dict(title="Four part bundle", components=components)
    with (
        mock.patch.object(desktop, "supported", return_value=True),
        mock.patch.object(desktop, "current_desktop", return_value="cinnamon"),
        mock.patch.object(desktop, "cinnamon_version", return_value=(6, 4)),
        mock.patch.object(installer, "load_manifest", return_value={"1": entry}),
    ):
        assert set(packs.choices(entry)) == {"gtk", "desktop", "icons", "wallpapers"}

        def cancel(dialog):
            grid = dialog.get_content_area().get_children()[0]
            assert len([c for c in grid.get_children() if isinstance(c, Gtk.ComboBoxText)]) == 4
            return Gtk.ResponseType.CANCEL

        with mock.patch.object(Gtk.Dialog, "run", side_effect=cancel, autospec=True):
            packs.apply_pack(win, "1")
        with mock.patch.object(Gtk.MessageDialog, "run", return_value=Gtk.ResponseType.CANCEL):
            assert theme_actions.ThemeActions.choose_installed_components(win, "1", entry, "gtk")
    win.destroy()
print(
    "GTK smoke passed: immediate cards, background filtering, uploader pagination, scroll persistence, four component chooser."
)
"""

code = SMOKE
with tempfile.TemporaryDirectory() as directory:
    environment = dict(
        os.environ,
        XDG_RUNTIME_DIR=directory,
        XDG_CONFIG_HOME=directory,
        XDG_DATA_HOME=directory,
        XDG_CACHE_HOME=directory,
    )
    daemon = subprocess.Popen(
        ["broadwayd", "--port=8191", ":91"],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        time.sleep(0.3)
        if daemon.poll() is not None:
            raise RuntimeError(daemon.stderr.read().decode())
        environment.update(
            GDK_BACKEND="broadway",
            BROADWAY_DISPLAY=":91",
            PYTHONPATH=str(Path(__file__).resolve().parents[1]),
        )
        subprocess.run([sys.executable, "-c", code], env=environment, check=True, timeout=30)
    finally:
        daemon.terminate()
        daemon.wait(timeout=5)
        daemon.stderr.close()
