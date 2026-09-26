# drape

A theme manager for Linux desktops that treats each part of your look separately. Browse
[gnome-look.org](https://www.gnome-look.org) by category — **icons, cursors, controls (GTK),
window borders, desktop (Cinnamon) themes and wallpapers** — and install or apply any of them in one
click. No hunting for archives, no guessing which folder things go in.

![drape browsing cursor themes](docs/img/shot-cursors.png)

**Website:** https://anolis.github.io/drape/

## What it handles for you

- **Finds what's actually in a download.** Archives are unpacked (including archives inside archives)
  and scanned: icon themes, cursor themes, GTK/window/Cinnamon themes and images are each detected
  from their contents, not from the category the uploader picked.
- **Puts things in the right place.** Icons → `~/.local/share/icons`, cursors → `~/.icons`,
  themes → `~/.themes`, wallpapers → `~/.local/share/backgrounds/drape` (and added to Cinnamon's
  Backgrounds settings).
- **Converts Windows cursor packs.** Lots of cursor uploads are Windows `.cur`/`.ani` packs; drape
  converts them to Linux cursors automatically, using the pack's `Install.inf` to map each cursor.
- **Shows variants before you pick.** Packs with Dark / Light / Compact variants get an
  [Apply | ▾] button; ▾ lists the variants with a live preview drawn from each one's own files.
- **Applies them.** Sets the matching setting for Cinnamon or GNOME. Packs with several variants
  (Dark, Compact, …) let you pick which one.
- **Boot splash, login and lock screens.** Plymouth boot splashes and SDDM / LightDM web greeter
  login themes install and apply in one click (with one password prompt). If a theme is for a
  login screen you don't have, drape offers to install it and to switch to it. Your own wallpaper,
  Controls theme, icons and cursor can be used for the LightDM login screen too, and the
  **Lock & login** page sets up the Cinnamon lock screen's clock and fonts. A setting (on by
  default) only shows login themes that work on your computer.
- **Shows where everything went.** ⋯ → *Show installed files* lists every folder an item
  installed (including system copies), with sizes, file lists and a button to open each one.
- **Cleans up.** Everything is tracked in `~/.local/share/drape/installed.json`, so Remove deletes
  exactly what was installed, and *Check for updates* compares against gnome-look.org.
- **Works with gnome-look.org's own Install buttons** (`ocs://` links) once registered.
- **Safe by default.** Downloads are checksum-verified, archive extraction rejects path tricks, and
  drape won't overwrite themes it didn't install without asking.

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

Needs Python 3.12+, PyGObject with GTK 3, Pillow and `python3-requests` (all present on Cinnamon/GNOME desktops), plus polkit for boot splash and login screen changes.

```sh
./install.sh            # per-user, no root; adds a menu entry and the ocs:// handler
./install.sh --uninstall
```

Or run it in place: `./bin/drape`.

## Command line

```sh
drape search icons papirus           # kinds: icons cursors gtk wm desktop wallpapers login boot
drape show 1166289                   # details and download variants
drape install 1166289 --apply        # --file N picks a variant
drape install-url 'ocs://install?url=…'
drape list
drape apply <id> [variant-name]
drape remove <id>
drape updates
```

## License

GPL-3.0. Themes and wallpapers belong to their creators on gnome-look.org; drape isn't affiliated
with Pling or gnome-look.org.

## Development

```sh
python3 -m unittest -v
```
