#!/bin/sh
# Per-user install: no root needed. Run ./install.sh --uninstall to undo.
set -e
here=$(cd "$(dirname "$0")" && pwd)
bin="$HOME/.local/bin"
apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
desktop=io.github.anolis.Drape.desktop

if [ "$1" = "--uninstall" ]; then
    rm -f "$bin/drape" "$apps/$desktop"
    update-desktop-database -q "$apps" 2>/dev/null || true
    echo "drape uninstalled (themes you installed with it are left in place)."
    exit 0
fi

mkdir -p "$bin" "$apps"
ln -sf "$here/bin/drape" "$bin/drape"
sed "s|^Exec=drape|Exec=$bin/drape|" "$here/data/$desktop" > "$apps/$desktop"
update-desktop-database -q "$apps" 2>/dev/null || true
xdg-mime default "$desktop" x-scheme-handler/ocs x-scheme-handler/ocss
echo "drape installed. Launch it from the menu, or run: drape"
echo "gnome-look.org 'Install' buttons now open in drape."
