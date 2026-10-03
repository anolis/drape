"""Convert Windows cursor packs (.cur / .ani + Install.inf) into an Xcursor theme.

Many "cursor themes" on gnome-look.org are Windows packs, which Linux can't use directly.
"""

import configparser
import io
import re
import struct
from pathlib import Path

from PIL import Image

# Windows cursor roles -> the X cursor names apps ask for (first name gets the real file)
ROLE_NAMES = {
    "Arrow": [
        "left_ptr",
        "default",
        "arrow",
        "top_left_arrow",
        "left_arrow",
        "context-menu",
        "copy",
        "alias",
        "dnd-copy",
        "dnd-link",
        "dnd-none",
        "right_ptr",
        "draft_large",
    ],
    "Help": [
        "help",
        "question_arrow",
        "whats_this",
        "left_ptr_help",
        "5c6cd98b3f3ebcb1f9c7f1c204630408",
        "d9ce0ab605698f320427677b458ad60b",
    ],
    "AppStarting": [
        "progress",
        "left_ptr_watch",
        "half-busy",
        "00000000000000020006000e7e9ffc3f",
        "08e8e1c95fe2fc01f976f1e063a24ccd",
        "3ecb610c1bf2410f44200f48c40d3599",
    ],
    "Wait": ["wait", "watch"],
    "Crosshair": ["crosshair", "cross", "tcross", "cross_reverse", "diamond_cross", "cell", "plus"],
    "IBeam": ["text", "xterm", "ibeam", "vertical-text"],
    "NWPen": ["pencil", "draft"],
    "No": [
        "not-allowed",
        "no-drop",
        "crossed_circle",
        "forbidden",
        "circle",
        "dnd-no-drop",
        "03b6e0fcb3499374a867c041f52298f0",
    ],
    "SizeNS": [
        "ns-resize",
        "n-resize",
        "s-resize",
        "size_ver",
        "sb_v_double_arrow",
        "v_double_arrow",
        "top_side",
        "bottom_side",
        "row-resize",
        "split_v",
        "00008160000006810000408080010102",
    ],
    "SizeWE": [
        "ew-resize",
        "e-resize",
        "w-resize",
        "size_hor",
        "sb_h_double_arrow",
        "h_double_arrow",
        "left_side",
        "right_side",
        "col-resize",
        "split_h",
        "028006030e0e7ebffc7f7070c0600140",
    ],
    "SizeNWSE": [
        "nwse-resize",
        "nw-resize",
        "se-resize",
        "size_fdiag",
        "bd_double_arrow",
        "top_left_corner",
        "bottom_right_corner",
        "c7088f0f3e6c8088236ef8e1e3e70000",
    ],
    "SizeNESW": [
        "nesw-resize",
        "ne-resize",
        "sw-resize",
        "size_bdiag",
        "fd_double_arrow",
        "top_right_corner",
        "bottom_left_corner",
        "fcf1c3c07f0a0a1e0c080f4c0f0b0b00",
    ],
    "SizeAll": [
        "move",
        "fleur",
        "all-scroll",
        "size_all",
        "grabbing",
        "dnd-move",
        "closedhand",
        "4498f0e0c1937ffe01fd06f973665830",
        "9081237383d90e509aa00f00170e968f",
    ],
    "UpArrow": ["up_arrow", "center_ptr", "sb_up_arrow"],
    "Hand": [
        "pointer",
        "hand",
        "hand1",
        "hand2",
        "pointing_hand",
        "grab",
        "openhand",
        "e29285e634086352946a0e7090d73106",
        "9d800788f1b08800ae810202380a0822",
    ],
}
# order of files in an Install.inf [Scheme.Reg] line
SCHEME_ORDER = [
    "Arrow",
    "Help",
    "AppStarting",
    "Wait",
    "Crosshair",
    "IBeam",
    "NWPen",
    "No",
    "SizeNS",
    "SizeWE",
    "SizeNWSE",
    "SizeNESW",
    "SizeAll",
    "UpArrow",
    "Hand",
]
# filename hints for packs without a usable Install.inf; covers Windows' default names
# ("Normal Select", "Link Select", "Diagonal Resize 1", "Working in Background", ...)
NAME_HINTS = [
    ("AppStarting", r"work|progress|appstart|background|half"),
    ("Wait", r"busy|wait|watch|hourglass"),
    ("Help", r"help|question"),
    ("IBeam", r"text|ibeam|beam"),
    ("NWPen", r"pen|handwrit|draw"),
    ("No", r"unavail|not.?allowed|no\b|forbid|block"),
    ("SizeNWSE", r"nwse|dgn1|diag\D*1|size.?fdiag"),
    ("SizeNESW", r"nesw|dgn2|diag\D*2|size.?bdiag"),
    ("SizeNS", r"\bns\b|vert|size.?ns|sizens"),
    ("SizeWE", r"\bew\b|horz|horiz|size.?we|sizewe"),
    ("SizeAll", r"move|sizeall|size.?all"),
    ("UpArrow", r"alternate|up.?arrow|center"),
    ("Hand", r"link|hand"),
    ("Crosshair", r"cross|precision"),
    ("Arrow", r"normal|arrow|default|pointer"),
]

XC_MAGIC = b"Xcur"
XC_IMAGE = 0xFFFD0002


class ConversionError(Exception):
    pass


# Input format detection


def is_windows_cursor(p):
    return p.suffix.lower() in (".cur", ".ani")


def is_xcursor(p):
    try:
        with open(p, "rb") as f:
            return f.read(4) == XC_MAGIC
    except OSError:
        return False


# ---------------------------------------------------------------- reading Windows formats


def _read_cur(data):
    """Return [(Image RGBA, xhot, yhot)] for every size in a .cur/.ico blob."""
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    if reserved != 0 or kind not in (1, 2) or count == 0:
        raise ConversionError("not a cursor file")
    frames = []
    for i in range(count):
        w, h, _cc, _r, xhot, yhot, size, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        blob = data[offset : offset + size]
        if blob[:8] == b"\x89PNG\r\n\x1a\n":
            img = Image.open(io.BytesIO(blob))
        else:
            # wrap the DIB in a one-entry .ico so Pillow decodes the colour + AND mask for us
            ico = (
                struct.pack("<HHH", 0, 1, 1)
                + struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, size, 22)
                + blob
            )
            img = Image.open(io.BytesIO(ico))
        img = img.convert("RGBA")
        if kind == 1:
            xhot = yhot = 0  # plain icons have no hotspot
        frames.append((img, xhot, yhot))
    return frames


def _read_ani(data):
    """Return a list of animation steps [(frames_for_step, delay_ms)]."""
    if data[:4] != b"RIFF" or data[8:12] != b"ACON":
        raise ConversionError("not an animated cursor")
    icons, rates, seq, jif = [], None, None, 6

    def walk(buf):
        nonlocal rates, seq, jif
        pos = 0
        while pos + 8 <= len(buf):
            cid, size = buf[pos : pos + 4], struct.unpack_from("<I", buf, pos + 4)[0]
            body = buf[pos + 8 : pos + 8 + size]
            if cid == b"anih" and len(body) >= 36:
                jif = struct.unpack_from("<I", body, 28)[0] or 6
            elif cid == b"rate":
                rates = list(struct.unpack_from(f"<{size // 4}I", body))
            elif cid == b"seq ":
                seq = list(struct.unpack_from(f"<{size // 4}I", body))
            elif cid == b"LIST":
                walk(body[4:])
            elif cid == b"icon":
                icons.append(_read_cur(body))
            pos += 8 + size + (size & 1)

    walk(data[12:])
    if not icons:
        raise ConversionError("animated cursor has no frames")
    order = seq or list(range(len(icons)))
    steps = []
    for i, frame_index in enumerate(order):
        rate = rates[i] if rates and i < len(rates) else jif
        steps.append((icons[frame_index % len(icons)], max(1, round(rate * 1000 / 60))))
    return steps


# ---------------------------------------------------------------- writing Xcursor


def _pixels(img):
    """RGBA image -> premultiplied ARGB little-endian words, as Xcursor wants."""
    out = bytearray()
    for r, g, b, a in img.getdata():
        out += struct.pack(
            "<I", (a << 24) | ((r * a // 255) << 16) | ((g * a // 255) << 8) | (b * a // 255)
        )
    return bytes(out)


def _write_xcursor(path, images):
    """images: [(Image, xhot, yhot, delay_ms)]"""
    header, toc_entry, chunk_header = 16, 12, 36
    pos = header + toc_entry * len(images)
    toc, chunks = b"", b""
    for img, xhot, yhot, delay in images:
        w, h = img.size
        nominal = max(w, h)
        toc += struct.pack("<III", XC_IMAGE, nominal, pos)
        chunk = struct.pack(
            "<IIIIIIIII",
            chunk_header,
            XC_IMAGE,
            nominal,
            1,
            w,
            h,
            min(xhot, w - 1),
            min(yhot, h - 1),
            delay,
        ) + _pixels(img)
        chunks += chunk
        pos += len(chunk)
    Path(path).write_bytes(
        struct.pack("<4sIII", XC_MAGIC, header, 0x10000, len(images)) + toc + chunks
    )


def convert_file(src, dest):
    data = Path(src).read_bytes()
    if data[:4] == b"RIFF":
        images = [(img, x, y, delay) for frames, delay in _read_ani(data) for img, x, y in frames]
    else:
        images = [(img, x, y, 0) for img, x, y in _read_cur(data)]
    _write_xcursor(dest, images)


# ---------------------------------------------------------------- role mapping


def _parse_inf(inf):
    """Return {role: filename} from an Install.inf, or {}."""
    text = inf.read_text(errors="replace")
    cp = configparser.ConfigParser(
        interpolation=None,
        strict=False,
        allow_no_value=True,
        delimiters=("=",),
        comment_prefixes=(";",),
    )
    cp.optionxform = str
    try:
        cp.read_string(text)
    except configparser.Error:
        return {}
    strings = (
        {k.lower(): v.strip().strip('"') for k, v in cp["Strings"].items()}
        if cp.has_section("Strings")
        else {}
    )

    def resolve(s):
        s = re.sub(r"%(\w+)%", lambda m: strings.get(m.group(1).lower(), m.group(0)), s)
        return s.strip().strip('"').replace("\\", "/").rsplit("/", 1)[-1]

    roles = {}
    # explicit lines: HKCU,"Control Panel\Cursors",Arrow,0x00020000,"%10%\%CUR_DIR%\%pointer%"
    for m in re.finditer(
        r'^\s*HKCU\s*,\s*"Control Panel\\Cursors"\s*,\s*"?(\w+)"?\s*,[^,]*,\s*"([^"]+)"',
        text,
        re.M | re.I,
    ):
        role = next((r for r in ROLE_NAMES if r.lower() == m.group(1).lower()), None)
        if role:
            roles.setdefault(role, resolve(m.group(2)))
    # scheme line: files in SCHEME_ORDER
    m = re.search(r'"Control Panel\\Cursors\\Schemes"\s*,[^,]*,[^,]*,\s*"([^"]+)"', text, re.I)
    if m:
        for role, entry in zip(SCHEME_ORDER, m.group(1).split(",")):
            f = resolve(entry)
            if f and "%" not in f:
                roles.setdefault(role, f)
    return roles


def map_roles(folder):
    """Return {role: Path} for the Windows cursors in `folder`."""
    files = {p.name.lower(): p for p in folder.iterdir() if is_windows_cursor(p)}
    roles = {}
    for inf in folder.glob("*.inf"):
        for role, name in _parse_inf(inf).items():
            if name.lower() in files:
                roles.setdefault(role, files[name.lower()])
    for role, pattern in NAME_HINTS:
        if role in roles:
            continue
        for name, p in sorted(files.items()):
            if p not in roles.values() and re.search(pattern, Path(name).stem, re.I):
                roles[role] = p
                break
    return roles


# Theme conversion and metadata


def convert_theme(src_folder, dest, name):
    """Build an Xcursor theme at `dest` from a folder of Windows cursors. Returns roles converted."""
    roles = map_roles(src_folder)
    if "Arrow" not in roles:
        raise ConversionError(
            "Couldn't tell which file is the normal pointer in this Windows cursor pack."
        )
    cursors = dest / "cursors"
    cursors.mkdir(parents=True)
    done = []
    for role, src in roles.items():
        names = ROLE_NAMES[role]
        try:
            convert_file(src, cursors / names[0])
        except (ConversionError, OSError, ValueError, struct.error):
            continue  # skip one unreadable cursor rather than the whole theme
        for alias in names[1:]:
            (cursors / alias).symlink_to(names[0])
        done.append(role)
    if "Arrow" not in done:
        raise ConversionError("The normal pointer in this Windows cursor pack couldn't be read.")
    # anything the pack doesn't provide falls back to Adwaita instead of X's ugly defaults
    (dest / "index.theme").write_text(
        f"[Icon Theme]\nName={name}\nComment=Converted from a Windows cursor pack by drape\nInherits=Adwaita\n"
    )
    return done
