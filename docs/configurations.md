# My configurations

Open **Collections → My configurations**, then select **Save current appearance…**.
Give the configuration a name and choose the current theme components to remember.
Saving records the selected variants without applying any appearance changes.

Supported selections include GTK application themes, a verified Drape native GNOME
override, Kvantum themes, Cinnamon or Plasma styles, supported window decorations,
KDE global themes and color schemes, icons, cursors and the desktop wallpaper.
Selections come from desktop settings, so themes installed outside Drape can also
be saved. Engines unsupported by the current session are omitted. Login, boot and
panel layouts stay in their dedicated settings pages.

Each saved card lists its components and the desktop/window manager where it was
created. **Apply…** reviews current-to-saved selections for the current session;
check the components you want to change. Missing local files and incompatible or
unsupported components cannot be selected. Application checks compatibility again
after confirmation, and reports any components that failed alongside those applied.
KDE global themes are applied first, followed by explicit selected components;
the global theme itself can also affect other appearance settings.

Native GNOME CSS and Kvantum login setup use their existing backups and Restore
actions. The apply dialog explains these changes and any required application restart
or logout/login. Native GNOME styling uses the current light/dark preference.

Use **Rename…** to change a configuration's name. Deleting a configuration requires
confirmation and removes its saved selections; it leaves theme files and the current
appearance intact. Names are unique, ignoring case.

Configurations are local references to themes by name and to wallpapers by URI.
They do not duplicate or package downloaded files, and they are not published to a
shared catalog. Keep the underlying themes installed; removing them makes their
saved selections unavailable until those files are installed again.

Records are stored in `~/.local/share/drape/configurations.json`, respecting
`XDG_DATA_HOME`, with locked atomic writes and a previous-version `.json.bak` backup.
Invalid or unexpectedly missing records are reported instead of being reset.
