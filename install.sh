#!/bin/sh
# Per-user install: no root needed. Run ./install.sh --uninstall to undo.
set -e
here=$(cd "$(dirname "$0")" && pwd)

# Per-user launchers and assets; the theme store is managed separately by Drape.
bin="$HOME/.local/bin"
apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
icons="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
desktop=io.github.anolis.Drape.desktop
icon=io.github.anolis.Drape.svg

if [ "$1" = "--uninstall" ]; then
    # Removing the launcher leaves downloaded themes and preferences intact.
    rm -f "$bin/drape" "$apps/$desktop" "$icons/$icon"
    update-desktop-database -q "$apps" 2>/dev/null || true
    echo "drape uninstalled (themes you installed with it are left in place)."
    exit 0
fi

# Keep the launcher linked to this checkout so Git updates take effect immediately.
mkdir -p "$bin" "$apps" "$icons"
ln -sf "$here/bin/drape" "$bin/drape"
cp "$here/data/$icon" "$icons/$icon"
gtk-update-icon-cache -q -t "${icons%/scalable/apps}" 2>/dev/null || true
sed "s|^Exec=drape|Exec=$bin/drape|" "$here/data/$desktop" > "$apps/$desktop"
update-desktop-database -q "$apps" 2>/dev/null || true

# Catalog install links launch the same per-user application.
xdg-mime default "$desktop" x-scheme-handler/ocs x-scheme-handler/ocss
echo "drape installed. Launch it from the menu, or run: drape"
echo "gnome-look.org 'Install' buttons now open in drape."
