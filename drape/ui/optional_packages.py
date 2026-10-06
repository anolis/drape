"""Reviewed, explicit package installation for optional playback sources."""

import os
import shlex
import shutil

from .. import dependencies
from .common import error_dialog, run_async
from .gtk import Gtk


def install(parent, keys, finished):
    alive = [True]
    handler = parent.connect("destroy", lambda *_: alive.__setitem__(0, False))

    def finish():
        if alive[0]:
            parent.disconnect(handler)
            finished()

    def failed(error):
        if alive[0]:
            error_dialog(parent, "Could not install playback support", error)
        finish()

    def probe():
        command = dependencies.install_command(keys)
        if command is None:
            raise RuntimeError(
                "Install these packages with your system's package manager: "
                + ", ".join(dependencies.LABELS[key] for key in keys)
            )
        upgrade = dependencies.pacman_database_missing(command)
        if upgrade:
            command = ["-Syu" if arg == "-S" else arg for arg in command]
        return command, upgrade

    def confirm(result):
        if not alive[0]:
            return  # Closing Drape during the probe cannot start an installation.
        command, upgrade = result
        privileged = command if os.geteuid() == 0 else ["pkexec", *command]
        if os.geteuid() != 0 and not shutil.which("pkexec"):
            failed(
                RuntimeError(
                    "Administrator authentication needs pkexec. Run manually: sudo "
                    + shlex.join(command)
                )
            )
            return
        text = "Install optional playback packages?"
        explanation = (
            "Drape will run this command. Administrator authentication may be required:\n\n"
            + shlex.join(privileged)
        )
        if upgrade:
            explanation += "\n\nThe repository databases are missing. This command also upgrades all installed system packages and may download substantially more than the playback dependencies."
        dialog = Gtk.MessageDialog(
            transient_for=parent,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=text,
        )
        dialog.format_secondary_text(explanation)
        response = dialog.run()
        dialog.destroy()
        if response != Gtk.ResponseType.YES or not alive[0]:
            finish()
            return
        success, output = dependencies.Dialogs().install(privileged)
        if not success:
            failed(RuntimeError(output))
        else:
            finish()

    run_async(probe, confirm, failed)
