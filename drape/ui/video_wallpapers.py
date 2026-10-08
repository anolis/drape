"""Local video selection and controls for supported live-wallpaper desktops."""

import shutil
from pathlib import Path

from .. import video_wallpapers as videos
from .. import wallpaper_desktop
from .. import wallpaper_sources as sources
from .common import error_dialog, run_async
from .gtk import GLib, Gtk
from .wallpaper_source_controls import SourceControls


class VideoWallpapersPage(Gtk.ScrolledWindow):
    def __init__(self, window):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.win, self.alive, self.busy = window, True, False
        self.loading, self.change_timer = False, None
        self.connect("destroy", self._destroyed)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin=24)
        self.add(self.body)
        title = Gtk.Label(label="Live wallpapers", xalign=0)
        title.get_style_context().add_class("title")
        self.body.pack_start(title, False, False, 0)
        self.body.pack_start(
            Gtk.Label(
                label="Play a local video, an XScreenSaver animation, or an audio visualization on your desktop. "
                "Playback appears beneath your desktop icons on all monitors. Videos loop silently. "
                "Closing this window keeps playback running. Reopen Drape for playback controls, "
                "or use the system tray where available. GNOME can run without a tray extension.",
                xalign=0,
                wrap=True,
            ),
            False,
            False,
            0,
        )
        self.controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.body.pack_start(self.controls, False, False, 0)
        video_controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.selector = Gtk.ComboBoxText()
        self.selector.connect("changed", lambda *_: self._selected())
        video_controls.pack_start(self.selector, False, False, 0)
        self.location = Gtk.Label(xalign=0, wrap=True, selectable=True)
        video_controls.pack_start(self.location, False, False, 0)
        files = Gtk.Box(spacing=8)
        self.add_button = Gtk.Button(label="Add video…")
        self.add_button.connect("clicked", self._add)
        files.pack_start(self.add_button, False, False, 0)
        self.remove_button = Gtk.Button(label="Remove from list")
        self.remove_button.set_tooltip_text(
            "Keeps the video file on disk. Stops it if it is playing."
        )
        self.remove_button.connect("clicked", self._remove)
        files.pack_start(self.remove_button, False, False, 0)
        video_controls.pack_start(files, False, False, 0)
        self.fit = Gtk.ComboBoxText()
        self.fit.append("fill", "Fill screen (crop edges)")
        self.fit.append("fit", "Fit video (keep entire picture)")
        self.fit.set_active_id("fill")
        self.fit.connect("changed", lambda *_: self._selected())
        video_controls.pack_start(self.fit, False, False, 0)
        self.sources = SourceControls(self, video_controls)
        self.controls.pack_start(self.sources, False, False, 0)
        actions = Gtk.Box(spacing=8)
        self.play_button = Gtk.Button(label="Play wallpaper")
        self.play_button.get_style_context().add_class("suggested-action")
        self.play_button.connect("clicked", self._play)
        self.pause_button = Gtk.Button(label="Pause")
        self.pause_button.connect("clicked", self._pause)
        self.stop_button = Gtk.Button(label="Stop & restore static wallpaper")
        self.stop_button.connect("clicked", lambda *_: self._run(videos.stop))
        self.quit_button = Gtk.Button(label="Quit Drape")
        self.quit_button.set_tooltip_text(
            "Stop live playback and quit Drape, including background playback."
        )
        self.quit_button.connect("clicked", lambda *_: self.win.get_application().quit())
        for button in (self.play_button, self.pause_button, self.stop_button):
            actions.pack_start(button, False, False, 0)
        actions.pack_end(self.quit_button, False, False, 0)
        self.controls.pack_start(actions, False, False, 0)
        host = wallpaper_desktop.current()
        self.xfce = isinstance(host, wallpaper_desktop.XfceX11)
        self.restore_desktop = Gtk.Button(label="Restore standard Xfce desktop")
        self.restore_desktop.set_sensitive(False)
        self.restore_desktop.set_no_show_all(True)
        self.restore_desktop.set_visible(self.xfce)
        self.restore_desktop.set_tooltip_text(
            "Stop playback and restart Xfdesktop without Drape’s adapter. Keeps icons, wallpaper settings and panel layout."
        )
        self.restore_desktop.connect("clicked", lambda *_: self._run(videos.restore_desktop))
        self.controls.pack_start(self.restore_desktop, False, False, 0)
        self.status = Gtk.Label(xalign=0, wrap=True)
        self.body.pack_start(self.status, False, False, 0)
        self.body.pack_start(
            Gtk.Label(
                label="Your static wallpaper stays unchanged underneath. Selecting a still wallpaper "
                "in Drape stops live playback. Videos are remembered by their file location; "
                "moving or deleting a file makes it unavailable. Playback pauses while the screen is locked. "
                "Supports Cinnamon, GNOME, MATE and Xfce on X11. Local video requires mpv; other sources use their own optional packages. "
                "Start playback manually after login.",
                xalign=0,
                wrap=True,
            ),
            False,
            False,
            0,
        )
        self.state = {"state": "stopped", "path": ""}

    def load(self):
        if self.busy:
            return
        if self.change_timer is not None:
            GLib.source_remove(self.change_timer)
            self.change_timer = None
        try:
            data = videos.library()
            preferences = sources.preferences()
        except videos.VideoError as exc:
            self.status.set_text(str(exc))
            self.controls.set_sensitive(False)
            self.show_all()
            return
        # Rebuilding widgets must never restart playback or start audio capture.
        self.loading = True
        try:
            self.selector.remove_all()
            for path in data["videos"]:
                suffix = " (file missing)" if not Path(path).is_file() else ""
                self.selector.append(path, Path(path).name + suffix)
            self.selector.set_active_id(data["selected"])
            self.fit.set_active_id(data["fit"])
            self.sources.load(preferences)
        finally:
            self.loading = False
        self._selected()
        self.show_all()
        self._run(lambda: videos.request("status"))

    def _selected(self):
        if not hasattr(self, "sources") or not hasattr(self, "play_button"):
            return
        path = self.selector.get_active_id()
        self.location.set_text(path or "Add a video to begin.")
        playable = self.sources.playable()
        self.play_button.set_sensitive(
            playable
            if playable is not None
            else bool(path) and Path(path).is_file() and bool(shutil.which("mpv"))
        )
        self.remove_button.set_sensitive(bool(path))
        if (
            not self.loading
            and not self.sources.updating
            and not self.busy
            and self.state["state"] in {"starting", "playing", "paused"}
        ):
            # Coalesce a quick series of control edits into one renderer change.
            if self.change_timer is not None:
                GLib.source_remove(self.change_timer)
            self.change_timer = GLib.timeout_add(200, self._apply_change)

    def _destroyed(self, *_):
        self.alive = False
        if self.change_timer is not None:
            GLib.source_remove(self.change_timer)
            self.change_timer = None

    def _apply_change(self):
        self.change_timer = None
        if self.alive and not self.busy and self.play_button.get_sensitive():
            self._play(automatic=True)
        return False

    def update_status(self, state):
        self.state = state
        restorable = bool(state.get("xfce_adapter"))
        self.restore_desktop.set_sensitive(restorable)
        active = state["state"] in {"starting", "playing", "paused"}
        self.pause_button.set_sensitive(active and state["state"] != "starting")
        self.pause_button.set_label("Resume" if state["state"] == "paused" else "Pause")
        self.stop_button.set_sensitive(active or state["state"] == "error")
        text = {
            "stopped": "Static wallpaper is showing.",
            "starting": "Starting wallpaper playback…",
            "playing": "Playing: " + (state.get("name") or Path(state.get("path", "")).name),
            "paused": "Wallpaper is paused (by you or the screen lock).",
            "error": state.get("message") or "Wallpaper playback failed.",
        }.get(state["state"], "")
        if active and state.get("message"):
            text += "\n" + state["message"]
        if active and state.get("audio_inputs"):
            text += "\nAudio inputs: " + " + ".join(state["audio_inputs"])
        if (
            not active
            and (self.sources.source.get_active_id() or "video") == "video"
            and not shutil.which("mpv")
        ):
            text += "\nInstall mpv with your system's package manager to enable video playback."
        self.status.set_text(text)

    def _run(self, work, enabled=False, reload=False):
        if self.busy:
            return
        self.busy = True
        self.controls.set_sensitive(False)
        self.status.set_text("Updating live wallpaper…")

        def done(state):
            if not self.alive:
                return
            self.busy = False
            self.controls.set_sensitive(True)
            self.update_status(state)
            if enabled or state["state"] in {"starting", "playing", "paused"}:
                self.win.get_application().wallpaper_tray.enable(state)
            if reload:
                self.load()

        def failed(error):
            if self.alive:
                self.busy = False
                self.controls.set_sensitive(True)
                self.status.set_text(str(error))
                error_dialog(self.win, "Could not update live wallpaper", error)

        run_async(work, done, failed)

    def _add(self, *_):
        dialog = Gtk.FileChooserDialog(
            title="Choose a video wallpaper",
            transient_for=self.win,
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Add video", Gtk.ResponseType.ACCEPT)
        file_filter = Gtk.FileFilter()
        file_filter.set_name("Video files")
        file_filter.add_mime_type("video/*")
        for extension in videos.EXTENSIONS:
            file_filter.add_pattern("*" + extension)
            file_filter.add_pattern("*" + extension.upper())
        dialog.add_filter(file_filter)
        accepted = dialog.run() == Gtk.ResponseType.ACCEPT
        path = dialog.get_filename() if accepted else None
        dialog.destroy()
        if path:
            try:
                videos.remember(path, self.fit.get_active_id())
                self.load()
            except videos.VideoError as exc:
                error_dialog(self.win, "Could not add video", exc)

    def _remove(self, *_):
        path = self.selector.get_active_id()
        if not path:
            return

        def work():
            state = videos.request("status")
            if state.get("source", "video") == "video" and state.get("path") == path:
                videos.stop()
            videos.forget(path)
            return videos.request("status")

        self._run(work, reload=True)

    def _play(self, *_, automatic=False):
        if self.busy:
            return
        try:
            path, fit, source, options = self.sources.selection()
        except videos.VideoError as exc:
            error_dialog(self.win, "Could not update live wallpaper", exc)
            return
        if path:
            extra = {}
            if self.xfce and not self.state.get("xfce_adapter") and not automatic:
                dialog = Gtk.MessageDialog(
                    transient_for=self.win,
                    modal=True,
                    message_type=Gtk.MessageType.QUESTION,
                    buttons=Gtk.ButtonsType.NONE,
                    text="Enable live wallpapers on Xfce?",
                )
                dialog.format_secondary_text(
                    "Drape will restart Xfdesktop with a temporary background adapter. Your desktop icons "
                    "briefly reload, then stay visible and clickable over playback. The panel and Thunar windows are separate.\n\n"
                    "The adapter is loaded only into this Xfdesktop process; menu entries, startup commands "
                    "and wallpaper settings are unchanged. Videos use software playback at 30 FPS.\n\n"
                    "Stop restores your static wallpaper. Restore standard Xfce desktop removes the adapter; "
                    "Quit Drape or loss of its player connection also restores the standard desktop."
                )
                dialog.add_buttons(
                    "Cancel", Gtk.ResponseType.CANCEL, "Enable & play", Gtk.ResponseType.OK
                )
                response = dialog.run()
                dialog.destroy()
                if response != Gtk.ResponseType.OK:
                    return
                extra["allow_desktop_restart"] = True

            def work():
                state = videos.request("status") if automatic else {}
                # The tray may have stopped playback since the last UI update.
                if automatic and state["state"] not in {"starting", "playing", "paused"}:
                    return state
                result = (
                    videos.play(path, fit, **extra)
                    if source == "video"
                    else videos.play(path, fit, source=source, **options, **extra)
                )
                if automatic and state.get("manual_paused", state.get("state") == "paused"):
                    result = videos.request("pause", paused=True)
                return result

            self._run(work, enabled=True)

    def _pause(self, *_):
        self._run(lambda: videos.request("pause", paused=self.state["state"] != "paused"))
