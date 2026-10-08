# Live wallpapers

Live wallpapers support Cinnamon, GNOME, MATE, Xfce and Plasma 6 on X11. GNOME video, XScreenSaver
animations and audio visualizations were verified on GNOME 48.7 with three monitors.
GNOME/Wayland remains unsupported: an XWayland display cannot host desktop wallpapers.
Install `mpv` through your system's
package manager, open **Shared assets → Live wallpapers**, choose **Local video**, add a file and
choose **Play wallpaper**. Supported file extensions are MP4, WebM, MKV, MOV, M4V,
AVI and OGV; actual decoding depends on the installed mpv and codecs.

Videos loop silently on every monitor. Fill crops the edges to cover each screen;
Fit keeps the whole picture with bars where needed. Pause/Resume keeps the current
frame. Playback pauses on screen lock, and unlocking preserves a manual pause.
Monitor changes rebuild playback surfaces for the new layout.

Once playback is running, changing the source, selected video or animation, layout,
frame-rate target, visualization style, color or audio inputs applies automatically.
Quick edits are combined into one switch. A manual pause is preserved; changing
controls while stopped does not start playback.

Starting playback puts Drape in Cinnamon, MATE, Xfce or Plasma's system tray. Closing the main window
hides it; the tray's **Show Drape** action or launching Drape again reopens it.
The tray also offers Pause/Resume, Stop wallpaper and Quit Drape. Stop reveals
the existing static wallpaper and leaves Drape available in the tray. Quit stops
the player and exits Drape. The page also offers **Quit Drape**. If the desktop's tray
applet is disabled, Drape keeps its window visible and explains how to enable the applet.

GNOME does not require a tray extension. Closing Drape during playback hides the
window and keeps its background process running. Launch Drape again to reopen
Pause/Resume, Stop and Quit controls. If GNOME has a compatible tray extension,
the tray menu is also available. The GNOME host uses its own screen-lock service;
it does not enable extensions or change desktop or startup settings.

MATE keeps Caja's desktop icons and desktop clicks available. Its host copies
rendered frames into Caja's existing shared background and asks Caja to redraw
its icons, without changing desktop settings or restarting Caja. Presentation is
capped at 30 FPS, and videos use software X11 output so hardware overlays do not
bypass the copied background. Stop, Quit and loss of the player connection restore the exact
original background pixels. If another application changes the static wallpaper,
Drape stops live playback and leaves the new background alone. This requires the
X11 Composite extension; the MATE host uses `org.mate.ScreenSaver` for lock
notifications when MATE's screen locker is running. MATE 1.26 with Compiz was
verified with video, animations and all audio input combinations on three monitors;
video and animations were also verified with Marco, with and without compositing.

Xfce keeps Xfdesktop's icons and desktop input available. On first Play, Drape
asks before briefly restarting Xfdesktop with a temporary background adapter.
Choose **Cancel** to leave it alone or **Enable & play** to proceed. This requires
`gcc` or `clang` to build a small adapter once; no development headers are needed.
The adapter changes only that Xfdesktop process's background rendering. Menu entries,
startup commands, Xfconf settings, the panel and Thunar windows are untouched.
Renderers stay offscreen so animations cannot cover icons if they raise their window.
Presentation is capped at 30 FPS and videos use software X11 output.

**Stop & restore static wallpaper** stops the source while keeping the adapter
available for the next selection. **Restore standard Xfce desktop** stops playback
and reloads Xfdesktop without the adapter. Quit Drape or a disconnected player also
restores the standard desktop; an unexpectedly killed guardian is recovered by the
player. Source switches reuse the same icon host. The host uses `org.xfce.ScreenSaver`
when Xfce's screen locker is running. Xfdesktop 4.20.1 was verified with video,
animations and all audio input combinations on three monitors with Compiz; video,
animations and restoration were also verified with Xfwm, with and without compositing.
Wayland remains unsupported.

Plasma 6 keeps its own desktop, icons, widgets and panels. First Play installs
Drape's native wallpaper plugin in your user data directory and selects it for
each visible screen in the current activity. Other activities are left alone.
Plasma is not restarted, and panel layouts, menu entries and startup settings
are not changed. Your previous wallpaper plugins and their settings are retained.
**Stop & restore static wallpaper**, Quit or a lost player connection returns
each screen to its previous wallpaper. **Restore previous Plasma wallpapers**
also recovers a saved session after an unexpected helper termination. Choosing
another wallpaper type through Plasma stops playback, preserving your new choice
and restoring the other screens. Restoration only affects screens still owned by Drape.

Plasma presentation is capped at 20 FPS with locally encoded JPEG frames, and
videos use software X11 output. This can use more CPU than hardware playback,
especially across several high-resolution monitors. The higher XScreenSaver
frame-rate targets still control the animation itself; presentation remains
capped. Plasma uses `org.freedesktop.ScreenSaver` for lock notifications and can
keep playback running without a tray. Plasma 6.3.6 with KWin/X11 was verified
with video, animations, all audio input combinations and crash restoration on
three monitors with fractional scaling. Plasma 5 and Plasma/Wayland are not
supported by this host yet.

Static wallpaper settings are retained during live playback. Choosing a still
wallpaper through Drape stops playback before applying the image. There is
no login startup entry in this version: start playback manually after login.
Menu entries and existing startup commands are not edited.

The local library at `~/.config/drape/videos.json` (or `$XDG_CONFIG_HOME/drape/videos.json`)
stores file references and the last layout, with locked, atomic writes. No videos
are copied or deleted. Removing an entry stops it if it is playing and only removes
the reference. Moving or deleting the original file makes the entry unavailable.

## XScreenSaver animations

Choose **XScreenSaver animation**, select an installed animation and click **Play wallpaper**.
The description comes from its packaged metadata. The frame-rate target offers
15, 30 or 60 FPS where the animation exposes a compatible frame-delay option;
it is a target rather than a guaranteed render rate. **Refresh installed animations**
finds newly installed packages without restarting Drape.

**Animation settings…** opens the selected animation's controls from its installed
XScreenSaver metadata: numbers, checkboxes, option lists and any text or file fields.
**Apply** saves the choices and updates active playback; **Cancel** discards dialog
edits. **Restore defaults**, followed by Apply, clears that animation's custom choices.
These settings are stored separately in `~/.config/drape/xscreensaver-settings.json`
(or the XDG config equivalent), without changing the XScreenSaver daemon's configuration.
The page's frame-rate target controls frame delay. Available controls depend on the
animation's packaged metadata.

**Install animation packages…** offers `xscreensaver`
on Arch/CachyOS or `xscreensaver-data` and `xscreensaver-gl` on Debian/Ubuntu.
Extra animation packages can be installed separately through the package manager.
The confirmation shows the exact command before administrator authentication.
On Arch, missing repository databases require a separately explained full system
upgrade. Drape does not start the XScreenSaver daemon or change your screen locker.
Only the selected animation runs, inside Drape's wallpaper surface rather than the
root window. Pause freezes its owned processes; Stop also terminates its helper children.

## Audio visualizations

Choose **Audio visualization**, select a style, and enable **Desktop audio**, **Microphone**,
or both. Styles include spectrum bars, a flowing curve, radial rings, blocks, mirrored
spectrum, circular wave, layered ribbons, orbiting lights and a spectrum spiral. Desktop audio
is on by default; the microphone is off. Click **Play wallpaper** to start playback.
While a live wallpaper is running, these controls update playback automatically,
including changes to the selected audio inputs. The playback status and tray tooltip
show the active audio inputs. Changing controls while stopped does not start capture.

**Color palette** offers Single color, a custom Two-color gradient, Rainbow, Aurora,
Sunset, Fire & gold and Ocean. The strip previews your selected colors. Custom palettes
show primary and second color pickers where relevant. **Cycle colors** shifts colors
smoothly at Slow, Normal or Fast speed and works with every style and palette. Changes
apply during playback and are remembered across source switches and launches. Color
cycling and moving geometry freeze on pause or screen lock; silence leaves rendering idle.

Desktop audio uses the monitor of your default output device. Microphone uses your
default input device, and rejects an output monitor selected as the default input.
Change defaults in your system's Sound settings, then click Play again. Both inputs
combine their strongest spectrum amplitudes. This is a frequency-spectrum display;
the flowing curve is not a raw audio waveform. Silence leaves the visualizer idle.

This source requires CAVA and `pactl`, with PulseAudio or PipeWire's PulseAudio service.
**Install audio support…** shows and confirms installation of `cava` plus
`pulseaudio-utils` on Debian/Ubuntu, or `cava` plus `libpulse` on Arch/CachyOS.
It does not replace or reconfigure the sound server. One capture per enabled input
serves every monitor; Drape receives only spectrum levels, never saves audio, and
closes its capture processes on Stop or Quit. Pause and screen lock freeze capture
processing and rendering while preserving a manual pause.

Source, animation, frame-rate target, audio style, color and input choices are saved
with palette, secondary color and cycling settings through locked, atomic writes in
`~/.config/drape/wallpaper-sources.json` (or the XDG
config equivalent), independently of `videos.json`. Playback still starts manually.
The visualizations are Drape's own Cairo renderers, inspired by common spectrum
styles such as [Kurve](https://github.com/luisbocanegra/kurve); they do not need KDE Plasma.

## Backend

`video_wallpapers.py` manages the library and private local controls. `video_player.py`
owns the source lifecycle independently of browsing and archive scanning.
`wallpaper_surface.py` hosts per-monitor renderers; `video_mpv.py` handles video
arguments and JSON controls, `wallpaper_xscreensaver.py` discovers and embeds installed
animations, and `wallpaper_audio.py` reads bounded CAVA frames. `wallpaper_visualizers.py`
draws spectrum styles, and `wallpaper_colors.py` supplies palettes and smooth color cycling.
`wallpaper_process.py` bounds logs and controls only worker-owned processes.

`wallpaper_desktop.py` owns desktop-specific window setup, icon layering and lock
service details. Registered hosts are Cinnamon, GNOME, MATE, Xfce and Plasma 6 on X11,
using their respective screen-lock services. A Wayland host needs compositor
integration rather than X11 window IDs; an XWayland connection does not enable it.

Cinnamon and GNOME use `video_x11.py` to place Drape's surfaces below icon-hosting
desktop windows through the window manager's EWMH interface. Stacking is verified
before revealing a decoded video. All hosts keep input regions empty, preserving
desktop clicks, selection, drag-and-drop and context menus.

MATE and Xfce isolate frame copying in `wallpaper_background.py` and the shared
X11 bridge `wallpaper_x11.py`. MATE updates Caja's existing shared pixmap and
restores its original pixels on disconnect. Xfce publishes its own pixmap through
`wallpaper_xfce_x11.py`; `native/xfdesktop_background.c` draws it behind Xfdesktop's
icons using public GTK/GDK/Cairo APIs. `wallpaper_xfce_adapter.py` caches the compiled
adapter by source revision and supervises its temporary desktop process. Its
`xfdesktop-adapter.log` is in the player's runtime directory. Removing playback's
owned properties reveals Xfdesktop's original background; the standard desktop is
restored without retaining the adapter in saved-session commands. Repaint serials
remain unchanged on pause and idle audio.

Plasma isolates X11 capture in `wallpaper_frame_server.py`, with bounded,
loopback-only HTTP transport using a private random URL. `wallpaper_plasma.py`
owns the current activity's wallpaper lease and stores an atomic restoration
record in `~/.config/drape/plasma-wallpaper.json` (or the XDG config equivalent)
before changing any screen. `plasma_wallpaper/` supplies the native Plasma 6
package; it polls frame metadata and swaps decoded images without changing
desktop containment or widgets. Paused frames and idle audio are not re-encoded.
Render windows stay offscreen. A disconnected player restores the wallpaper;
a killed presentation helper is recovered by the player, the Restore action
or the next Play. Lease ownership checks preserve external wallpaper changes.
Ownership queries pause with playback and retry temporary D-Bus timeouts rather
than treating them as wallpaper changes. Unlock resumes playback unless it was
manually paused; this applies to video, animations and audio visualizations.

The worker listens on a private Unix socket in `$XDG_RUNTIME_DIR/drape-video`
(or a private per-user temporary directory). It runs one mpv per monitor, disables
audio, scripts, external references and player input, and does not inhibit the
screensaver. Stop terminates only those child processes. Playback failures release
the video surfaces, reveal the static wallpaper and appear in the page/tray status.
Worker startup errors are logged to `player.log` in that runtime directory.
Per-monitor mpv warnings are kept in bounded `mpv-N.log` files, with one previous
attempt retained. Playback uses OpenGL and copy-mode hardware decoding to avoid
direct-frame and Vulkan interop failures at loop boundaries. Decoder threads are
limited per monitor. If a player exits or fails to start, Drape retries once with
software decoding, then once with a software video output. It keeps manual pause,
screen-lock behavior and icon layering during recovery. After all modes fail, it
releases the video windows and reports an error; it never retries indefinitely.
Compatibility playback is shown in the page status and can use more CPU.

The GTK page is in `ui/video_wallpapers.py`; application lifetime and the extensible
source controls are in `ui/wallpaper_source_controls.py`, confirmed optional installs
in `ui/optional_packages.py`, and the tray menu in `ui/tray.py`. Drape can reconnect to a surviving player after a GUI
crash, without starting duplicate players.
