"""Main-window actions for installing, applying and removing themes."""

from pathlib import Path

from .. import desktop, installer, pling
from ..installer import system_copies
from .common import (
    PART_NAMES,
    TAB_PART,
    error_dialog,
    in_use,
    matches,
    run_async,
    system_theme_active,
)
from .gtk import GLib, Gtk
from .install_progress import InstallProgress


class ThemeActions:
    """Install, apply and remove theme packs through the main window."""

    def remove_wallpaper(self, key, component):
        installer.remove_component(key, component["path"])
        self.notify(f"Removed {Path(component['path']).name}.")
        self.refresh_item()

    # Installation, retries and completion feedback

    def install(
        self, item, file_index=None, apply_kind=None, required_kind=None, context_kind=None
    ):
        if item.id in self.busy:
            return
        feedback = InstallProgress(self._closing)
        self.busy[item.id] = {"progress": feedback}
        self.refresh_item(item.id)

        flags = {"foreign": False, "items": False}  # what the user agreed to replace
        accepted_styles = set()

        def confirm_styles(warnings):
            from .cinnamon_warnings import confirm_install

            pending = [w for w in warnings if (w["name"], w["reason"]) not in accepted_styles]
            if pending and not confirm_install(self, pending):
                return False
            accepted_styles.update((w["name"], w["reason"]) for w in warnings)
            return True

        def work():
            fresh = pling.get(item.id)  # download links are signed and expire
            try:
                return installer.install_item(
                    fresh,
                    file_index,
                    feedback.download,
                    flags["foreign"],
                    flags["items"],
                    status=feedback.status,
                    required_kind=required_kind or ("packs" if apply_kind == "packs" else None),
                    confirm_cinnamon=confirm_styles,
                )
            except installer.ConflictError as e:
                return e  # ask the user on the main thread
            except installer.InstallError as e:
                if isinstance(e, installer.InstallCancelled):
                    return e
                if "wasn't installed by drape" not in str(e):
                    raise
                return e

        def retry():
            nonlocal feedback
            feedback = InstallProgress(self._closing)
            self.busy[item.id] = {"progress": feedback}
            self.refresh_item(item.id)
            run_async(work, done, error)

        def done(result):
            if not isinstance(result, installer.InstallError):
                feedback.complete()
            feedback.close()
            self.busy.pop(item.id, None)
            if isinstance(result, installer.InstallCancelled):
                self.refresh_item(item.id)
                self.notify(str(result))
                return
            if isinstance(result, installer.ConflictError):
                self.refresh_item(item.id)
                self.resolve_conflict(
                    result, lambda reapply: (flags.update(items=True, reapply=reapply), retry())
                )
                return
            if isinstance(result, installer.InstallError):
                self.refresh_item(item.id)
                if self.confirm_overwrite(str(result)):
                    flags["foreign"] = True
                    retry()
                return
            self.installed.updates.pop(item.id, None)
            self.refresh_item(item.id)
            if self.reapply_replaced(result, flags.get("reapply")):
                return
            if (context_kind or apply_kind) and self.choose_installed_components(
                item.id, result, context_kind or apply_kind
            ):
                return
            if any(c.get("system") for c in result["components"]) and not apply_kind:
                self.notify(
                    f"Downloaded {item.name}. Apply it to install it for the whole system "
                    "(you'll be asked for your password)."
                )
                return
            if apply_kind == "packs":
                self.apply_pack(item.id)
                return
            if apply_kind and not any(matches(apply_kind, c) for c in result["components"]):
                # misfiled on gnome-look: don't apply something other than what the tab is for
                parts = sorted(
                    {
                        PART_NAMES.get(p, p)
                        for c in result["components"]
                        for p in c["provides"]
                        if p in PART_NAMES
                    }
                )
                self.notify(
                    f"Installed {item.name}, but it contains {', '.join(parts) or 'something else'}, "
                    f"not {PART_NAMES.get(TAB_PART.get(apply_kind, ''), apply_kind)}, so it wasn't applied. "
                    "Find it on the Installed page."
                )
                return
            if apply_kind:
                comps = [c for c in result["components"] if matches(apply_kind, c)] or result[
                    "components"
                ]
                if len(comps) == 1 or apply_kind == "wallpapers":
                    self.apply(comps[0], apply_kind)
                else:
                    self.notify(
                        f"Installed {item.name} — it has {len(comps)} variants, pick one with Apply."
                    )
            else:
                skipped = result.get("skipped", [])
                self.notify(
                    f"Installed {item.name}."
                    + (f" Skipped {len(skipped)} incompatible component(s)." if skipped else "")
                )

        def error(e):
            feedback.close()
            self.busy.pop(item.id, None)
            self.refresh_item(item.id)
            error_dialog(self, f"Couldn't install {item.name}", e)

        run_async(work, done, error)

    def choose_installed_components(self, key, entry, kind):
        """A download's extra components require an explicit scope choice, not implicit application."""
        from .packs import apply_pack, choices

        groups = choices(entry)
        if not kind or len(groups) < 2:
            return False
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text=f"Apply {entry['title']}?",
        )
        dialog.format_secondary_text(
            "This download includes "
            + ", ".join(PART_NAMES.get(p, p) for p in groups)
            + ". Choose which components to apply. You can select variants in the next step."
        )
        dialog.add_button("Later", Gtk.ResponseType.CANCEL)
        part = desktop.theme_part(kind) if kind != "packs" else None
        if part in groups:
            dialog.add_button(f"Only {PART_NAMES.get(part, part)}", 1)
        dialog.add_button("All components", 2)
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        response = dialog.run()
        dialog.destroy()
        if response == 2:
            apply_pack(self, key)
        elif response == 1 and part in groups:
            apply_pack(self, key, only_kind=kind)
        else:
            self.notify(f"Installed {entry['title']}. Apply it later from Installed.")
        return True

    def reapply_replaced(self, entry, names):
        """After replacing the boot splash / login theme in use, put the new one's files in place."""
        comps = [c for c in entry["components"] if c.get("system") and c["name"] in (names or [])]
        for c in comps:
            self.apply_system(c)
        return bool(comps)

    def resolve_conflict(self, err, then):
        """Another installed item has a theme with the same name: offer to uninstall it and go ahead."""
        esc = GLib.markup_escape_text
        m = installer.load_manifest()
        owners = [(k, o) for k, o in err.owners.items() if k in m]
        names = sorted({n for _k, o in owners for n in o["names"]})
        olds = " and ".join(f"<b>{esc(o['title'])}</b>" for _k, o in owners)
        text = (
            f"<b>{esc(err.title)}</b> installs a theme called <b>{esc(', '.join(names))}</b>, but {olds} "
            f"already has one with that name, and only one can be installed.\n\n"
            f"Replacing uninstalls {olds} (everything it installed) and installs <b>{esc(err.title)}</b>."
        )
        if any(in_use(c) for k, _o in owners for c in m[k]["components"]):
            text += "\n\nIt's in use right now; the new theme takes its place."
        copies = [c for k, _o in owners for c in system_copies(m[k])]
        # a boot splash / login theme with the same name is swapped in place when the new one is applied,
        # so it isn't removed first (the helper won't delete the one in use, and needn't)
        carried = [c for c in copies if c[0] in installer.SYSTEM_KINDS and c[1] in names]
        remove = [c for c in copies if c not in carried]
        reapply = [name for kind, name, _p, _l in carried if system_theme_active(kind, name)]
        if reapply:
            what = (
                "boot splash"
                if any(k == "plymouth" for k, n, _p, _l in carried if n in reapply)
                else "login screen"
            )
            text += (
                f"\n\nIt's your current {what}, so drape will switch it over to the new one "
                "(you'll be asked for your password)."
            )
        elif remove:
            text += "\n\nIt also has copies for the login screen or boot splash, so you'll be asked for your password."
        if not self.ask(
            f"Replace {', '.join(o['title'] for _k, o in owners)}?",
            text,
            "Replace",
            destructive=True,
        ):
            return
        if remove:
            self.run_root(
                [["uninstall", kind, name] for kind, name, _p, _l in remove],
                "Removing the old theme's system copies…",
                lambda: then(reapply),
            )
        else:
            then(reapply)

    def confirm_overwrite(self, msg):
        d = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text="Replace existing theme?",
        )
        d.format_secondary_text(msg + "\n\nReplacing it deletes the existing copy.")
        d.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Replace", Gtk.ResponseType.ACCEPT)
        ok = d.run() == Gtk.ResponseType.ACCEPT
        d.destroy()
        return ok

    def apply_pack(self, key):
        from .packs import apply_pack

        apply_pack(self, key)

    # Revalidate against the current desktop before applying

    def apply(self, component, kind=None):
        if component.get("system"):
            self.apply_system(component)
            return
        only = [desktop.theme_part(kind)] if kind else None
        parts = only or component["provides"]
        if "desktop" in parts:
            from .cinnamon_warnings import approve_application

            try:
                if not approve_application(self, component):
                    return
            except installer.InstallError as error:
                error_dialog(self, "Couldn't save theme preference", error)
                return
        # Visibility preferences never bypass the current desktop's apply requirements.
        if not set(parts) & set(desktop.compatible_parts(component)):
            reason = (
                desktop.cinnamon_rejection_reason(component["path"])
                if "desktop" in parts and "desktop" in component["provides"]
                else None
            )
            self.notify(
                f"{component['name']}: {reason}"
                if reason
                else f"{component['name']} has no compatible component for this desktop and version.",
                Gtk.MessageType.WARNING,
            )
            return
        if "libadwaita" in (only or []):
            from .libadwaita_settings import confirm_application

            if not confirm_application(self, component["name"]):
                return
        try:
            applied = desktop.apply_component(component, only)
        except desktop.ApplyError as e:
            error_dialog(self, "Couldn't apply theme", e)
            return
        if applied:
            if any(p in applied for p in ("wm", "xfwm", "aurorae")):
                self.notify(
                    f"Now using {component['name']} for window borders. They show on apps with a "
                    "classic title bar; apps that draw their own title bar use their toolkit or appearance settings instead.",
                    action=("Show me", self.show_border_sample),
                )
            else:
                note = " " + desktop.qt.RESTART_NOTE if "kvantum" in applied else ""
                if "libadwaita" in applied:
                    note += " " + desktop.libadwaita.RESTART_NOTE
                if "cursors" in applied:
                    note += " " + desktop.cursors.RESTART_NOTE
                self.notify(f"Now using {component['name']} ({', '.join(applied)})." + note)
        else:
            self.notify(
                "Your desktop doesn't support applying this automatically.", Gtk.MessageType.WARNING
            )
        self.refresh_item()

    def choose_wallpaper(self, comps):
        d = Gtk.FileChooserDialog(
            title="Choose a wallpaper", transient_for=self, action=Gtk.FileChooserAction.OPEN
        )
        d.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Set wallpaper", Gtk.ResponseType.ACCEPT)
        d.set_current_folder(str(Path(comps[0]["path"]).parent))
        if d.run() == Gtk.ResponseType.ACCEPT:
            path = d.get_filename()
            self.apply({"provides": ["wallpapers"], "name": Path(path).name, "path": path})
        d.destroy()

    # Confirmed bulk removal and privileged system copies

    def remove_selected(self, selection):
        if not selection:
            return
        try:
            plan = installer.removal_plan(selection, installer.load_manifest())
        except installer.InstallError as exc:
            error_dialog(self, "Couldn't delete selected items", exc)
            self.refresh_item()
            return
        names = [
            Path(path).name if path else f"{entry['title']} (whole pack)"
            for _key, path, entry in plan
        ]
        commands = sorted(
            {
                ("uninstall", kind, name)
                for _key, _path, entry in plan
                for kind, name, _location, _label in system_copies(entry)
            }
        )
        active = any(in_use(c) for _key, _path, entry in plan for c in entry["components"])
        detail = "\n".join(names[:15])
        if len(names) > 15:
            detail += f"\n… and {len(names) - 15} more"
        detail += "\n\nThis permanently deletes the selected wallpapers and whole theme packs, including all pack variants."
        if active:
            detail += "\nSome selected items are currently in use. Choose a replacement after deleting them."
        if commands:
            detail += "\nTheir system copies will also be removed, with a password prompt."
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text=f"Delete {len(plan)} selected item(s)?",
        )
        dialog.format_secondary_text(detail)
        dialog.add_buttons(
            "Cancel", Gtk.ResponseType.CANCEL, "Delete selected", Gtk.ResponseType.ACCEPT
        )
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        dialog.get_widget_for_response(Gtk.ResponseType.ACCEPT).get_style_context().add_class(
            "destructive-action"
        )
        accepted = dialog.run() == Gtk.ResponseType.ACCEPT
        dialog.destroy()
        if not accepted:
            return

        def finish():
            removed, failures = 0, []
            for key, path, entry in plan:
                try:
                    if path is None:
                        installer.remove(key)
                    else:
                        installer.remove_component(key, path)
                    removed += 1
                    if path is None:
                        self.installed.selected.difference_update(
                            item for item in list(self.installed.selected) if item[0] == key
                        )
                    else:
                        self.installed.selected.discard((key, path))
                except (OSError, installer.InstallError) as exc:
                    failures.append(f"{Path(path).name if path else entry['title']}: {exc}")
            self.refresh_item()
            self.notify(f"Deleted {removed} selected item(s).")
            if failures:
                error_dialog(self, "Some items couldn't be deleted", "\n".join(failures))

        if commands:
            self.run_root([list(cmd) for cmd in commands], "Deleting selected items…", finish)
        else:
            finish()

    def remove(self, key):
        entry = installer.load_manifest().get(key)
        if not entry:
            return
        if any(in_use(c) for c in entry["components"]):
            d = Gtk.MessageDialog(
                transient_for=self,
                modal=True,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.NONE,
                text=f"Remove {entry['title']}?",
            )
            d.format_secondary_text(
                "It's currently in use. Your desktop will fall back to its default look "
                "for that part until you pick something else."
            )
            d.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Remove", Gtk.ResponseType.ACCEPT)
            ok = d.run() == Gtk.ResponseType.ACCEPT
            d.destroy()
            if not ok:
                return
        cmds = [["uninstall", kind, name] for kind, name, _path, _label in system_copies(entry)]

        def finish():
            installer.remove(key)
            self.notify(f"Removed {entry['title']}.")
            self.refresh_item()

        if cmds:
            self.run_root(cmds, f"Removing {entry['title']}…", finish)
        else:
            finish()

    def install_link(self, url):
        self.notify("Installing from theme catalog link…")

        reapply = []

        def done(result):
            key, entry = result
            if self.reapply_replaced(entry, reapply):
                return
            self.stack.set_visible_child(self.installed)
            self.installed.load()
            self.notify(f"Installed {entry['title']}. Pick Apply to use it.")

        def attempt(replace=False):
            from .cinnamon_warnings import confirm_install

            def failed(e):
                if isinstance(e, installer.ConflictError):
                    self.resolve_conflict(
                        e, lambda names: (reapply.extend(names), attempt(replace=True))
                    )
                elif isinstance(e, installer.InstallCancelled):
                    self.notify(str(e))
                else:
                    error_dialog(self, "Couldn't install from link", e)

            run_async(
                lambda: installer.install_url(
                    url,
                    replace_items=replace,
                    confirm_cinnamon=lambda warnings: confirm_install(self, warnings),
                ),
                done,
                failed,
            )

        attempt()
