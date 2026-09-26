# drape

A theme manager for Linux desktops that treats each part of your look separately. Browse
[gnome-look.org](https://www.gnome-look.org) by category — **icons, cursors, controls (GTK),
window borders, desktop (Cinnamon) themes and wallpapers** — and install or apply any of them in one
click. No hunting for archives, no guessing which folder things go in.

## What it handles for you

- **Finds what's actually in a download.** Archives are unpacked (including archives inside archives)
  and scanned: icon themes, cursor themes, GTK/window/Cinnamon themes and images are each detected
  from their contents, not from the category the uploader picked.
- **Puts things in the right place.** Icons → `~/.local/share/icons`, cursors → `~/.icons`,
  themes → `~/.themes`, wallpapers → `~/.local/share/backgrounds/drape` (and added to Cinnamon's
  Backgrounds settings).
- **Applies them.** Sets the matching setting for Cinnamon or GNOME. Packs with several variants
  (Dark, Compact, …) let you pick which one.
- **Cleans up.** Everything is tracked in `~/.local/share/drape/installed.json`, so Remove deletes
  exactly what was installed, and *Check for updates* compares against gnome-look.org.
- **Works with gnome-look.org's own Install buttons** (`ocs://` links) once registered.
- **Safe by default.** Downloads are checksum-verified, archive extraction rejects path tricks, and
  drape won't overwrite themes it didn't install without asking.

## Install

Needs Python 3.12+, PyGObject with GTK 3, and `python3-requests` (all present on Cinnamon/GNOME desktops).

```sh
./install.sh            # per-user, no root; adds a menu entry and the ocs:// handler
./install.sh --uninstall
```

Or run it in place: `./bin/drape`.

## Command line

```sh
drape search icons papirus           # kinds: icons cursors gtk wm desktop wallpapers
drape show 1166289                   # details and download variants
drape install 1166289 --apply        # --file N picks a variant
drape install-url 'ocs://install?url=…'
drape list
drape apply <id> [variant-name]
drape remove <id>
drape updates
```

## Development

```sh
python3 -m unittest -v
```
