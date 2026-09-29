# Desktop and catalog compatibility

Drape uses the Pling OCS API shared by GNOME-Look, KDE-Look and Xfce-Look. The active
desktop and window manager determine which categories are searched and which settings
backend is used. The sidebar hides unsupported categories when the compatibility filter is
enabled, and refreshes when the active window manager changes. Turning the filter off
shows all categories. Installing several desktops does not make their themes interchangeable.

Catalog IDs verified against `/ocs/v1/content/categories` on 2026-09-28:

| Category | Desktop / window manager | Pling IDs |
| --- | --- | --- |
| Desktop style | Cinnamon | 133 |
| Plasma style | KDE Plasma | 104 |
| Global theme | Plasma 5 / Plasma 6 | 121 / 722 |
| Color scheme | KDE Plasma | 112 |
| Window borders | Marco, Metacity, Muffin | 125 |
| Window borders | Xfwm4 | 138 |
| Window borders | KWin / Aurorae | 114; also 717 on Plasma 6 |
| GTK controls | Cinnamon, MATE, GNOME, Xfce | 135 |
| Icons / cursors | Shared | 132 / 107 |

Shared wallpaper categories remain available for desktops with an apply backend.
Unrecognized or incomplete archive listings remain visible. Installation checks extracted
files, and Apply checks compatibility again. Source-only compiled plugins are not installed
as decorations. A catalog match is not a guarantee that a theme's CSS, QML, or external
dependencies work on every release.

## Xfce wallpaper, panel and border support

Xfce uses `xfconf-query` for GTK controls, icons, cursors, Xfwm borders and wallpapers.
Wallpaper application discovers existing monitor/workspace entries and, on X11, active
display connectors and workspaces. It creates missing entries for fresh Xfce sessions
instead of writing obsolete monitor-level settings. It applies the image to all targets, stops cycling and
preserves the placement mode unless backgrounds were disabled (then it uses Zoomed).
If display discovery is unavailable (including Wayland) and no workspace entries exist,
open Xfce Desktop Settings and choose an image once. Drape's current-wallpaper card displays
the first active monitor's configured image when backgrounds differ.

The **Xfce panel** settings page appears only in Xfce. It offers panel size, length,
autohide, position locking and the GTK theme background. Choose a Controls theme to change
panel styling; Cinnamon desktop and Plasma styles do not style an Xfce panel.
Presets (bottom taskbar, top bar, bottom dock, left bar) reshape one selected panel,
keeping its widgets, launchers and output selection. They do not create/remove panels or
install plugins. Other panel settings remain available in Xfce's own Panel Preferences.
**Undo last panel change** restores the preceding settings during the current drape session.
Failed multi-setting applications attempt to roll back; any restore failures are reported.

Xfwm previews use a bundled thumbnail or an illustration of active/inactive title bars from
the theme's artwork. These are approximate previews, not live Xfwm window renders.

References: [Xfce Desktop Settings](https://docs.xfce.org/xfce/xfdesktop/preferences),
[Xfce Panel Preferences](https://docs.xfce.org/xfce/xfce4-panel/preferences).

Run `python3 -m unittest -q` for the unit suite. The opt-in
`python3 -m tests.xfce_integration` checks the real Xfconf service and GTK panel page
on a temporary D-Bus/Broadway display (requires `dbus-run-session`, `xfconf-query`
and `broadwayd`). It verifies wallpaper writes, panel presets, legacy value types
and Undo, and checks that real user configuration files remain unchanged.
Visual application in a running Xfce desktop still needs verification.

## KDE installation and application

All paths honor `XDG_DATA_HOME` (normally `~/.local/share`).

| Format | Installation folder | Apply tool |
| --- | --- | --- |
| Plasma style | `plasma/desktoptheme/<id>` | `plasma-apply-desktoptheme` |
| Global theme | `plasma/look-and-feel/<id>` | `plasma-apply-lookandfeel --apply` |
| Color scheme | `color-schemes/<name>.colors` | `plasma-apply-colorscheme` |
| Aurorae decoration | `aurorae/themes/<id>` | `kwin-applywindowdecoration` |
| Icons | `icons/<name>` | `plasma-changeicons` |
| Cursors | `~/.icons/<name>` | `plasma-apply-cursortheme` |
| Wallpapers | `backgrounds/drape/<pack>` | `plasma-apply-wallpaperimage` |

Helper programs in distribution-specific `libexec` directories are detected. Missing
helpers disable that component; command failures are shown rather than reported as success.
Package IDs are read from metadata and validated before constructing destination paths.
The manifest tracks KDE files for conflict handling, updates and removal.

Plasma 6 global themes require JSON metadata with `KPackageStructure=Plasma/LookAndFeel`.
Old `.desktop`-only global themes are rejected on Plasma 6. Applying a global theme does
not request `--resetLayout`, but can change multiple appearance settings. Themes may require
separate icons, styles or plugins: Drape installs components present in the download, and
does not automatically install external dependencies or run bundled installation scripts.
Kvantum and compiled Qt/KWin plugins are outside the supported archive formats.

References: [KDE theme formats and locations](https://develop.kde.org/docs/plasma/),
[Plasma 6 theme changes](https://develop.kde.org/docs/plasma/theme/theme-porting-to-plasma6/),
[Aurorae format](https://develop.kde.org/docs/plasma/aurorae/), and
[Xfconf commands](https://docs.xfce.org/xfce/xfconf/xfconf-query).

Validation includes unit tests for catalog selection, native command arguments and failures,
Wayland KWin detection, mixed archives, metadata IDs, and install/remove behavior. Isolated
package discovery was also checked with the installed Plasma 6.3 tools. Live rendering and
application in a running Plasma session still need visual verification.
