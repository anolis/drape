# drape

A theme manager for Linux desktops that treats each part of your look separately. Browse
[GNOME-Look](https://www.gnome-look.org), [KDE-Look](https://www.kde-look.org), and
[Xfce-Look](https://www.xfce-look.org) through their shared Pling catalog by category — **icons,
cursors, controls (GTK), window borders, desktop styles, KDE global themes, color schemes and wallpapers** — and install or apply them in one
click. No hunting for archives, no guessing which folder things go in.

![drape browsing cursor themes](docs/img/shot-cursors.png)

**Website:** https://anolis.github.io/drape/

## What it handles for you

- **Finds what's actually in a download.** Archives are unpacked (including archives inside archives)
  and scanned: icon themes, cursor themes, GTK/window/Cinnamon themes and images are each detected
  from their contents, not from the category the uploader picked.
- **Puts things in the right place.** Icons → `~/.local/share/icons`, cursors → `~/.icons`,
  GTK/Cinnamon/Xfwm themes → `~/.themes`, wallpapers → `~/.local/share/backgrounds/drape` (and added to Cinnamon's
  Backgrounds settings when running Cinnamon).
- **Converts Windows cursor packs.** Lots of cursor uploads are Windows `.cur`/`.ani` packs; drape
  converts them to Linux cursors automatically, using the pack's `Install.inf` to map each cursor.
- **Shows variants before you pick.** Packs with Dark / Light / Compact variants get an
  [Apply | ▾] button; ▾ lists the variants with a live preview drawn from each one's own files.
- **Deletes installed items in batches.** Check wallpaper or theme-pack cards in Installed,
  or use Select all in category, then Delete selected. Selection carries across categories;
  one confirmation lists the affected images/packs and flags anything currently in use.
- **Applies them.** Uses Cinnamon, MATE or GNOME settings, or Xfce's Xfconf settings for controls,
  icons, cursors, wallpapers and Xfwm borders. MATE wallpaper settings use filenames, while Cinnamon/GNOME
  use URIs. Packs with several variants (Dark, Compact, …) let you pick which one.
- **Styles the Xfce panel.** An Xfce-only sidebar page controls panel size, length, hiding,
  position locking and GTK theme backgrounds. Bottom taskbar, top bar, bottom dock and left bar
  presets reshape the selected panel while keeping its widgets, with Undo for the last change
  during this app session. Xfwm variants show previews from their own title-bar artwork.
  Wallpaper application covers configured monitors/workspaces, discovers active displays on X11
  even before their wallpaper settings exist, and stops wallpaper cycling.
- **Supports KDE Plasma.** Plasma styles, global themes, color schemes and Aurorae window
  decorations install into their native user data folders. Icons, cursors and wallpapers use
  Plasma's apply tools too. Catalog selection follows Plasma 5/6, with KWin detected on X11
  or through D-Bus on Wayland. Global themes apply without requesting a desktop layout reset.
  Native tools must be installed; missing tools disable the corresponding feature. See
  [desktop compatibility](docs/compatibility.md) for category mappings and limitations.
- **Matches the running desktop and window manager.** With **Only show themes that work on this
  computer** enabled (the default), window borders are scoped to Marco/Metacity/Muffin, Xfwm or KWin/Aurorae,
  and Cinnamon desktop themes are excluded from other sessions. Known incompatible downloads
  are filtered after archive inspection; incomplete or unrecognized listings remain visible
  until installation checks their extracted contents. GTK controls require a GTK 3 component.
  Mixed archives install usable components, and Apply checks compatibility again. The default
  download selection tries another variant if the first is incompatible; an explicitly chosen
  download is never silently substituted. The sidebar shows supported categories and updates when the window manager changes.
  Turn the filter off to install for another session, or use CLI `--all-themes`; this does not
  enable applying unsupported components.

  Compiz decorators, GNOME Shell themes, compiled Qt styles/KWin plugins, Kvantum engines,
  and non-KWin window borders on Wayland are not supported yet.
  GTK application themes on KDE should be configured in KDE's own settings. Drape does not assume these sessions use
  GNOME settings just because the schemas are installed. Compatibility checks identify formats,
  not whether every theme's CSS or artwork renders correctly.
- **Boot splash, login and lock screens.** Plymouth boot splashes and SDDM / LightDM web greeter
  login themes install and apply in one click (with one password prompt). If a theme is for a
  login screen you don't have, drape offers to install it and to switch to it. Your own wallpaper,
  Controls theme, icons and cursor can be used for the LightDM login screen too, and the
  **Lock & login** page sets up the Cinnamon lock screen's clock and fonts. The compatibility
  filter also limits login themes to installed login managers.
- **Shows where everything went.** ⋯ → *Show installed files* lists every folder an item
  installed (including system copies), with sizes, file lists and a button to open each one.
- **Cleans up.** Everything is tracked in `~/.local/share/drape/installed.json`, so Remove deletes
  exactly what was installed, and *Check for updates* compares against the Pling catalog.
- **Works with the catalogs' own Install buttons** (`ocs://` links) once registered.
- **Safe by default.** Downloads are checksum-verified when the catalog provides a checksum, archive extraction rejects path tricks, and
  drape won't overwrite themes it didn't install without asking.

## Download failures

Catalog and file requests retry HTTP 500, 502, 503 and 504 failures twice. If the server
still fails, Drape reports the failure. You can also install an archive from the author
with `drape install-file /path/to/theme.zip`; external installation scripts are not run.

## Root access

Boot splash and login screen changes need root. They go through a small helper
(`drape/helper.py`) run with `pkexec`, so you get the normal password prompt and only those
specific actions run as root. The helper copies files without following links swapped in along
the way, marks everything it installs, and never replaces or removes system themes it didn't
install. Its Plymouth handling is adapted from
[Plymouth Configurator](https://github.com/anolis/plymouth-configurator).

GDM login themes aren't supported: they work by replacing a core GNOME Shell file, which breaks
when GNOME updates.

## Install

Needs Python 3.12+, PyGObject with GTK 3, Pillow and `python3-requests`, plus polkit for boot splash and login screen changes. Xfce uses `xfconf-query`; KDE uses its native Plasma apply tools.

```sh
./install.sh            # per-user, no root; adds a menu entry and the ocs:// handler
./install.sh --uninstall
```

Or run it in place: `./bin/drape`.

## Command line

```sh
drape search icons papirus           # kinds: icons cursors gtk wm desktop lookandfeel colors wallpapers login boot
drape search colors arc              # KDE color schemes
drape search lookandfeel             # global themes for your Plasma version
drape show 1166289                   # details and download variants
drape install 1166289 --apply        # --file N picks a variant
drape install-url 'ocs://install?url=…'
drape list
drape apply <id> [variant-name]
drape remove <id>
drape updates
```

## License

GPL-3.0. Themes and wallpapers belong to their creators; drape is not affiliated with Pling,
GNOME-Look, KDE-Look or Xfce-Look.

## Development

```sh
python3 -m unittest -v
```
