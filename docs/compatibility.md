# Desktop and catalog compatibility

Drape uses the Pling OCS API shared by GNOME-Look, KDE-Look and Xfce-Look. The active
desktop and window manager determine which categories are searched and which settings
backend is used. The sidebar hides unsupported categories and refreshes when the active
window manager changes. Unsupported sections stay hidden in both the sidebar and
Installed tabs even when the archive filter is off. Installing several desktops does
not make their themes interchangeable.

Catalog IDs verified against `/ocs/v1/content/categories` on 2026-09-28:

| Category | Desktop / window manager | Pling IDs |
| --- | --- | --- |
| Desktop style | Cinnamon | 133 |
| Plasma style | KDE Plasma | 104 |
| Global theme | Plasma 5 / Plasma 6 | 121 / 722 |
| Color scheme | KDE Plasma | 112 |
| Window borders | Marco, Metacity, Muffin before Cinnamon 5.4 | 125 |
| Window borders | Xfwm4 | 138 |
| Window borders | KWin / Aurorae | 114; also 717 on Plasma 6 |
| GTK controls | Cinnamon, MATE, GNOME, Xfce | 135 |
| Icons / cursors | Shared | 132 / 107 |

Cinnamon 5.4 and newer render all title bars with the GTK Controls theme and no longer
use Metacity border themes. Drape hides their separate Window borders section and does
not write the obsolete border-theme setting. See [Linux Mint's Cinnamon 5.4 release notes](https://linuxmint.com/rel_vanessa_cinnamon_whatsnew.php).

Theme packs combine the applicable GTK, desktop-style, window-border, global-theme and
color-scheme catalogs above; catalog IDs were rechecked on 2026-10-03. A bundle must contain
at least two supported appearance parts, including controls, desktop style or window borders.
Compatible KDE global themes also qualify. An archive's GTK 2/3/4 directories count as one
part, and several variants of one part do not qualify by themselves. After inspection,
unverified downloads stay hidden, and disabling the general compatibility filter does not
broaden Theme packs. Extraction rechecks compatibility before installation, and Apply pack
allows one variant per part or Keep current. Drape does not run an archive's installation scripts.

Shared wallpaper categories remain available for desktops with an apply backend.
Unrecognized or incomplete archive listings stay hidden while filtering is enabled. Disabling the filter reveals them with a Compatibility unverified label. Installation checks extracted
files, and Apply checks compatibility again. Source-only compiled plugins are not installed
as decorations. A catalog match is not a guarantee that a theme's CSS, QML, or external
dependencies work on every release.

## Desktop-aware compatibility filtering

**☰ → Hide incompatible themes for this desktop** defaults to on and controls every theme category in Browse and Installed. Compatibility follows the desktop, window manager, display manager and known version requirements. Unsupported sections remain hidden independently of this preference. Disabling the filter reveals incompatible downloads within supported sections, but does not enable applying unsupported components.

The same rules evaluate each archive variant and each extracted component. A mixed bundle can retain usable GTK or icon components while its Cinnamon styles are hidden from the Desktop category. One compatible download keeps a card available; an unverified alternate download does not override a failed check. Cards load immediately while compatibility checks run independently of preview loading. Visible incompatible or unverified results fade out as their individual scans finish. Off-screen cards keep their layout space and wait to fade out until they re-enter the viewport, so background scans do not shift the current rows; one positively identified compatible variant keeps a card visible. A slow scan never holds the first page behind a batch barrier.

Cinnamon checks work in both directions across the 5.4 dialog-style change. Newer Cinnamon requires `.dialog` or `.prompt-dialog` styles. Older Cinnamon uses `.modal-dialog`; an inspected modern-only theme is incompatible there. Themes including both generations can remain usable on both. Imported styles are included; unresolved imports and bounded partial reads remain unverified. These checks identify known CSS mismatches, rather than certifying every panel, menu or app style on every Cinnamon release.

Window border formats follow the active window manager: Marco/Metacity, pre-5.4 Muffin, Xfwm or KWin/Aurorae. GTK controls require GTK 3 content. Plasma global-theme catalogs follow Plasma 5/6 and installation validates their extracted metadata. Login-theme formats follow the detected display manager. Installed files and records are preserved when filtering hides an item.

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

## Local compatibility evidence

Drape builds a SQLite index in `$XDG_DATA_HOME/drape/compatibility.sqlite3`
(normally `~/.local/share/drape/compatibility.sqlite3`). Archive evidence is keyed by
catalog item, filename and checksum, or modification date when no checksum is supplied.
Results are re-evaluated for the current desktop, window manager and versions.
Complete listings are reused for a week; incomplete checks retry after ten minutes.
Matching installed bundle files can supply additional local evidence without claiming
that companion themes or other downloads were included in that archive.

Export observations with `drape compatibility export --output compatibility.json`.
The output's `cursor` can be passed to a later export as `--since CURSOR` to produce a
diff. Repeated unchanged checks do not create duplicate contributions. Import reviewed
records with `drape compatibility import compatibility.json`; local inspection takes
precedence and imported claims are evaluated by local compatibility rules. Installed-file
observations are recorded separately and are not treated as portable archive listings.

These commands prepare for a community database. There is no central service or automatic
upload/download configured yet. Exports contain public catalog identifiers, filenames and
desktop/version context; they omit download tokens and local filesystem paths.

## Browsing and applying components

Click an uploader name on a card or in theme details to open their public Pling profile,
biography, website and paginated uploads. Installed items also offer **Uploader profile**
in their menu. Uploads open details for their own catalog category.

Downloads with multiple usable appearance components offer **All components**, **Only
[current section]**, or **Later** after installation. The component chooser lists every
supported part, with variant selection and **Keep current**. Companion icon/cursor names
in metatheme metadata are labeled as separate downloads when their files are absent.

Browse sections remember their scroll positions per search and sort order. Installed
categories and settings sections also save their positions across launches. Drape restores
a deep catalog position by loading additional pages as content becomes available.
