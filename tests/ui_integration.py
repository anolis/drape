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
from drape import desktop, installer, pling, settings, qt, libadwaita
from drape.ui import browse, installed, packs, profile, theme_actions
from drape.ui.gtk import Gdk, Gtk, GLib
from drape.ui.scroll_state import ScrollState
from drape.ui.qt_settings import QtSettingsPage
from drape.ui.navigation import GROUPS, build_sidebar
from drape.ui.libadwaita_settings import LibadwaitaSettingsPage
from drape.ui.video_wallpapers import VideoWallpapersPage
from drape import video_wallpapers as videos
from drape import wallpaper_sources as sources, wallpaper_xscreensaver as saver, wallpaper_audio as audio
from drape import wallpaper_animation_settings as animation_settings
from drape.ui.animation_settings import edit as edit_animation
from tests.test_wallpaper_animation_settings import XML
import xml.etree.ElementTree as ET


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
    win.go_to = mock.Mock()
    win._sidebar_list = mock.Mock()
    # Video UI persists file references and activates the tray only after a
    # successful playback request. No desktop player runs in this GTK check.
    videos.PATH = Path(temp) / "videos.json"
    sources.PATH = Path(temp) / "wallpaper-sources.json"
    animation_settings.PATH = Path(temp) / "xscreensaver-settings.json"
    fixture = Path(temp) / "wallpaper.mp4"
    fixture.touch()
    videos.remember(str(fixture), "fit")
    video_page = VideoWallpapersPage(win)
    app = mock.Mock()
    win.get_application = lambda: app
    video_page.quit_button.clicked()
    app.quit.assert_called_once()
    with mock.patch.object(videos, "request", return_value={"state": "stopped", "path": ""}):
        video_page.load()
        pump()
        assert video_page.selector.get_active_id() == str(fixture)
        assert video_page.fit.get_active_id() == "fit"
        assert not video_page.pause_button.get_sensitive()
        with mock.patch.object(videos, "play", return_value={"state": "starting", "path": str(fixture)}) as play:
            video_page._play()
            pump()
            play.assert_called_once_with(str(fixture), "fit")
            app.wallpaper_tray.enable.assert_called_once()
        video_page.update_status({"state": "paused", "path": str(fixture)})
        assert video_page.pause_button.get_label() == "Resume"
        assert video_page.stop_button.get_sensitive()
        with mock.patch.object(videos, "forget") as forget:
            video_page._remove()
            pump()
            forget.assert_called_once_with(str(fixture))
    video_page.destroy()
    # Source changes share tray/lifecycle controls, without requiring mpv for
    # animations or accidentally enabling microphone capture.
    animation = {"name": "fuzzyflakes", "label": "Fuzzy Flakes", "description": "Falling flakes", "delay": "--delay", "executable": "/fake/fuzzyflakes"}
    with mock.patch.object(saver, "catalog", return_value=[animation]), mock.patch.object(audio, "available", return_value=True), mock.patch.object(videos, "request", return_value={"state": "stopped", "path": ""}):
        live_page = VideoWallpapersPage(win)
        live_page.load()
        pump()
        live_page.sources.source.set_active_id("xscreensaver")
        live_page.sources.fps.set_active_id("15")
        assert live_page.play_button.get_sensitive()
        assert live_page.sources.stack.get_visible_child_name() == "xscreensaver"
        with mock.patch.object(videos, "play", return_value={"state": "starting", "source": "xscreensaver", "path": "fuzzyflakes", "name": "Fuzzy Flakes"}) as play:
            live_page._play()
            pump()
            play.assert_called_once_with("fuzzyflakes", "fill", source="xscreensaver", fps=15)
        live_page.update_status({"state": "stopped", "path": ""})
        live_page.sources.source.set_active_id("audio")
        assert live_page.sources.desktop_audio.get_active()
        assert not live_page.sources.microphone.get_active()
        live_page.sources.desktop_audio.set_active(False)
        assert not live_page.play_button.get_sensitive()
        live_page.sources.microphone.set_active(True)
        assert live_page.play_button.get_sensitive()
        live_page.sources.desktop_audio.set_active(True)
        live_page.sources.style.set_active_id("rings")
        with mock.patch.object(videos, "play", return_value={"state": "playing", "source": "audio", "path": "rings", "name": "Radial rings"}) as play:
            live_page._play()
            pump()
            play.assert_called_once_with("rings", "fill", source="audio", desktop_audio=True, microphone=True, color="#65d6ce")
            assert "Radial rings" in live_page.status.get_text()
        live_page.update_status({"state": "stopped", "path": ""})
        with mock.patch.object(saver, "catalog", return_value=[]):
            live_page.sources.source.set_active_id("xscreensaver")
            live_page.sources.refresh()
            assert not live_page.play_button.get_sensitive()
            assert "No installed" in live_page.sources.description.get_text()
        live_page.destroy()
    # Running wallpapers follow edits, while page loads and stopped selections
    # never launch renderers. Quick edits yield one playback request.
    active = {"state": "playing", "source": "video", "path": str(fixture)}
    animation["settings"] = animation_settings.schema(ET.fromstring(XML))
    with mock.patch.object(saver, "catalog", return_value=[animation]), mock.patch.object(audio, "available", return_value=True), mock.patch.object(videos, "request", return_value=active) as request, mock.patch.object(videos, "play", return_value=active) as play:
        live_page = VideoWallpapersPage(win)
        live_page.load()
        pump(.35)
        play.assert_not_called()
        live_page.fit.set_active_id("fill")
        live_page.fit.set_active_id("fit")
        live_page.fit.set_active_id("fill")
        pump(.4)
        play.assert_called_once_with(str(fixture), "fill")
        play.reset_mock()
        live_page.sources.source.set_active_id("xscreensaver")
        live_page.sources.fps.set_active_id("60")
        pump(.4)
        play.assert_called_once_with("fuzzyflakes", "fill", source="xscreensaver", fps=60)
        assert live_page.sources.settings_button.get_sensitive()
        play.reset_mock()
        # A settings dialog saves only on Apply; Cancel leaves saved values alone.
        def answer_settings(response, value=None):
            for top in Gtk.Window.list_toplevels():
                if isinstance(top, Gtk.Dialog) and top.get_title() == "Fuzzy Flakes settings":
                    def change(widget):
                        if isinstance(widget, Gtk.SpinButton) and widget.get_value() == 10:
                            widget.set_value(value)
                        elif isinstance(widget, Gtk.Container):
                            for child in widget.get_children():
                                change(child)
                    if value is not None:
                        change(top)
                    top.response(response)
            return False
        GLib.idle_add(answer_settings, Gtk.ResponseType.CANCEL, 25)
        assert not edit_animation(win, animation)
        assert animation_settings.saved("fuzzyflakes", animation["settings"]) == {}
        GLib.idle_add(answer_settings, Gtk.ResponseType.ACCEPT, 25)
        live_page.sources.settings_button.clicked()
        pump(.4)
        play.assert_called_once_with("fuzzyflakes", "fill", source="xscreensaver", fps=60, settings={"speed": 25})
        play.reset_mock()
        live_page.sources.source.set_active_id("audio")
        live_page.sources.style.set_active_id("blocks")
        live_page.sources.microphone.set_active(True)
        pump(.4)
        play.assert_called_once_with("blocks", "fill", source="audio", desktop_audio=True, microphone=True, color="#65d6ce")
        play.reset_mock()
        request.return_value = {"state": "paused", "path": "blocks", "manual_paused": True}
        live_page.update_status(request.return_value)
        live_page.sources.style.set_active_id("rings")
        pump(.4)
        assert play.call_count == 1
        request.assert_called_with("pause", paused=True)
        play.reset_mock()
        request.return_value = {"state": "stopped", "path": ""}
        live_page.sources.style.set_active_id("curve")
        pump(.4)
        play.assert_not_called()  # The tray stopped playback after the last UI status.
        live_page.update_status(request.return_value)
        live_page.sources.style.set_active_id("bars")
        pump(.4)
        play.assert_not_called()
        request.return_value = active
        live_page.update_status(active)
        live_page.sources.color_mode.set_active_id("gradient")
        assert live_page.sources.color_row.get_visible()
        assert live_page.sources.second_color_row.get_visible()
        assert live_page.sources.color.get_visible()
        assert live_page.sources.color2.get_visible()
        accent = Gdk.RGBA()
        accent.parse("#ffd166")
        live_page.sources.color2.set_rgba(accent)
        live_page.sources.color2.emit("color-set")
        live_page.sources.cycle_colors.set_active(True)
        live_page.sources.color_speed.set_active_id("fast")
        pump(.4)
        play.assert_called_once_with("bars", "fill", source="audio", desktop_audio=True, microphone=True,
                                    color="#65d6ce", color2="#ffd166", color_mode="gradient", cycle_colors=True, color_speed="fast")
        play.reset_mock()
        live_page.sources.color_mode.set_active_id("aurora")
        assert not live_page.sources.color_row.get_visible()
        assert not live_page.sources.second_color_row.get_visible()
        assert live_page.sources.color_speed.get_sensitive()
        pump(.4)
        assert play.call_args.kwargs["color_mode"] == "aurora"
        play.reset_mock()
        live_page.update_status(active)
        live_page.sources.style.set_active_id("rings")
        live_page.destroy()
        pump(.4)
        play.assert_not_called()
    qt_page = QtSettingsPage(win)
    with mock.patch.object(qt, "engines", return_value={6}):
        qt_page.load()
        labels = [widget.get_label() for row in qt_page.body.get_children() if isinstance(row, Gtk.Box) for widget in row.get_children() if isinstance(widget, Gtk.Button)]
        assert "Browse Qt themes" in labels and "Restore previous Qt appearance" in labels
        with mock.patch.object(qt, "configured", return_value=True), mock.patch.object(qt, "get", return_value="Example"), mock.patch.object(qt, "session_active", return_value=False):
            qt_page.load()
            labels = [widget.get_label() for row in qt_page.body.get_children() if isinstance(row, Gtk.Box) for widget in row.get_children() if isinstance(widget, Gtk.Button)]
            assert "Reapply Kvantum setup" in labels
            text = [widget.get_text() for widget in qt_page.body.get_children() if isinstance(widget, Gtk.Label)]
            assert "Selected Qt theme: Example" in text
            assert "Current desktop: log out and back in to activate the saved Kvantum setup." in text
            assert not any("OpenSnitch" in label for label in labels)
        with mock.patch("drape.ui.qt_settings.Gtk.MessageDialog") as dialog, mock.patch.object(qt, "disable") as disable:
            dialog.return_value.run.return_value = Gtk.ResponseType.NO
            qt_page._disable(None)
            disable.assert_not_called()
        with mock.patch.object(qt, "enable", return_value=True) as enable:
            qt_page._enable(None)
            enable.assert_called_once()
            assert "log out and back in" in win.notify.call_args.args[0]
    qt_page.destroy()
    native_page = LibadwaitaSettingsPage(win)
    with mock.patch.object(desktop, "get", return_value="Example"), mock.patch.object(desktop, "supported", return_value=True), mock.patch.object(libadwaita, "theme_dir", return_value=Path(temp)), mock.patch.object(libadwaita, "get", return_value=""), mock.patch.object(libadwaita, "configured", return_value=False):
        native_page.load()
        labels = [widget.get_label() for widget in native_page.body.get_children() if isinstance(widget, Gtk.Button)]
        assert "Browse GTK 4 themes" in labels
        assert "Restore previous native GNOME appearance" in labels
        with mock.patch("drape.ui.libadwaita_settings.Gtk.MessageDialog") as dialog, mock.patch.object(libadwaita, "apply") as apply:
            dialog.return_value.run.return_value = Gtk.ResponseType.NO
            native_page._apply(None, "Example")
            apply.assert_not_called()
            assert "gtk.css" in dialog.return_value.format_secondary_text.call_args.args[0]
    native_page.destroy()
    # Grouping preserves page IDs and selection, and filtered engines leave no
    # empty headers. Kvantum setup remains available to install a missing engine.
    win.stack = Gtk.Stack()
    for group, names in GROUPS:
        for name in names:
            win.stack.add_titled(Gtk.Box(), name, name)
    win.stack.show_all()
    win.stack.set_visible_child_name("installed")
    hidden = {"desktop", "lookandfeel", "colors", "xfcepanel", "wm", "kvantum", "libadwaita", "libadwaitasettings"}
    win.page_visible = lambda name: name not in hidden
    sidebar = build_sidebar(win)
    win.add(sidebar)
    win.show_all()
    pump()
    rows = win._sidebar_list.get_children()
    visible = [row for row in rows if row.get_child_visible()]
    headers = [row.get_header().get_text() for row in visible if row.get_header()]
    assert all(row.get_header().get_visible() for row in visible if row.get_header())
    assert "GTK APPLICATIONS" in headers and "QT / KVANTUM APPLICATIONS" in headers
    assert "DESKTOP SHELL" not in headers
    assert "GNOME / LIBADWAITA" not in headers
    assert win._sidebar_list.get_selected_row().page == "installed"
    gtk_row = next(row for row in rows if row.page == "gtk")
    assert gtk_row.get_child().get_text() == "GTK themes"
    assert "libadwaita" in gtk_row.get_tooltip_text()
    qt_setup = next(row for row in visible if row.page == "qtsettings")
    win._sidebar_list.select_row(qt_setup)
    assert win.stack.get_visible_child_name() == "qtsettings"
    hidden.clear()
    win._sidebar_list.invalidate_filter()
    pump()
    visible = [row for row in rows if row.get_child_visible()]
    assert "DESKTOP SHELL" in [row.get_header().get_text() for row in visible if row.get_header()]
    assert "GNOME / LIBADWAITA" in [row.get_header().get_text() for row in visible if row.get_header()]
    sidebar.destroy()
    win.stack.destroy()
    win.stack = mock.Mock()
    with (
        mock.patch.object(installer, "load_manifest", return_value={}),
        mock.patch.object(desktop, "supported", return_value=True),
    ):
        page = browse.BrowsePage(win, "gtk")
        win.pages["gtk"] = page
        with mock.patch.object(pling, "cached_search", return_value=None), mock.patch.object(browse, "run_async"):
            page.load()
            assert "libadwaita" in page.banner.get_text() and page.banner.get_visible()
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
            assert card.compatibility_note.get_text() == "Contents pending inspection"
            primary = unknown.files[0]
            alternate = pling.Download(2, "alternate.zip", "url", 1, "")
            unknown.files.append(alternate)
            card.refresh_evidence({2: ({"gtk", "gtk-3.0"}, True)})
            assert card.glyphs.get_visible() and "GTK" in card.glyphs.get_tooltip_text()
            assert card._checks.get(primary.index) is None
            unknown.files.pop()
            card.refresh_evidence({})
            card.set_scan_busy(True)
            assert card.scan_spinner.get_visible() and card.scan_spinner.get_property("active")
            assert card.compatibility_note.get_text() == "Inspecting download…"
            card.set_scan_busy(False)
            assert not card.scan_spinner.get_visible()
            assert card.compatibility_note.get_text() == "Contents pending inspection"
            # The entire card routes hover intent, without treating child crossings
            # as leaving the card or consuming ordinary click events.
            win.idle_inspector = mock.Mock()
            event = Gdk.Event.new(Gdk.EventType.ENTER_NOTIFY)
            event.detail = Gdk.NotifyType.NONLINEAR
            card.hover_area.emit("enter-notify-event", event)
            win.idle_inspector.hover.assert_called_once_with(card)
            event = Gdk.Event.new(Gdk.EventType.LEAVE_NOTIFY)
            event.detail = Gdk.NotifyType.INFERIOR
            card.hover_area.emit("leave-notify-event", event)
            win.idle_inspector.unhover.assert_not_called()
            event.detail = Gdk.NotifyType.NONLINEAR
            card.hover_area.emit("leave-notify-event", event)
            win.idle_inspector.unhover.assert_called_once_with(card)
            del win.idle_inspector
        # Cached previews must not destroy overlay children inside the draw callback.
        from drape.ui import images
        from drape.ui.gtk import GdkPixbuf
        preview = "https://test.invalid/cached.png"
        pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 8, 8)
        pixbuf.fill(0x224466ff)
        key = (pling.thumb_url(preview), 260, 160)
        draw_active, delivery_during_draw = [False], []
        original_load = images.load_image

        def first_draw_load(*args, on_done=None, **kwargs):
            def delivered(ok):
                delivery_during_draw.append(draw_active[0])
                if on_done:
                    on_done(ok)
            draw_active[0] = True
            try:
                original_load(*args, on_done=delivered, **kwargs)
            finally:
                draw_active[0] = False

        with mock.patch.dict(images._pixbufs, {key: pixbuf}), mock.patch.object(browse, "load_image", side_effect=first_draw_load):
            for index in range(60):
                item = pling.Item(f"cached-{index}", "Cached preview", "", "", "", "", 0, 0, "", previews=[preview])
                card = browse.Card(win, "gtk", item, checks={})
                page.flow.add(card)
                card.show_all()
            pump(0.1)
            adj = page.scroller.get_vadjustment()
            for index in range(35):
                adj.set_value(min(adj.get_upper() - adj.get_page_size(), index * 250))
                pump(0.03)
            assert delivery_during_draw and not any(delivery_during_draw)
        print(f"Cached-preview scrolling check: {len(delivery_during_draw)} previews delivered outside draw callbacks.")
        win.remove(page)
        page.destroy()
    from tests.configurations_ui_checks import check as check_configurations
    check_configurations(win, temp, pump)
    from tests.update_ui_checks import check as check_update_notice
    check_update_notice()
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
    # The live viewport signal cancels the isolated worker before the next timer tick.
    from drape.ui.idle_scan import ViewportInspector
    from drape import compatibility
    import io
    now = [0]
    inspector = ViewportInspector(win)
    status_bar = inspector.create_status_bar()
    status_bar.update(dict(text="Checking saved themes — 1 of 3 cached", spinning=True, fraction=1/3))
    assert status_bar.spinner.get_visible() and status_bar.spinner.get_property("active")
    assert status_bar.progress.get_visible() and abs(status_bar.progress.get_fraction() - 1/3) < 0.001
    status_bar.update(dict(text="Server rate limit — next check in 60s", spinning=False, fraction=None))
    assert not status_bar.spinner.get_visible() and not status_bar.progress.get_visible()
    assert "60s" in status_bar.label.get_text()
    status_bar.destroy()
    inspector.local_manifest = None
    inspector.clock = lambda: now[0]
    inspector.index = compatibility.Index(Path(temp) / "viewport.sqlite3")
    process = mock.Mock(stdout=io.BytesIO(b'{"status":"checked"}'))
    process.poll.return_value = None
    inspector.spawn = mock.Mock(return_value=process)
    for index, card in enumerate(cards):
        card.item = pling.Item(str(index), "Theme", "", "", "", "today", 0, 0, "",
                              files=[pling.Download(1, "theme.zip", "https://test/theme", 1, "abc")])
        card._scan_alive, card._checks = True, {}
    inspector.register_view(flow, scroller)
    cards[0].set_scan_busy = mock.Mock()
    inspector._card_busy(cards[0], True)
    inspector._card_busy(cards[0], False)
    cards[0].set_scan_busy.assert_not_called()
    pump(0.02)
    cards[0].set_scan_busy.assert_called_once_with(False)
    cards[0].set_scan_busy.reset_mock()
    with mock.patch.object(Gtk.Window, "is_active", return_value=False):
        now[0] = 2
        inspector.tick()
        inspector.spawn.assert_called_once()
        assert inspector.active[1] is cards[0]
        adjustment.set_value(1000)
        process.terminate.assert_called_once()
        assert inspector.active is None
    inspector.set_enabled(False)
    process.poll.return_value = -15
    inspector.tick()
    adjustment.set_value(0)
    pump(0.1)
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
        assert card.type_label.get_text() == "Icons · Application and folder icons"
        assert "compatibility is shown separately" in card.type_label.get_tooltip_text()
        qt_upload = pling.Item("3", "Qt theme", "author", "", "", "", 0, 0, "", category="123")
        unknown_upload = pling.Item("4", "Other", "author", "", "", "", 0, 0, "", category="99999")
        dialog.view.got_uploads(([qt_upload, unknown_upload], 3))
        type_labels = [card.type_label.get_text() for card in dialog.view.flow.cards()]
        assert "Qt applications (Kvantum) · Qt app widgets" in type_labels
        assert "Other upload · Theme type not provided" in type_labels
        dialog.view.all_button.clicked()
        assert dialog.view.busy and dialog.view.page == 2
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

    # Real worker-to-GTK confirmation keeps the extracted archive and remembers consent.
    import threading
    import zipfile
    from drape.ui import cinnamon_warnings
    from drape.ui.widgets import ApplyControl
    warning_root = Path(temp) / "cinnamon-warning"
    warning_root.mkdir()
    archive = warning_root / "legacy.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("Legacy/cinnamon/cinnamon.css", ".modal-dialog {}")
    warning_window = Gtk.Window()
    warning_window._closing = threading.Event()
    warning_window.notify = mock.Mock()
    warning_window.refresh_item = mock.Mock()
    main_thread = threading.get_ident()
    with (
        mock.patch.object(installer, "MANIFEST", warning_root / "installed.json"),
        mock.patch.object(installer, "THEMES_DIR", warning_root / "themes"),
        mock.patch.object(desktop, "supported", return_value=True),
        mock.patch.object(desktop, "current_desktop", return_value="cinnamon"),
        mock.patch.object(desktop, "cinnamon_version", return_value=(6, 4)),
        mock.patch.object(desktop, "running_wm", return_value="Muffin"),
    ):
        for response in (Gtk.ResponseType.CANCEL, Gtk.ResponseType.ACCEPT):
            results = []
            def inspect_dialog(dialog):
                assert threading.get_ident() == main_thread
                assert "Cinnamon 6.4" in dialog.get_property("secondary-text")
                assert "Install anyway" in [b.get_label() for b in dialog.get_action_area().get_children()]
                return response
            def install_with_confirmation():
                try:
                    results.append(installer.install_file(
                        archive, "legacy", "Legacy", only_applicable=True,
                        confirm_cinnamon=lambda warnings: cinnamon_warnings.confirm_install(warning_window, warnings),
                    ))
                except installer.InstallCancelled as error:
                    results.append(error)
            with mock.patch.object(Gtk.MessageDialog, "run", side_effect=inspect_dialog, autospec=True):
                thread = threading.Thread(target=install_with_confirmation)
                thread.start()
                deadline = time.monotonic() + 5
                while thread.is_alive() and time.monotonic() < deadline:
                    pump(0.02)
                thread.join(0.1)
                assert not thread.is_alive(), "Cinnamon confirmation worker stalled"
            assert results
            if response == Gtk.ResponseType.CANCEL:
                assert isinstance(results[0], installer.InstallCancelled)
                assert not installer.MANIFEST.exists() and not installer.THEMES_DIR.exists()
            else:
                component = installer.load_manifest()["legacy"]["components"][0]
                assert component["allow_incomplete_cinnamon"]
                control = ApplyControl(warning_window, "legacy", "desktop")
                assert control.get_children()[0].get_sensitive()
                with mock.patch.object(cinnamon_warnings, "confirm") as repeat, mock.patch.object(desktop, "set_", return_value=True) as apply:
                    theme_actions.ThemeActions.apply(warning_window, component, "desktop")
                    repeat.assert_not_called()
                    apply.assert_called_once_with("desktop", "Legacy")
                control.destroy()
    warning_window.destroy()
    print("Cinnamon warning passed: main-loop Install anyway/Cancel, persisted consent and enabled Apply.")

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
