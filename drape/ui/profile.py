"""Public uploader profiles with ordinary catalog cards and an expandable view."""

from urllib.parse import quote
from weakref import WeakSet

from .. import pling
from .common import run_async
from .gtk import Gtk
from .images import load_image
from .card_transitions import CardFlow


class ProfileView(Gtk.Box):
    def __init__(self, window, author, kind):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=16)
        self.win, self.author, self.kind = window, author, kind
        self.page, self.count, self.busy, self.closed = 0, 0, False, False
        if not hasattr(window, "_profiles"):
            window._profiles = WeakSet()
        window._profiles.add(self)
        self.connect("destroy", lambda *_: setattr(self, "closed", True))
        self.connect("destroy", lambda *_: window._profiles.discard(self))
        header = Gtk.Box(spacing=12)
        self.avatar = Gtk.Image()
        header.pack_start(self.avatar, False, False, 0)
        self.bio = Gtk.Label(label="Loading public profile…", wrap=True, xalign=0, selectable=True)
        header.pack_start(self.bio, True, True, 0)
        self.expand = Gtk.Button.new_from_icon_name("view-fullscreen-symbolic", Gtk.IconSize.BUTTON)
        self.expand.set_tooltip_text("Expand profile in the main window")
        header.pack_end(self.expand, False, False, 0)
        self.pack_start(header, False, False, 0)
        self.pack_start(
            Gtk.LinkButton.new_with_label(
                f"https://www.pling.com/u/{quote(author, safe='')}", "View profile on Pling"
            ),
            False,
            False,
            0,
        )
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.flow = CardFlow(
            selection_mode=Gtk.SelectionMode.NONE,
            homogeneous=True,
            valign=Gtk.Align.START,
            max_children_per_line=8,
            row_spacing=6,
            column_spacing=6,
        )
        self.scroller.add(self.flow)
        self.pack_start(self.scroller, True, True, 0)
        footer = Gtk.Box(spacing=12)
        self.status = Gtk.Label(xalign=0, wrap=True)
        footer.pack_start(self.status, True, True, 0)
        self.all_button = Gtk.Button(label="All uploads")
        self.all_button.connect("clicked", lambda *_: self.more())
        footer.pack_end(self.all_button, False, False, 0)
        self.pack_start(footer, False, False, 0)
        self.show_all()
        run_async(lambda: pling.profile(author), self.loaded_profile, self.failed_profile)

    # Public profile fields and paginated catalog cards

    def loaded_profile(self, data):
        if self.closed:
            return
        name = " ".join(str(data.get(k) or "") for k in ("firstname", "lastname")).strip()
        location = ", ".join(str(data.get(k)) for k in ("city", "country") if data.get(k))
        self.bio.set_text(
            "\n".join(
                filter(
                    None,
                    [
                        name or self.author,
                        location,
                        str(data.get("description") or "No public biography provided."),
                    ],
                )
            )
        )
        avatar = data.get("avatarpic") or ""
        if avatar.startswith(("https://", "http://")):
            load_image(avatar, self.avatar, 80, 80, alive=lambda: not self.closed)
        homepage = data.get("homepage") or ""
        if homepage.startswith(("https://", "http://")):
            self.pack_start(
                Gtk.LinkButton.new_with_label(homepage, "Uploader website"), False, False, 0
            )
            self.show_all()
        self.more()

    def failed_profile(self, error):
        if not self.closed:
            self.bio.set_text(f"Public profile unavailable: {error}")
            self.more()

    def more(self):
        if self.closed or self.busy:
            return
        self.busy = True
        self.all_button.set_sensitive(False)
        self.status.set_text("Loading uploads…")
        run_async(
            lambda: pling.uploads(self.author, self.page), self.got_uploads, self.failed_uploads
        )

    def got_uploads(self, result):
        if self.closed:
            return
        from .browse import Card

        items, total = result
        self.busy = False
        self.page += 1
        existing = {card.item.id for card in self.flow.cards()}
        for item in items:
            if item.id not in existing:
                self.flow.add(Card(self.win, pling.item_kind(item), item))
                existing.add(item.id)
                self.count += 1
        self.flow.show_all()
        self.status.set_text(
            f"{self.count} of {total} uploads" if items else "No more public uploads."
        )
        self.all_button.set_label("Load more uploads")
        self.all_button.set_sensitive(bool(items) and self.count < total)

    def failed_uploads(self, error):
        if not self.closed:
            self.busy = False
            self.status.set_text(f"Could not load uploads: {error}")
            self.all_button.set_label("Retry uploads")
            self.all_button.set_sensitive(True)


class ProfileDialog(Gtk.Dialog):
    def __init__(self, window, author, kind):
        super().__init__(title=author, transient_for=window, modal=True, use_header_bar=True)
        self.set_default_size(900, 650)
        self.win = window
        self.view = ProfileView(window, author, kind)
        self.get_content_area().pack_start(self.view, True, True, 0)
        self.add_button("Close", Gtk.ResponseType.CLOSE)
        self.connect("response", lambda *_: self.destroy())
        self.view.expand.set_visible(isinstance(getattr(window, "stack", None), Gtk.Stack))
        self.view.expand.set_no_show_all(not isinstance(getattr(window, "stack", None), Gtk.Stack))
        self.view.expand.connect("clicked", lambda *_: self.expand())
        self.show_all()

    # Reparent the same live content so previews and pagination survive expansion

    def expand(self):
        stack = self.win.stack
        previous = stack.get_visible_child()
        self.get_content_area().remove(self.view)
        stack.add_named(self.view, f"profile:{id(self.view)}")
        self.view.expand.set_image(
            Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.BUTTON)
        )
        self.view.expand.set_tooltip_text("Close profile and return to the previous section")
        self.view.expand.destroy()
        close = Gtk.Button.new_from_icon_name("window-close-symbolic", Gtk.IconSize.BUTTON)
        close.set_tooltip_text("Close profile and return to the previous section")
        header = self.view.get_children()[0]
        header.pack_end(close, False, False, 0)

        def collapse(_button):
            if previous.get_parent() is stack:
                stack.set_visible_child(previous)
            stack.remove(self.view)
            self.view.destroy()

        close.connect("clicked", collapse)
        close.show()
        stack.set_visible_child(self.view)
        self.destroy()


def author_link(label, window, author, kind):
    """Intercept internal author links so usernames open a modal."""

    def activate(_label, uri):
        if uri == "drape:author":
            ProfileDialog(window, author, kind)
            return True
        return False

    label.connect("activate-link", activate)
