# Qt application themes

Drape's **Kvantum themes** section browses the Pling Kvantum catalog (category 123).
It is available on any desktop when a Kvantum style plugin is installed. GTK themes
do not style Qt widgets; many authors publish matching GTK and Kvantum themes.

Open **Qt / Kvantum applications → Kvantum setup** to see which engines are present. Qt 5 and Qt 6
need separate plugins. The page offers installation from configured repositories
on Arch-based and Debian/Ubuntu-based systems, with a confirmation and administrator
authentication. An unavailable package or an apt plan that would remove packages is
rejected. Other distributions have a link to the upstream installation guide.

Choose **Enable Kvantum for Qt applications**, then browse and install a Qt theme.
The page shows saved setup, the selected theme and whether the running desktop
launcher has inherited Kvantum. A saved setting does not change an already-running
desktop's environment; the page explicitly requests a new login in that case.
**Reapply Kvantum setup** repairs settings saved by older
versions that only wrote `~/.profile`.
Install & apply also enables the engine. The first setup requires logging out and
back in for desktop-launched applications; subsequently, restart Qt applications
after switching themes. Existing windows do not change their widget engine live.
**Restore previous Qt appearance** confirms what will be restored, then restores
the original Qt override and selected theme. Log out and back in afterward.
Drape saves the baseline before its first change and retains it across subsequent
theme switches. Unrelated profile edits and application assignments are preserved;
conflicting edits to a setting Drape changed are reported rather than overwritten.
Backups live in `$XDG_CONFIG_HOME/drape/qt-restore.json`. Downloads and installed
engine packages are retained. Older setups have no original backup: the confirmation
explains that only Drape's identifiable login override can be removed and the earlier
appearance cannot be reconstructed automatically.

Kvantum themes need matching `<name>.kvconfig` and `<name>.svg` files. Drape installs
each variant into `$XDG_CONFIG_HOME/Kvantum/<name>/` and selects it in
`Kvantum/kvantum.kvconfig`, preserving application-specific assignments. Duplicate
names within an archive are renamed together with their file pairs. Theme packs
include Kvantum in their component chooser when an engine is available.

Drape persists `QT_STYLE_OVERRIDE=kvantum` in its own environment.d file and marked
blocks in `~/.profile`, `~/.xprofile` and `~/.xsessionrc`. It also updates the active
shell's login file: zsh uses `.zprofile` (respecting `ZDOTDIR`), while bash uses an
existing `.bash_profile` or `.bash_login`. Drape does not create a new bash_profile
that would prevent bash reading .profile. This covers display managers such as SDDM
that choose shell-specific startup files. It also updates the D-Bus activation
environment when the tool is available.
Other login setup is preserved. This is session-wide setup for Qt widget apps,
including menu and autostart launches after a new login.

Drape does not edit application menu entries or autostart commands for Qt setup.
Any future launcher override must require a separate explicit confirmation naming
the entry and file, showing the original and replacement command, explaining its
effect and providing a way to undo the change. Enabling Kvantum is not permission
to rewrite launchers.

Apps with their own stylesheet can override the selected widget theme. OpenSnitch's
**Preferences → UI** theme selection can do this; select its default appearance to
follow the Qt style and restart its GUI. Qt appearance settings use session-wide setup
rather than a temporary per-application relaunch. Application-specific appearance
preferences are managed in those applications.
Flatpak/Snap apps may need the engine within
their sandbox. Qt Quick/QML applications use a different styling system. Drape does
not install arbitrary compiled style plugins from theme archives or execute theme
installation scripts.

## Which apps does each engine affect?

The sidebar separates application engines from desktop shells, window decorations,
shared assets, and startup/login screens. Every theme catalog explains its scope.

| Theme type | What it changes |
| --- | --- |
| GTK applications | GTK app widgets, with support for the app's GTK version required |
| GNOME / libadwaita | Native GNOME apps through an explicitly applied GTK 4 user stylesheet |
| Kvantum (Qt) | Qt widget apps such as OpenSnitch using their System appearance |
| Desktop shell | Panels, menus and shell widgets for the supported desktop |
| Window decorations | Frames and title bars drawn by a supported window manager |
| Shared assets | Icons, cursors and wallpapers, independently of widget themes |

Modern GNOME Files and Settings use libadwaita, which provides its own appearance.
They do not generally follow downloaded GTK 3 themes, and Kvantum does not affect
them. Use **GNOME / libadwaita → GTK 4 themes** for GTK 4 styles, or **Native GNOME setup**
to apply the current GTK theme's GTK 4 files. Applying replaces
`$XDG_CONFIG_HOME/gtk-4.0/gtk.css` with an import of the theme's stylesheet after
confirmation and parser validation. The original file, permissions or symlink are
saved in `$XDG_CONFIG_HOME/drape/libadwaita-restore.json` before the first change.
Later theme switches retain that baseline. **Restore previous native GNOME appearance**
restores it; conflicting edits are reported instead of overwritten. Other GTK settings
and theme files are preserved. GNOME's dark preference selects `gtk-dark.css` when
available, otherwise `gtk.css`; reapply after changing the preference. Restart native
apps after applying or restoring. GTK 4 file presence and valid syntax do not guarantee
that a stylesheet covers every libadwaita widget or version. Sandboxed apps may need
their own configuration access.

Kitty uses its own rendering and appearance settings; on GNOME Wayland its
titlebar color is controlled by `wayland_titlebar_color`. Its styling is separate
from GTK and Qt themes. Drape does not rewrite launchers or force a different
display backend to change these decorations.

References: [Kvantum setup and theme paths](https://github.com/tsujan/Kvantum/blob/master/Kvantum/INSTALL.md),
[Kvantum selection configuration](https://github.com/tsujan/Kvantum/blob/master/Kvantum/kvantummanager/KvCommand.cpp),
[Arch Kvantum packages](https://archlinux.org/packages/extra/x86_64/kvantum/), and
[OpenSnitch's custom UI themes](https://github.com/evilsocket/opensnitch/wiki/Events-window-themes),
[Libadwaita appearance](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/1-latest/styles-and-appearance.html),
[GTK 4 user stylesheets](https://docs.gtk.org/gtk4/class.CssProvider.html),
[Kitty titlebar settings](https://sw.kovidgoyal.net/kitty/conf/#opt-kitty.wayland_titlebar_color).
