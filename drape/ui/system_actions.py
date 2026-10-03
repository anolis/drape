"""Main-window actions for privileged and login-screen changes."""

from pathlib import Path

from .gtk import GLib, Gtk, Pango
from .. import system
from .common import error_dialog, login_commands, run_async


class SystemActions:
    """Dialogs and actions for privileged, login and lock-screen changes."""

    def run_root(self, commands, title, on_success=None):
        """Run helper commands with one password prompt, showing progress."""
        d = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.OTHER,
            buttons=Gtk.ButtonsType.NONE,
            text=title,
        )
        d.format_secondary_text("You'll be asked for your password.")
        spinner = Gtk.Spinner(active=True, margin=8)
        d.get_message_area().pack_start(spinner, False, False, 0)
        # long jobs (installing software) report real progress; the spinner gives way to a bar
        bar = Gtk.ProgressBar(show_text=True, margin_top=8, no_show_all=True)
        bar.set_size_request(360, -1)
        step = Gtk.Label(
            xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=48, no_show_all=True
        )
        step.get_style_context().add_class("dim-label")
        d.get_message_area().pack_start(bar, False, False, 0)
        d.get_message_area().pack_start(step, False, False, 0)
        d.show_all()

        # the bar glides toward each new value instead of jumping
        anim = {"shown": 0.0, "target": 0.0, "tick": None}

        def glide():
            gap = anim["target"] - anim["shown"]
            anim["shown"] += gap * 0.15 if abs(gap) > 0.002 else gap
            bar.set_fraction(anim["shown"])
            bar.set_text(f"{anim['shown'] * 100:.0f}%")
            if anim["shown"] == anim["target"]:
                anim["tick"] = None
                return False
            return True

        def progress(percent, text):
            def update():
                if spinner.get_visible():
                    spinner.hide()
                    d.format_secondary_text("")
                    bar.show()
                    step.show()
                anim["target"] = max(
                    anim["target"], min(percent / 100, 1.0)
                )  # never slides backwards
                if anim["tick"] is None:
                    anim["tick"] = GLib.timeout_add(16, glide)
                step.set_text(text)

            GLib.idle_add(update)

        def done(result):
            if anim["tick"] is not None:
                GLib.source_remove(anim["tick"])
            d.destroy()
            if result.ok:
                if on_success:
                    on_success()
            elif not result.cancelled:
                lines = [
                    l for l in result.output.splitlines() if l.startswith("Error:")
                ] or result.output.splitlines()[-6:]
                error_dialog(self, title.rstrip("…") + " failed", "\n".join(lines))
            self.refresh_item()

        run_async(lambda: system.run_helper(*commands, on_progress=progress), done)

    def ask(self, title, text, yes, no="Cancel", destructive=False):
        d = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text=title,
        )
        d.format_secondary_markup(text)
        d.add_button(no, Gtk.ResponseType.CANCEL)
        b = d.add_button(yes, Gtk.ResponseType.ACCEPT)
        b.get_style_context().add_class("destructive-action" if destructive else "suggested-action")
        ok = d.run() == Gtk.ResponseType.ACCEPT
        d.destroy()
        return ok

    # Explicit system-theme requirements and privileged activation

    def apply_system(self, component):
        kind, name = component["system"], component["name"]
        req = system.requirement(kind)
        esc = GLib.markup_escape_text
        cmds = []
        installed, active = req.installed, req.active
        if not installed:
            if req.package:
                if not self.ask(
                    f"Install {req.label}?",
                    f"<b>{esc(name)}</b> is for {esc(req.label)}, which isn't installed. drape can "
                    f"install it now (package <tt>{esc(req.package)}</tt>).",
                    "Install and apply",
                ):
                    return
                cmds.append(["apt-install", req.package])
                installed = True
            else:
                d = Gtk.MessageDialog(
                    transient_for=self,
                    modal=True,
                    message_type=Gtk.MessageType.INFO,
                    buttons=Gtk.ButtonsType.CLOSE,
                    text=f"{name} needs {req.label}",
                )
                d.format_secondary_markup(
                    f"That isn't available from your distribution's software sources. You can get it from "
                    f'<a href="{esc(req.url)}">{esc(req.url)}</a>. The theme is downloaded; apply it again '
                    "once that's installed."
                )
                d.run()
                d.destroy()
                return
        cmds.append(["install", kind, component["path"], "--name", name])
        cmds.append(
            {
                "plymouth": ["set-plymouth", name],
                "sddm": ["sddm-theme", name],
                "webgreeter": ["web-greeter-theme", name],
            }[kind]
        )
        switched = False
        if not active and req.activate:
            switched = self.ask(
                "Switch your login screen?",
                esc(req.note) + "\n\nIf you don't switch, the "
                "theme is still set up and will be used if you switch later.",
                "Switch",
                "Keep current login screen",
            )
            if switched:
                cmds.append(req.activate)

        def success():
            if kind == "plymouth":
                self.notify(
                    f"{name} is now your boot splash. You'll see it next time you start up."
                )
            elif switched or active:
                self.notify(
                    f"{name} is now your login screen"
                    + (" (after a restart)." if switched else ".")
                )
            else:
                self.notify(f"{name} is set up. It'll show once you switch to {req.label}.")

        title = f"Setting up {name}…" + (
            " (rebuilding the boot image takes a minute)" if kind == "plymouth" else ""
        )
        self.run_root(cmds, title, success)

    def use_for_login(self, kind, component):
        """Put an installed wallpaper / Controls theme / icons / cursor on the login screen."""
        dm, greeter = system.display_manager(), system.lightdm_greeter()
        if dm != "lightdm" or greeter not in system.GTK_GREETERS:
            error_dialog(
                self,
                "Your login screen can't use this",
                f"Your login screen is {greeter if dm == 'lightdm' else dm}, which has its own themes. "
                "Browse them under Login screen, or switch login screen on the Lock & login page.",
            )
            return
        cmds = login_commands(greeter, kind, component)
        self.run_root(
            cmds,
            "Updating the login screen…",
            lambda: self.notify(f"The login screen now uses {component['name']}."),
        )

    def pick_variant_for_login(self, kind, comps):
        d = Gtk.Dialog(
            title="Which variant for the login screen?",
            transient_for=self,
            modal=True,
            use_header_bar=True,
        )
        d.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Use", Gtk.ResponseType.ACCEPT)
        combo = Gtk.ComboBoxText(margin=12)
        for i, c in enumerate(comps):
            combo.append(str(i), c["name"])
        combo.set_active(0)
        d.get_content_area().add(combo)
        d.show_all()
        ok = d.run() == Gtk.ResponseType.ACCEPT
        choice = comps[int(combo.get_active_id())]
        d.destroy()
        if ok:
            self.use_for_login(kind, choice)

    def use_for_lock(self, component):
        """GNOME has a separate lock screen wallpaper."""
        from gi.repository import Gio as _Gio

        s = _Gio.Settings.new("org.gnome.desktop.screensaver")
        s.set_string("picture-uri", Path(component["path"]).as_uri())
        self.notify(f"The lock screen now shows {Path(component['path']).name}.")
