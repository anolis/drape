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
from drape.ui import browse, installed, packs, profile, theme_actions
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
            mock.patch.object(browse.compatibility.Index, "read_many", return_value={item.id: {1: ({"gtk", "gtk-4.0"}, True)}}),
            mock.patch.object(browse.peek, "inspect_downloads", side_effect=AssertionError("GUI must not scan archives")),
            mock.patch.object(browse.peek, "contents", side_effect=AssertionError("GUI must not inspect archives")),
        ):
            page._preflight([item], lambda: page._add([item]), mock.Mock(), page.generation)
            pump(0.25)
            card = page.cards()[0]
            assert not card.get_child_visible() and not card.compatibility_pending
            assert page.flow.get_visible()
        unknown = pling.Item("unknown", "Not indexed", "author", "", "", "", 0, 0, "", files=[pling.Download(1, "theme.zip", "url", 1, "")])
        with mock.patch.object(browse.compatibility.Index, "read_many", return_value={unknown.id: {}}), mock.patch.object(browse.peek, "inspect_downloads", side_effect=AssertionError("GUI must not scan")):
            page._preflight([unknown], lambda: page._add([unknown]), mock.Mock(), page.generation)
            pump(0.25)
            card = next(card for card in page.cards() if card.item.id == "unknown")
            assert card.get_child_visible() and card.compatibility_pending
            assert card.compatibility_note.get_text() == "Compatibility unverified"
        win.remove(page)
        page.destroy()
    # Off-screen removals keep row allocations, including cards above the viewport.
    from drape.ui.card_transitions import CardFlow, FadingCard

    scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
    flow = CardFlow(min_children_per_line=1, max_children_per_line=1)
    scroller.add(flow)
    cards = []
    for index in range(20):
        card = FadingCard()
        card.add(Gtk.Label(label=f"Card {index}", height_request=120))
        cards.append(card)
        flow.add(card)
    win.add(scroller)
    win.show_all()
    pump(0.3)
    adjustment = scroller.get_vadjustment()
    initial_height = adjustment.get_upper()
    last = cards[-1]
    assert last.get_mapped() and not flow.in_view(last)
    rejected = {last}
    flow.set_card_filter(lambda card: card not in rejected)
    pump(0.25)
    assert last._included and last in flow._deferred
    assert adjustment.get_upper() == initial_height
    adjustment.set_value(1000)
    pump(0.1)
    first = cards[0]
    middle = cards[9]
    assert not flow.in_view(first)
    before = middle.translate_coordinates(scroller, 0, 0)
    rejected.add(first)
    flow.refilter()
    pump(0.25)
    assert first._included and first in flow._deferred
    assert adjustment.get_upper() == initial_height
    assert middle.translate_coordinates(scroller, 0, 0) == before
    adjustment.set_value(0)
    pump(0.3)
    assert not first._included and first not in flow._deferred
    assert last._included and last in flow._deferred
    adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
    pump(0.3)
    assert not last._included and not flow._deferred
    # Restoring the filter cancels a queued removal before scrolling back to it.
    restored = cards[2]
    rejected.add(restored)
    flow.refilter()
    assert restored in flow._deferred
    rejected.remove(restored)
    flow.refilter()
    assert restored._included and restored not in flow._deferred
    # Actual deletion follows the same rule; explicit reload can still clear the grid.
    adjustment.set_value(0)
    pump(0.1)
    removed = cards[10]
    removed.dismiss()
    assert removed.get_parent() is flow and removed in flow._deferred
    adjustment.set_value(removed.get_allocation().y - 50)
    pump(0.3)
    assert removed.get_parent() is None
    cards[15].dismiss()
    flow.clear()
    pump(0.3)
    assert not flow.get_children() and not flow._deferred
    win.remove(scroller)
    scroller.destroy()
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

    # Large Installed collections must not hydrate hidden tabs or off-screen cards.
    large = {
        str(i): dict(title=f"Theme {i:04}", components=[dict(name=f"Theme {i}", path=f"/themes/{i}", provides=["icons", "cursors"])])
        for i in range(500)
    }
    with (
        mock.patch.object(installer, "load_manifest", return_value=large) as records,
        mock.patch.object(desktop, "category_visible", return_value=True),
        mock.patch.object(desktop, "supported", return_value=True),
        mock.patch.object(desktop, "compatible_parts", side_effect=lambda c: c["provides"]) as compatibility,
        mock.patch.object(installed, "in_use", return_value=False),
        mock.patch.object(installed, "entry_image") as pictures,
    ):
        window = Gtk.Window()
        window.set_default_size(800, 500)
        settings.set("scroll_positions", {"installed:cursors": 8000})
        window.installed = installed.InstalledPage(window, [pling.Kind("icons", "Icons", ""), pling.Kind("cursors", "Cursors", "")])
        window.add(window.installed)
        window.installed.tabs.set_visible_child_name("icons")
        started = time.monotonic()
        window.installed.load()
        load_seconds = time.monotonic() - started
        assert records.call_count == 1
        assert compatibility.call_count == 500
        assert len(window.installed.grids["icons"][2].cards()) == 500
        assert not window.installed.grids["cursors"][2].cards()
        assert not window.installed.grids["active"][2].cards()
        assert pictures.call_count == 0
        window.show_all()
        pump(0.4)
        assert 0 < pictures.call_count < 30, pictures.call_count
        assert records.call_count == 1, records.call_count
        flow = window.installed.grids["icons"][2]
        visible = [card for card in flow.cards() if flow.in_view(card)]
        assert visible and all(card._draw_handler is None for card in visible)
        images_before = pictures.call_count
        adjustment = window.installed.grids["icons"][1].get_vadjustment()
        adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
        pump(0.3)
        assert pictures.call_count > images_before
        window.installed.select_all()
        assert len(window.installed.selected) == 500
        window.installed.tabs.set_visible_child_name("cursors")
        pump(0.3)
        assert abs(window.installed.grids["cursors"][1].get_vadjustment().get_value() - 8000) < 2
        assert len(window.installed.grids["cursors"][2].cards()) == 500
        assert all(card.select_check.get_active() for card in window.installed.grids["cursors"][2].cards())
        assert records.call_count == 1
        window.destroy()
        print(f"Installed stress check: 500 items loaded in {load_seconds:.3f}s; only viewport cards hydrated.")
print(
    "GTK smoke passed: immediate cards, deferred off-screen removals, index-only compatibility, uploader pagination, scroll persistence, four component chooser."
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
