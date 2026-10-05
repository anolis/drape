"""Local video selection and playback controls for Cinnamon."""

import shutil
from pathlib import Path

from .. import video_wallpapers as videos
from .common import error_dialog, run_async
from .gtk import Gtk


class VideoWallpapersPage(Gtk.ScrolledWindow):
    def __init__(self, window):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.win, self.alive, self.busy = window, True, False
        self.connect("destroy", lambda *_: setattr(self, "alive", False))
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin=24)
        self.add(self.body)
        title = Gtk.Label(label="Video wallpapers", xalign=0)
        title.get_style_context().add_class("title")
        self.body.pack_start(title, False, False, 0)
        self.body.pack_start(
            Gtk.Label(
                label="Play a local video on your Cinnamon desktop. Videos loop silently on all monitors. "
                "Drape stays in the system tray during playback; closing this window hides it. "
                "Use the tray to reopen Drape, pause, stop, or quit.",
                xalign=0,
                wrap=True,
            ),
            False,
            False,
            0,
        )
        self.controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.body.pack_start(self.controls, False, False, 0)
        self.selector = Gtk.ComboBoxText()
        self.selector.connect("changed", lambda *_: self._selected())
        self.controls.pack_start(self.selector, False, False, 0)
        self.location = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.controls.pack_start(self.location, False, False, 0)
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
        self.controls.pack_start(files, False, False, 0)
        self.fit = Gtk.ComboBoxText()
        self.fit.append("fill", "Fill screen (crop edges)")
        self.fit.append("fit", "Fit video (keep entire picture)")
        self.fit.set_active_id("fill")
        self.controls.pack_start(self.fit, False, False, 0)
        actions = Gtk.Box(spacing=8)
        self.play_button = Gtk.Button(label="Play wallpaper")
        self.play_button.get_style_context().add_class("suggested-action")
        self.play_button.connect("clicked", self._play)
        self.pause_button = Gtk.Button(label="Pause")
        self.pause_button.connect("clicked", self._pause)
        self.stop_button = Gtk.Button(label="Stop & restore static wallpaper")
        self.stop_button.connect("clicked", lambda *_: self._run(videos.stop))
        for button in (self.play_button, self.pause_button, self.stop_button):
            actions.pack_start(button, False, False, 0)
        self.controls.pack_start(actions, False, False, 0)
        self.status = Gtk.Label(xalign=0, wrap=True)
        self.body.pack_start(self.status, False, False, 0)
        self.body.pack_start(
            Gtk.Label(
                label="Your static wallpaper stays unchanged underneath. Selecting a still wallpaper "
                "in Drape stops video playback. Videos are remembered by their file location; "
                "moving or deleting a file makes it unavailable. Playback pauses while the screen is locked. "
                "This first version supports Cinnamon on X11 and requires mpv. Start playback manually after login.",
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
        try:
            data = videos.library()
        except videos.VideoError as exc:
            self.status.set_text(str(exc))
            self.controls.set_sensitive(False)
            self.show_all()
            return
        self.selector.remove_all()
        for path in data["videos"]:
            suffix = " (file missing)" if not Path(path).is_file() else ""
            self.selector.append(path, Path(path).name + suffix)
        self.selector.set_active_id(data["selected"])
        self.fit.set_active_id(data["fit"])
        self._selected()
        self.show_all()
        self._run(lambda: videos.request("status"))

    def _selected(self):
        path = self.selector.get_active_id()
        self.location.set_text(path or "Add a video to begin.")
        self.play_button.set_sensitive(
            bool(path) and Path(path).is_file() and bool(shutil.which("mpv"))
        )
        self.remove_button.set_sensitive(bool(path))

    def update_status(self, state):
        self.state = state
        active = state["state"] in {"starting", "playing", "paused"}
        self.pause_button.set_sensitive(active and state["state"] != "starting")
        self.pause_button.set_label("Resume" if state["state"] == "paused" else "Pause")
        self.stop_button.set_sensitive(active or state["state"] == "error")
        text = {
            "stopped": "Static wallpaper is showing.",
            "starting": "Starting video playback…",
            "playing": "Playing: " + Path(state.get("path", "")).name,
            "paused": "Video wallpaper is paused (by you or the screen lock).",
            "error": state.get("message") or "Video playback failed.",
        }.get(state["state"], "")
        if not shutil.which("mpv"):
            text = "Install mpv with your system's package manager to enable video playback."
        self.status.set_text(text)

    def _run(self, work, enabled=False, reload=False):
        if self.busy:
            return
        self.busy = True
        self.controls.set_sensitive(False)
        self.status.set_text("Updating video wallpaper…")

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
                error_dialog(self.win, "Could not update video wallpaper", error)

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
            if videos.request("status").get("path") == path:
                videos.stop()
            videos.forget(path)
            return videos.request("status")

        self._run(work, reload=True)

    def _play(self, *_):
        path, fit = self.selector.get_active_id(), self.fit.get_active_id()
        if path:
            self._run(lambda: videos.play(path, fit), enabled=True)

    def _pause(self, *_):
        self._run(lambda: videos.request("pause", paused=self.state["state"] != "paused"))
