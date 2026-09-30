"""Main window layout, navigation, preferences and page coordination."""

from pathlib import Path
import threading

from .gtk import GLib, Gdk, Gtk
from .. import desktop, installer, pling, settings
from ..installer import system_copies
from .browse import BrowsePage, CHUNK, FIRST_CHUNKS
from .common import APP_ID, CARD_H, CARD_W, error_dialog, login_commands, run_async
from .details import DetailsDialog
from .images import ANIMATIONS, _fetch_thumb, _foreground
from .installed import InstalledPage
from .system_actions import SystemActions
from .theme_actions import ThemeActions


class Window(ThemeActions, SystemActions, Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="drape")
        # start at a comfortable size that fits the screen's usable area (small laptops too)
        width, height = 1180, 760
        display = Gdk.Display.get_default()
        monitor = display and (display.get_primary_monitor() or display.get_monitor(0))
        if monitor:
            area = monitor.get_workarea()
            width, height = min(width, area.width - 40), min(height, area.height - 40)
        self.set_default_size(width, height)
        self.set_app_icon()
        self.busy = {}  # item id -> installation progress controller

        hb = Gtk.HeaderBar(show_close_button=True, title="drape",
                           subtitle="Themes, icons, cursors & wallpapers for your desktop")
        self.set_titlebar(hb)
        self.search = Gtk.SearchEntry(placeholder_text="Search", width_chars=18)
        self.search.connect("search-changed", lambda _e: self.reload_current())
        hb.pack_end(self.search)
        self.sort_combo = Gtk.ComboBoxText()
        # Pling's "top" favours brand-new items with a handful of votes, so popularity is the default
        for key, label in [("downloads", "Most downloaded"), ("top", "Top rated"), ("new", "Newest"),
                           ("alpha", "A–Z")]:
            self.sort_combo.append(key, label)
        self.sort_combo.set_active_id("downloads")
        self.sort_combo.connect("changed", lambda _c: self.reload_all())
        menu = Gtk.Menu()
        only = Gtk.CheckMenuItem(label="Only show themes that work on this computer",
                                 active=settings.get("only_applicable"))
        only.connect("toggled", self._toggle_applicable)
        menu.append(only)
        self.window_check_item = Gtk.CheckMenuItem(label="Check open windows on the Window borders tab automatically",
                                                   active=settings.get("window_check") == "always")
        self.window_check_item.connect("toggled", lambda it: settings.set(
            "window_check", "always" if it.get_active() else "ask"))
        menu.append(self.window_check_item)
        menu.append(Gtk.SeparatorMenuItem())
        heading = Gtk.MenuItem(label="Animate previews", sensitive=False)
        menu.append(heading)
        group = None
        for mode, label in (("auto", "Automatically (on hover if it gets heavy)"), ("always", "Always"),
                            ("hover", "Only on hover")):
            item = Gtk.RadioMenuItem.new_with_label_from_widget(group, label)
            group = group or item
            item.set_active(settings.get("animations") == mode)
            item.connect("toggled", lambda it, m=mode: it.get_active() and self._set_animations(m))
            menu.append(item)
        menu.show_all()
        hb.pack_end(Gtk.MenuButton(popup=menu, image=Gtk.Image.new_from_icon_name("open-menu-symbolic",
                                                                                  Gtk.IconSize.BUTTON)))
        hb.pack_end(self.sort_combo)

        # each page asks only for its own width, so one wide page can't stop the window shrinking
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, hhomogeneous=False)
        self.pages = {}
        for k in pling.KINDS:
            p = BrowsePage(self, k.key)
            self.pages[k.key] = p
            self.stack.add_titled(p, k.key, desktop.category_label(k.key, k.label))
        self.installed = InstalledPage(self, [k for k in pling.KINDS if k.key in self.pages])
        self.stack.add_titled(self.installed, "installed", "Installed")
        from ..lockpage import LockLoginPage
        self.lockpage = LockLoginPage(self, login_commands)
        self.stack.add_titled(self.lockpage, "lock", "Lock & login")
        from ..wmpage import WindowManagerPage
        self.ccsm = None  # ccsm hosted on the Window manager page, once loaded
        self.wmpage = WindowManagerPage(self)
        self.stack.add_titled(self.wmpage, "windowmanager", "Window manager")
        from ..xfcepage import XfcePanelPage
        self.xfcepage = XfcePanelPage(self)
        self.stack.add_titled(self.xfcepage, "xfcepanel", "Xfce panel")
        self.stack.connect("notify::visible-child", lambda *_: self.on_page())

        side = self._sidebar()
        paned = Gtk.Box()
        paned.pack_start(side, False, False, 0)
        paned.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 0)
        paned.pack_start(self.stack, True, True, 0)

        self.infobar = Gtk.InfoBar(show_close_button=True, no_show_all=True)
        self.infobar.connect("response", lambda b, _r: b.hide())
        self.info_label = Gtk.Label(wrap=True, xalign=0)
        self.infobar.get_content_area().add(self.info_label)
        self.info_label.show()
        self.info_action = Gtk.Button(no_show_all=True, valign=Gtk.Align.CENTER)
        self.info_action_cb = None
        self.info_action.connect("clicked", lambda _b: self.info_action_cb and self.info_action_cb())
        self.infobar.get_content_area().pack_end(self.info_action, False, False, 0)
        self._sample = None

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.pack_start(self.infobar, False, False, 0)
        root.pack_start(paned, True, True, 0)
        self.add(root)
        self.show_all()
        self.on_page()
        ANIMATIONS.on_switch = self._animations_switched
        self._closing = threading.Event()
        self.connect("destroy", lambda *_: self._closing.set())
        GLib.timeout_add_seconds(4, self._start_prefetch)
        GLib.timeout_add_seconds(3, self._check_theme_session)


    def set_app_icon(self):
        """The drape mark: from the icon theme once installed, else straight from the repo."""
        if Gtk.IconTheme.get_default().has_icon(APP_ID):
            Gtk.Window.set_default_icon_name(APP_ID)
            self.set_icon_name(APP_ID)
            return
        svg = Path(__file__).resolve().parents[2] / "data" / f"{APP_ID}.svg"
        try:
            Gtk.Window.set_default_icon_from_file(str(svg))
            self.set_icon_from_file(str(svg))
        except GLib.Error:
            self.set_icon_name("preferences-desktop-theme")


    SETTINGS_PAGES = ("lock", "windowmanager", "xfcepanel")


    def page_visible(self, name):
        if name == "xfcepanel":
            return desktop.current_desktop() == "xfce"
        return name not in self.pages or desktop.category_visible(name, settings.get("only_applicable"))


    def __getattr__(self, name):
        """ccsm's pages call their window (widget.get_toplevel()) to switch pages; pass those to the
        ccsm hosted on the Window manager page."""
        from ..compiz import FORWARDED
        ccsm = self.__dict__.get("ccsm")
        if name in FORWARDED and ccsm is not None:
            return getattr(ccsm, name)
        raise AttributeError(name)


    def _sidebar(self):
        """Like Gtk.StackSidebar, plus a labelled divider between theme pages and settings pages."""
        lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.BROWSE)
        self._sidebar_list = lb
        lb.set_filter_func(lambda row: self.page_visible(row.page))
        lb.get_style_context().add_class("sidebar")
        rows = {}
        for child in self.stack.get_children():
            name = self.stack.child_get_property(child, "name")
            row = Gtk.ListBoxRow()
            row.page = name
            row.add(Gtk.Label(label=self.stack.child_get_property(child, "title"), xalign=0,
                              margin=6, margin_start=10, margin_end=10))
            lb.add(row)
            rows[name] = row

        def header(row, before):
            if row.page in self.SETTINGS_PAGES and (before is None or before.page not in self.SETTINGS_PAGES):
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=10)
                box.pack_start(Gtk.Separator(), False, False, 0)
                label = Gtk.Label(xalign=0, margin_start=10, margin_top=4)
                label.set_markup("<small><b>SETTINGS</b></small>")
                label.get_style_context().add_class("dim-label")
                box.pack_start(label, False, False, 0)
                box.show_all()
                row.set_header(box)
            else:
                row.set_header(None)
        lb.set_header_func(header)

        syncing = {"on": False}

        def selected(_lb, row):
            if row and not syncing["on"]:
                self.stack.set_visible_child_name(row.page)
        lb.connect("row-selected", selected)

        def follow(*_):
            # keep the highlight right when a page is opened some other way (e.g. after an install link)
            row = rows.get(self.stack.get_visible_child_name())
            if row and lb.get_selected_row() is not row:
                syncing["on"] = True
                lb.select_row(row)
                syncing["on"] = False
        self.stack.connect("notify::visible-child", follow)
        follow()

        sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        sw.set_size_request(170, -1)
        sw.add(lb)
        return sw


    def _start_prefetch(self):
        """Quietly cache the first results and previews of every tab, so opening one is instant.
        One download at a time, and paused whenever previews on screen are still loading."""
        sort = self.sort()
        plan = []
        for kind, page in self.pages.items():
            if not self.page_visible(kind):
                continue
            categories, _ = page._scope()
            if categories != "":
                plan.append((kind, categories))

        def idle_wait():
            while _foreground["pending"] > 0 and not self._closing.is_set():
                self._closing.wait(0.5)
            return not self._closing.is_set()

        def run():
            for kind, categories in plan:
                for chunk in range(FIRST_CHUNKS):
                    if not idle_wait():
                        return
                    cached = pling.cached_search(kind, "", sort, chunk, CHUNK, categories, max_age=6 * 3600)
                    try:
                        items = cached[0] if cached else pling.search(kind, "", sort, chunk, CHUNK, categories)[0]
                    except pling.PlingError:
                        return  # offline: try again next launch
                    for it in items:
                        if it.previews and idle_wait():
                            try:
                                _fetch_thumb(pling.thumb_url(it.previews[0]), CARD_W, CARD_H)
                            except Exception:  # noqa: BLE001 - best effort
                                pass
        threading.Thread(target=run, daemon=True).start()
        return False


    def sync_menu(self):
        self.window_check_item.set_active(settings.get("window_check") == "always")


    def _set_animations(self, mode):
        settings.set("animations", mode)
        ANIMATIONS.refresh()


    def _animations_switched(self, pct):
        self.notify(f"Animated previews were using about {pct:.0f}% CPU, so they now play when you hover "
                    "over them. Change this in the ☰ menu.")


    def _toggle_applicable(self, item):
        settings.set("only_applicable", item.get_active())
        self.reload_all()


    def _check_theme_session(self):
        if self._closing.is_set():
            return False
        if getattr(self, "_checking_theme_session", False):
            return True
        self._checking_theme_session = True

        def done(signature):
            self._checking_theme_session = False
            if not self._closing.is_set() and signature != getattr(self, "_theme_session", None):
                self.reload_all()
        run_async(lambda: (desktop.current_desktop(), desktop.running_wm()), done,
                  lambda _e: setattr(self, "_checking_theme_session", False))
        return True


    def query(self):
        return self.search.get_text().strip()


    def sort(self):
        return self.sort_combo.get_active_id()


    def notify(self, text, kind=Gtk.MessageType.INFO, action=None):
        """Show a message in the bar at the top; action=(label, callback) adds a button."""
        self.info_action.set_visible(action is not None)
        if action:
            self.info_action.set_label(action[0])
            self.info_action_cb = action[1]
        self.info_label.set_text(text)
        self.infobar.set_message_type(kind)
        self.infobar.show()


    def on_page(self):
        signature = (desktop.current_desktop(), desktop.running_wm())
        if signature != getattr(self, "_theme_session", None):
            self._theme_session = signature
            for page in self.pages.values():
                page.loaded = False
        sidebar = getattr(self, "_sidebar_list", None)
        state = (*signature, settings.get("only_applicable"))
        if sidebar is not None and state != getattr(self, "_sidebar_state", None):
            self._sidebar_state = state
            sidebar.invalidate_filter()
        current = self.stack.get_visible_child_name()
        if not self.page_visible(current):
            self.stack.set_visible_child_name("installed")
            return
        child = self.stack.get_visible_child()
        searchable = child not in (self.installed, self.lockpage, self.wmpage, self.xfcepage)
        self.search.set_sensitive(searchable)
        self.sort_combo.set_sensitive(searchable)
        if child is self.lockpage:
            self.lockpage.load()
        elif child is self.wmpage:
            self.wmpage.load()
        elif child is self.xfcepage:
            self.xfcepage.load()
        elif child is self.installed:
            self.installed.load()
        elif not child.loaded:
            child.load()


    def reload_current(self):
        child = self.stack.get_visible_child()
        if child in self.pages.values():
            child.load()
        for p in self.pages.values():
            if p is not child:
                p.loaded = False


    def reload_all(self):
        for p in self.pages.values():
            p.loaded = False
        self.on_page()


    def refresh_item(self, item_id=None):
        for p in self.pages.values():
            p.refresh_cards(item_id)
        if self.stack.get_visible_child() is self.installed:
            self.installed.load()


    def show_details(self, kind, item):
        DetailsDialog(self, kind, item)


    def show_details_by_id(self, kind, key):
        run_async(lambda: pling.get(key), lambda item: self.show_details(kind, item),
                  lambda e: error_dialog(self, "Couldn't load details", e))


    def show_border_sample(self):
        """A small ordinary window: the window manager draws its title bar with the window border theme."""
        if self._sample is not None:
            self._sample.present()
            return
        w = Gtk.Window(title="Window border preview", transient_for=None)
        w.set_default_size(420, 170)
        w.set_icon_name(APP_ID)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=18)
        box.pack_start(Gtk.Label(label="This window's title bar and edges come from your Window borders theme. "
                                       "Apps with a classic title bar, like Files, look like this.",
                                 wrap=True, xalign=0), True, True, 0)
        close = Gtk.Button(label="Close", halign=Gtk.Align.END)
        close.connect("clicked", lambda _b: w.destroy())
        box.pack_start(close, False, False, 0)
        w.add(box)

        def gone(*_):
            self._sample = None
        w.connect("destroy", gone)
        w.show_all()
        self._sample = w


    def go_to(self, target):
        """'Change' on an In use card: that category's installed themes if there are any, else browsing."""
        if target == "lock":
            self.stack.set_visible_child_name("lock")
            return
        grid = self.installed.grids.get(target)
        if grid and grid[2].get_children():
            self.stack.set_visible_child(self.installed)
            self.installed.tabs.set_visible_child_name(target)
        elif target in self.pages:
            self.stack.set_visible_child_name(target)


    def show_files(self, key, only=None):
        from ..filesview import FilesDialog
        entry = installer.load_manifest().get(key)
        if entry:
            FilesDialog(self, entry, system_copies(entry), only).show_all()
