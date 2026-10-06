"""Source-specific controls for the shared live-wallpaper player."""

from .. import wallpaper_audio as audio
from .. import wallpaper_sources as sources
from .. import wallpaper_xscreensaver as saver
from ..wallpaper_colors import Palette
from .gtk import Gdk, Gtk


class SourceControls(Gtk.Box):
    def __init__(self, page, video_controls):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.page = page
        self.updating = False
        self.source = Gtk.ComboBoxText()
        for key, label in (
            ("video", "Local video"),
            ("xscreensaver", "XScreenSaver animation"),
            ("audio", "Audio visualization"),
        ):
            self.source.append(key, label)
        self.pack_start(Gtk.Label(label="Playback source", xalign=0), False, False, 0)
        self.pack_start(self.source, False, False, 0)
        self.stack = Gtk.Stack()
        self.stack.add_named(video_controls, "video")
        self.pack_start(self.stack, False, False, 0)

        animations = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.animation = Gtk.ComboBoxText()
        self.animation.connect("changed", self._animation_changed)
        animations.pack_start(self.animation, False, False, 0)
        self.description = Gtk.Label(xalign=0, wrap=True)
        animations.pack_start(self.description, False, False, 0)
        self.settings_button = Gtk.Button(label="Animation settings…")
        self.settings_button.connect("clicked", self._settings)
        animations.pack_start(self.settings_button, False, False, 0)
        self.fps = Gtk.ComboBoxText()
        self.fps.connect("changed", lambda *_: page._selected())
        for fps in (15, 30, 60):
            self.fps.append(str(fps), f"{fps} FPS target (where supported)")
        animations.pack_start(self.fps, False, False, 0)
        refresh = Gtk.Button(label="Refresh installed animations")
        refresh.connect("clicked", lambda *_: self.refresh())
        animations.pack_start(refresh, False, False, 0)
        self.install_animations = Gtk.Button(label="Install animation packages…", no_show_all=True)
        self.install_animations.connect(
            "clicked", lambda *_: self._install(["xscreensaver", "xscreensaver_gl"])
        )
        animations.pack_start(self.install_animations, False, False, 0)
        self.stack.add_named(animations, "xscreensaver")

        visualizer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.style = Gtk.ComboBoxText()
        for key, label in sources.STYLES.items():
            self.style.append(key, label)
        visualizer.pack_start(self.style, False, False, 0)
        inputs = Gtk.Box(spacing=16)
        self.desktop_audio = Gtk.CheckButton(label="Desktop audio")
        self.microphone = Gtk.CheckButton(label="Microphone")
        self.microphone.set_tooltip_text(
            "React to your system's default microphone while playback is running."
        )
        for check in (self.desktop_audio, self.microphone):
            inputs.pack_start(check, False, False, 0)
            check.connect("toggled", lambda *_: page._selected())
        visualizer.pack_start(inputs, False, False, 0)
        self.color_mode = Gtk.ComboBoxText()
        for key, label in sources.COLOR_MODES.items():
            self.color_mode.append(key, label)
        self.color_mode.connect("changed", self._colors_changed)
        visualizer.pack_start(Gtk.Label(label="Color palette", xalign=0), False, False, 0)
        visualizer.pack_start(self.color_mode, False, False, 0)
        self.color = Gtk.ColorButton(title="Choose visualization color", use_alpha=False)
        self.color2 = Gtk.ColorButton(title="Choose second visualization color", use_alpha=False)
        self.color_row = Gtk.Box(spacing=12, no_show_all=True)
        self.color_row.pack_start(Gtk.Label(label="Primary color", xalign=0), False, False, 0)
        self.color_row.pack_start(self.color, False, False, 0)
        self.second_color_row = Gtk.Box(spacing=12, no_show_all=True)
        self.second_color_row.pack_start(Gtk.Label(label="Second color", xalign=0), False, False, 0)
        self.second_color_row.pack_start(self.color2, False, False, 0)
        for button in (self.color, self.color2):
            button.connect("color-set", self._colors_changed)
        # show_all on the page must preserve mode-dependent visibility.
        for row in (self.color_row, self.second_color_row):
            for child in row.get_children():
                child.show_all()
        visualizer.pack_start(self.color_row, False, False, 0)
        visualizer.pack_start(self.second_color_row, False, False, 0)
        self.swatch = Gtk.DrawingArea(height_request=24)
        self.swatch.connect("draw", self._draw_palette)
        visualizer.pack_start(self.swatch, False, False, 0)
        color_motion = Gtk.Box(spacing=12)
        self.cycle_colors = Gtk.CheckButton(label="Cycle colors")
        self.cycle_colors.set_tooltip_text(
            "Shift colors smoothly while audio is playing. Pause freezes color changes."
        )
        self.cycle_colors.connect("toggled", self._colors_changed)
        color_motion.pack_start(self.cycle_colors, False, False, 0)
        self.color_speed = Gtk.ComboBoxText()
        for key, label in sources.COLOR_SPEEDS.items():
            self.color_speed.append(key, label)
        self.color_speed.connect("changed", lambda *_: page._selected())
        color_motion.pack_start(self.color_speed, False, False, 0)
        visualizer.pack_start(color_motion, False, False, 0)
        self.audio_note = Gtk.Label(xalign=0, wrap=True)
        visualizer.pack_start(self.audio_note, False, False, 0)
        self.install_audio = Gtk.Button(label="Install audio support…", no_show_all=True)
        self.install_audio.connect("clicked", lambda *_: self._install(["cava", "pactl"]))
        visualizer.pack_start(self.install_audio, False, False, 0)
        visualizer.pack_start(
            Gtk.Label(
                label="Choose either input or both. Desktop audio follows your default output; "
                "Microphone follows your default input. Audio is analyzed locally and never saved. "
                "Changes apply automatically while a live wallpaper is running.",
                xalign=0,
                wrap=True,
            ),
            False,
            False,
            0,
        )
        self.stack.add_named(visualizer, "audio")
        self.style.connect("changed", lambda *_: page._selected())
        self.source.connect("changed", self._changed)
        self.entries = {}

    def load(self, preferences):
        self.refresh(preferences["animation"])
        self.fps.set_active_id(str(preferences["fps"]))
        self.style.set_active_id(preferences["style"])
        self.desktop_audio.set_active(preferences["desktop_audio"])
        self.microphone.set_active(preferences["microphone"])
        color = Gdk.RGBA()
        color.parse(preferences["color"])
        self.color.set_rgba(color)
        color.parse(preferences["color2"])
        self.color2.set_rgba(color)
        self.color_speed.set_active_id(preferences["color_speed"])
        self.cycle_colors.set_active(preferences["cycle_colors"])
        self.color_mode.set_active_id(preferences["color_mode"])
        self.refresh_audio()
        self.source.set_active_id(preferences["source"])
        self._changed()

    @staticmethod
    def _hex_color(button):
        color = button.get_rgba()
        return "#" + "".join(f"{round(v * 255):02x}" for v in (color.red, color.green, color.blue))

    def _colors_changed(self, *_):
        mode = self.color_mode.get_active_id() or "single"
        self.color_row.set_visible(mode in {"single", "gradient"})
        self.second_color_row.set_visible(mode == "gradient")
        self.color_speed.set_sensitive(self.cycle_colors.get_active())
        self.swatch.queue_draw()
        self.page._selected()

    def _draw_palette(self, widget, cr):
        palette = Palette(
            self._hex_color(self.color),
            self._hex_color(self.color2),
            self.color_mode.get_active_id() or "single",
        )
        width, height = widget.get_allocated_width(), widget.get_allocated_height()
        for i in range(64):
            cr.set_source_rgb(*palette.at(i / 63))
            cr.rectangle(i * width / 64, 0, width / 64 + 1, height)
            cr.fill()

    def refresh_audio(self):
        self.audio_note.set_text(
            "Uses CAVA with PulseAudio or PipeWire's PulseAudio service."
            if audio.available()
            else "Audio visualization needs CAVA and PulseAudio-compatible audio controls. "
            "Install audio support below, then click Play wallpaper."
        )
        self.install_audio.set_visible(not audio.available())

    def refresh(self, selected=None):
        self.updating = True
        selected = selected or self.animation.get_active_id()
        self.entries = {entry["name"]: entry for entry in saver.catalog()}
        self.animation.remove_all()
        for name, entry in self.entries.items():
            self.animation.append(name, entry["label"])
        if selected not in self.entries:
            selected = next(iter(self.entries), None)
        self.animation.set_active_id(selected)
        self.install_animations.set_visible(True)
        self._animation_changed()
        self.updating = False

    def _animation_changed(self, *_):
        entry = self.entries.get(self.animation.get_active_id())
        self.description.set_text(
            entry["description"]
            if entry
            else "No installed XScreenSaver animations found. Install xscreensaver on Arch/CachyOS, "
            "or xscreensaver-data / xscreensaver-gl on Debian/Ubuntu, then refresh this list."
        )
        self.fps.set_sensitive(bool(entry and entry["delay"]))
        self.settings_button.set_sensitive(bool(entry and entry.get("settings")))
        self.page._selected()

    def _settings(self, *_):
        from . import animation_settings

        entry = self.entries.get(self.animation.get_active_id())
        if entry and animation_settings.edit(self.page.win, entry):
            self.page._selected()

    def _changed(self, *_):
        self.stack.set_visible_child_name(self.source.get_active_id() or "video")
        self.page._selected()

    def playable(self):
        source = self.source.get_active_id() or "video"
        if source == "xscreensaver":
            return bool(self.animation.get_active_id())
        if source == "audio":
            return audio.available() and (
                self.desktop_audio.get_active() or self.microphone.get_active()
            )
        return None  # The page checks whether the selected video exists.

    def _install(self, keys):
        from . import optional_packages

        if self.page.busy:
            return
        self.page.busy = True
        self.page.controls.set_sensitive(False)

        def finished():
            if self.page.alive:
                self.page.busy = False
                self.page.controls.set_sensitive(True)
                self.refresh()
                self.refresh_audio()
                self.page._selected()

        optional_packages.install(self.page.win, keys, finished)

    def selection(self):
        source = self.source.get_active_id() or "video"
        if source == "xscreensaver":
            from .. import wallpaper_animation_settings as settings

            entry = self.entries.get(self.animation.get_active_id())
            values = settings.saved(entry["name"], entry.get("settings", [])) if entry else {}
            return (
                self.animation.get_active_id(),
                "fill",
                source,
                {
                    "fps": int(self.fps.get_active_id() or 30),
                    **({"settings": values} if values else {}),
                },
            )
        if source == "audio":
            color_options = {
                "color2": self._hex_color(self.color2),
                "color_mode": self.color_mode.get_active_id() or "single",
                "cycle_colors": self.cycle_colors.get_active(),
                "color_speed": self.color_speed.get_active_id() or "normal",
            }
            return (
                self.style.get_active_id(),
                "fill",
                source,
                {
                    "desktop_audio": self.desktop_audio.get_active(),
                    "microphone": self.microphone.get_active(),
                    "color": self._hex_color(self.color),
                    **{
                        key: value
                        for key, value in color_options.items()
                        if value != sources.DEFAULTS[key]
                    },
                },
            )
        return self.page.selector.get_active_id(), self.page.fit.get_active_id(), source, {}
