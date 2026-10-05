# Video wallpapers

The first backend supports Cinnamon on X11. Install `mpv` through your system's
package manager, open **Shared assets → Video wallpapers**, add a local video and
choose **Play wallpaper**. Supported file extensions are MP4, WebM, MKV, MOV, M4V,
AVI and OGV; actual decoding depends on the installed mpv and codecs.

Videos loop silently on every monitor. Fill crops the edges to cover each screen;
Fit keeps the whole picture with bars where needed. Pause/Resume keeps the current
frame. Playback pauses on screen lock, and unlocking preserves a manual pause.
Monitor changes rebuild playback surfaces for the new layout.

Starting playback puts Drape in Cinnamon's system tray. Closing the main window
hides it; the tray's **Show Drape** action or launching Drape again reopens it.
The tray also offers Pause/Resume, Stop wallpaper and Quit Drape. Stop reveals
the existing static wallpaper and leaves Drape available in the tray. Quit stops
the player and exits Drape. If the tray applet is disabled, Drape keeps its window
visible and explains how to enable the applet.

Static wallpaper settings stay unchanged beneath the video. Choosing a still
wallpaper through Drape stops video playback before applying the image. There is
no login startup entry in this version: start playback manually after login.
Menu entries and existing startup commands are not edited.

The local library at `~/.config/drape/videos.json` (or `$XDG_CONFIG_HOME/drape/videos.json`)
stores file references and the last layout, with locked, atomic writes. No videos
are copied or deleted. Removing an entry stops it if it is playing and only removes
the reference. Moving or deleting the original file makes the entry unavailable.

## Backend

`video_wallpapers.py` manages the library and private local controls. `video_player.py`
owns the GTK/X11 surfaces and mpv child processes, independently of browsing and
archive scanning. `video_mpv.py` handles mpv arguments and JSON controls.
`video_x11.py` places only Drape's surfaces below existing desktop windows through
Muffin's EWMH desktop-manager interface. Ordinary GDK lowering can be rejected by
Muffin's user-time checks; stacking is verified before revealing a decoded video,
then checked again when desktop windows change. Input regions are empty, preserving
Nemo's icons, selection, drag-and-drop and context menu.

The worker listens on a private Unix socket in `$XDG_RUNTIME_DIR/drape-video`
(or a private per-user temporary directory). It runs one mpv per monitor, disables
audio, scripts, external references and player input, and does not inhibit the
screensaver. Stop terminates only those child processes. Playback failures release
the video surfaces, reveal the static wallpaper and appear in the page/tray status.
Worker startup errors are logged to `player.log` in that runtime directory.

The GTK page is in `ui/video_wallpapers.py`; application lifetime and the extensible
tray menu are in `ui/tray.py`. Drape can reconnect to a surviving player after a GUI
crash, without starting duplicate players.
