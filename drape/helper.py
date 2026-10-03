#!/usr/bin/env python3
"""Privileged helper for drape: the few things that need root.

Run through ``pkexec`` - it installs boot splashes and login screen themes into system
directories, writes login screen configuration, rebuilds the initramfs and installs greeter
packages. Standard library only, so it runs on its own outside the package.

Plymouth handling is adapted from Plymouth Configurator (github.com/anolis/plymouth-configurator).
Everything drape copies into a system directory gets a marker file; drape refuses to overwrite
or remove anything without it.
"""

import argparse
import configparser
import fcntl
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

MARKER = ".drape-installed"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+-]*$")

# kind -> system directory it installs into
DIRS = {
    "plymouth": Path("/usr/share/plymouth/themes"),
    "sddm": Path("/usr/share/sddm/themes"),
    "webgreeter": Path("/usr/share/web-greeter/themes"),
    "gtk": Path("/usr/share/themes"),
    "icons": Path("/usr/share/icons"),
    "background": Path("/usr/share/backgrounds/drape"),
}
PLYMOUTHD_CONF = Path("/etc/plymouth/plymouthd.conf")
LOCK = Path("/run/drape-helper.lock")

INITRD_TOOLS = [
    ("update-initramfs", ["update-initramfs", "-u", "-k", "all"]),
    ("mkinitcpio", ["mkinitcpio", "-P"]),
    ("dracut", ["dracut", "--regenerate-all", "--force"]),
]
# only these packages can be installed through the helper
PACKAGES = {
    "plymouth",
    "plymouth-themes",
    "lightdm-gtk-greeter",
    "slick-greeter",
    "sddm",
    "lightdm",
    # Compiz, and a minimal MATE session for it on desktops that can't host it (Cinnamon)
    "compiz",
    "compiz-mate",
    "compizconfig-settings-manager",
    "compiz-plugins",
    "compiz-plugins-extra",
    "emerald",
    "mate-session-manager",
    "mate-panel",
    "mate-settings-daemon",
    "caja",
    "marco",
}
DISPLAY_MANAGERS = {"lightdm": "/usr/sbin/lightdm", "sddm": "/usr/bin/sddm"}
GREETER_CONF = {
    "slick-greeter": (Path("/etc/lightdm/slick-greeter.conf"), "Greeter"),
    "lightdm-gtk-greeter": (Path("/etc/lightdm/lightdm-gtk-greeter.conf"), "greeter"),
}
GREETER_KEYS = {"background", "theme-name", "icon-theme-name", "cursor-theme-name"}
LIGHTDM_DROPIN = Path("/etc/lightdm/lightdm.conf.d/90-drape.conf")
SDDM_DROPIN = Path("/etc/sddm.conf.d/90-drape.conf")
WEB_GREETER_CONF = Path("/etc/lightdm/web-greeter.yml")


class HelperError(Exception):
    pass


def log(msg):
    print(msg, flush=True)


def which(name):
    for p in os.environ.get("PATH", "").split(os.pathsep) + [
        "/usr/sbin",
        "/sbin",
        "/usr/bin",
        "/bin",
    ]:
        cand = Path(p) / name
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def run(cmd, env=None, check=True):
    log("$ " + " ".join(cmd))
    proc = subprocess.run(cmd, env=env, text=True, capture_output=True)
    if proc.stdout.strip():
        log(proc.stdout.rstrip())
    if proc.stderr.strip():
        log(proc.stderr.rstrip())
    if check and proc.returncode != 0:
        raise HelperError(f"'{cmd[0]}' failed with exit code {proc.returncode}")
    return proc


# Destination validation and ownership


def validate_name(name):
    if not NAME_RE.fullmatch(name) or name in {".", ".."} or "/" in name:
        raise HelperError(f"Invalid name: {name!r}")
    return name


def target(kind, name):
    base = DIRS[kind]
    path = base / validate_name(name)
    if path.is_symlink():
        raise HelperError(f"Refusing a symlinked path: {path}")
    if path.resolve().parent != base.resolve():
        raise HelperError(f"Refusing to touch a path outside {base}: {path}")
    return path


def owned(path):
    return (path / MARKER).is_file() if path.is_dir() else Path(str(path) + MARKER).is_file()


# ---------------------------------------------------------------- safe copying (from Plymouth Configurator)


def _open_source(path):
    """Pin each directory component without following a swapped-in symlink."""
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _copy_source(fd, dest, allow_links=False):
    """Copy from pinned descriptors; reject devices, hard links and source swaps. Icon and cursor
    themes are full of relative symlinks, so those are recreated when they stay inside the theme."""
    dest.mkdir(mode=0o755)
    dest.chmod(0o755)
    for name in os.listdir(fd):
        if name in {".git", "__pycache__", MARKER}:
            continue
        before = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISLNK(before.st_mode):
            if not allow_links:
                raise HelperError(f"Theme links are not supported here: {name}")
            link = os.readlink(name, dir_fd=fd)
            if os.path.isabs(link) or ".." in Path(link).parts:
                log(f"Skipping link that points outside the theme: {name} -> {link}")
                continue
            os.symlink(link, dest / name)
            continue
        if not (stat.S_ISDIR(before.st_mode) or stat.S_ISREG(before.st_mode)):
            raise HelperError(f"Special files are not supported: {name}")
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        if stat.S_ISDIR(before.st_mode):
            flags |= os.O_DIRECTORY
        child = os.open(name, flags, dir_fd=fd)
        try:
            after = os.fstat(child)
            if (before.st_dev, before.st_ino, before.st_mode) != (
                after.st_dev,
                after.st_ino,
                after.st_mode,
            ):
                raise HelperError(f"Theme changed while being copied: {name}")
            if stat.S_ISDIR(after.st_mode):
                _copy_source(child, dest / name, allow_links)
            else:
                if after.st_nlink != 1:
                    raise HelperError(f"Hard-linked files are not supported: {name}")
                with os.fdopen(os.dup(child), "rb") as inp, (dest / name).open("xb") as out:
                    shutil.copyfileobj(inp, out)
                (dest / name).chmod(0o644)
        finally:
            os.close(child)


def _copy_file(src, dest):
    parent = _open_source(src.parent)
    try:
        fd = os.open(src.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally:
        os.close(parent)
    with os.fdopen(fd, "rb") as inp:
        info = os.fstat(inp.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise HelperError("Background must be a regular, non-linked file")
        with dest.open("xb") as out:
            shutil.copyfileobj(inp, out)
    dest.chmod(0o644)


# ---------------------------------------------------------------- plymouth (from Plymouth Configurator)


def plymouth_file_in(theme_dir):
    preferred = theme_dir / f"{theme_dir.name}.plymouth"
    if preferred.is_file():
        return preferred
    files = sorted(theme_dir.glob("*.plymouth"))
    if not files:
        raise HelperError(f"No .plymouth file in {theme_dir}")
    return files[0]


def uses_alternatives():
    default = DIRS["plymouth"] / "default.plymouth"
    return (
        which("update-alternatives") is not None
        and default.is_symlink()
        and "alternatives" in os.readlink(default)
    )


def rewrite_plymouth_file(plymouth_file, dest, source=None):
    """Relocate ImageDir/ScriptFile while preserving the staged theme's internal layout."""
    staged = plymouth_file.parent
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.read(plymouth_file, encoding="utf-8")
    if not cp.has_section("Plymouth Theme"):
        raise HelperError(f"Missing [Plymouth Theme] in {plymouth_file.name}")

    def relocate(value, key):
        path = Path(value)
        if path.is_absolute():
            roots = [
                dest,
                DIRS["plymouth"] / dest.name,
                Path("/usr/local/share/plymouth/themes") / dest.name,
            ]
            if source is not None:
                roots.insert(0, source)
            for root in roots:
                if path.is_relative_to(root):
                    path = path.relative_to(root)
                    break
            else:
                candidates = [Path(*path.parts[i:]) for i in range(1, len(path.parts))]
                if key == "ImageDir" and path.name == dest.name:
                    candidates.append(Path("."))
                path = next(
                    (
                        p
                        for p in candidates
                        if ((staged / p).is_dir() if key == "ImageDir" else (staged / p).is_file())
                    ),
                    path,
                )
                if path.is_absolute():
                    raise HelperError(f"Cannot locate {key} inside theme: {value}")
        if ".." in path.parts:
            raise HelperError(f"Path escapes theme: {value}")
        candidate = staged / path
        if not (candidate.is_dir() if key == "ImageDir" else candidate.is_file()):
            raise HelperError(f"Missing {key} in theme: {value}")
        return dest / path

    out = []
    for line in plymouth_file.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^(\s*)(ImageDir|ScriptFile)(\s*=\s*)(.*)$", line)
        if m:
            indent, key, sep, value = m.groups()
            line = f"{indent}{key}{sep}{relocate(value.strip(), key)}"
        out.append(line)
    plymouth_file.write_text("\n".join(out) + "\n", encoding="utf-8")


def active_plymouth_themes():
    names = set()
    tool = which("plymouth-set-default-theme")
    if tool:
        r = subprocess.run([tool], capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            names.add(r.stdout.strip())
    if PLYMOUTHD_CONF.exists():
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        cp.read(PLYMOUTHD_CONF)
        value = cp.get("Daemon", "Theme", fallback="").strip()
        if value:
            names.add(value)
    default = DIRS["plymouth"] / "default.plymouth"
    if default.is_symlink():
        names.add(default.resolve().parent.name)
    return names


# Boot splash installation and activation


def cmd_rebuild_initrd(_args=None):
    for name, cmd in INITRD_TOOLS:
        tool = which(name)
        if tool:
            log(f"Rebuilding initramfs with {name} (this can take a minute)…")
            run([tool] + cmd[1:])
            return
    raise HelperError("No initramfs tool found (looked for update-initramfs, mkinitcpio, dracut).")


def cmd_set_plymouth(args):
    name = validate_name(args.name)
    path = target("plymouth", name)
    plymouth_file = plymouth_file_in(path)
    tool = which("plymouth-set-default-theme")
    if tool:
        run([tool, name])
    else:
        log(f"plymouth-set-default-theme not found, writing {PLYMOUTHD_CONF}")
        _set_ini(PLYMOUTHD_CONF, "Daemon", {"Theme": name})
    if uses_alternatives():
        ua = which("update-alternatives")
        run(
            [
                ua,
                "--install",
                str(DIRS["plymouth"] / "default.plymouth"),
                "default.plymouth",
                str(plymouth_file),
                "100",
            ],
            check=False,
        )
        run([ua, "--set", "default.plymouth", str(plymouth_file)], check=False)
    log(f"Boot splash set to '{name}'.")
    if args.rebuild:
        cmd_rebuild_initrd()


# ---------------------------------------------------------------- generic install / remove


def cmd_install(args):
    kind = args.kind
    src = Path(os.path.abspath(args.source))
    name = validate_name(args.name or src.name)
    dest = target(kind, name)
    base = DIRS[kind]
    base.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not owned(dest):
        raise HelperError(
            f"{dest} already exists and wasn't installed by drape - refusing to replace it."
        )
    log(f"Installing {name} → {dest}")

    if kind == "background":
        if dest.exists():
            dest.unlink()
            Path(str(dest) + MARKER).unlink(missing_ok=True)
        _copy_file(src, dest)
        Path(str(dest) + MARKER).touch()
        return

    work = Path(tempfile.mkdtemp(prefix=".drape-", dir=base))
    try:
        staged = work / name
        fd = _open_source(src)
        try:
            _copy_source(fd, staged, allow_links=kind in ("icons", "gtk"))
        finally:
            os.close(fd)
        if kind == "plymouth":
            plymouth_file_in(staged)
            for metadata in staged.glob("*.plymouth"):
                rewrite_plymouth_file(metadata, dest, src)
        (staged / MARKER).touch()
        if dest.exists():
            dest.rename(work / "previous")
        staged.rename(dest)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if kind == "plymouth" and uses_alternatives():
        run(
            [
                which("update-alternatives"),
                "--install",
                str(base / "default.plymouth"),
                "default.plymouth",
                str(plymouth_file_in(dest)),
                "100",
            ],
            check=False,
        )
    if kind == "icons" and which("gtk-update-icon-cache"):
        run([which("gtk-update-icon-cache"), "-q", "-f", "-t", str(dest)], check=False)
    log("Installed.")


def cmd_uninstall(args):
    dest = target(args.kind, args.name)
    if not dest.exists():
        raise HelperError(f"{args.name} is not installed")
    if not owned(dest):
        raise HelperError(f"{dest} wasn't installed by drape - refusing to remove it.")
    if args.kind == "plymouth":
        if args.name in active_plymouth_themes():
            raise HelperError(f"'{args.name}' is the current boot splash; pick another one first.")
        if uses_alternatives():
            run(
                [
                    which("update-alternatives"),
                    "--remove",
                    "default.plymouth",
                    str(plymouth_file_in(dest)),
                ],
                check=False,
            )
    log(f"Removing {dest}")
    if dest.is_dir():
        shutil.rmtree(dest)
    else:
        dest.unlink()
        Path(str(dest) + MARKER).unlink(missing_ok=True)


# ---------------------------------------------------------------- configuration


def _set_ini(path, section, values):
    """Set keys in one section of an ini-style file, keeping everything else as it was."""
    lines = path.read_text().splitlines() if path.is_file() else []
    out, current, done = [], None, set()
    for line in lines:
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            if current == section:
                out += [f"{k}={v}" for k, v in values.items() if k not in done]
                done.update(values)
            current = s[1:-1]
        elif current == section:
            m = re.match(r"^\s*([\w-]+)\s*=", line)
            if m and m.group(1) in values:
                if m.group(1) not in done:
                    out.append(f"{m.group(1)}={values[m.group(1)]}")
                    done.add(m.group(1))
                continue
        out.append(line)
    if current == section:
        out += [f"{k}={v}" for k, v in values.items() if k not in done]
    elif set(values) - done:
        out += ["", f"[{section}]"] + [f"{k}={v}" for k, v in values.items() if k not in done]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".drape-tmp")
    tmp.write_text("\n".join(out).lstrip("\n") + "\n")
    tmp.chmod(0o644)
    tmp.replace(path)


def cmd_greeter_set(args):
    """Configure slick-greeter / lightdm-gtk-greeter appearance."""
    if args.greeter not in GREETER_CONF:
        raise HelperError(f"Unsupported greeter: {args.greeter}")
    values = {}
    for pair in args.values:
        key, _, value = pair.partition("=")
        if key not in GREETER_KEYS:
            raise HelperError(f"Unsupported setting: {key}")
        if key == "background":
            p = Path(value)
            if (
                not (
                    p.is_relative_to(DIRS["background"])
                    or p.is_relative_to("/usr/share/backgrounds")
                )
                or ".." in p.parts
            ):
                raise HelperError("Login screen backgrounds must be in /usr/share/backgrounds")
        elif value and not NAME_RE.fullmatch(value):
            raise HelperError(f"Invalid value for {key}: {value!r}")
        values[key] = value
    path, section = GREETER_CONF[args.greeter]
    _set_ini(path, section, values)
    log(f"Updated {path}")


def cmd_sddm_theme(args):
    name = validate_name(args.name)
    if not (DIRS["sddm"] / name).is_dir():
        raise HelperError(f"SDDM theme '{name}' is not installed")
    _set_ini(SDDM_DROPIN, "Theme", {"Current": name})
    log(f"SDDM theme set to '{name}'.")


def cmd_web_greeter_theme(args):
    name = validate_name(args.name)
    if not (DIRS["webgreeter"] / name).is_dir():
        raise HelperError(f"Web greeter theme '{name}' is not installed")
    text = WEB_GREETER_CONF.read_text() if WEB_GREETER_CONF.is_file() else "branding:\ngreeter:\n"
    if re.search(r"^(\s+)theme:.*$", text, re.M):
        text = re.sub(
            r"^(\s+)theme:.*$", lambda m: f"{m.group(1)}theme: {name}", text, count=1, flags=re.M
        )
    else:
        text = re.sub(r"^greeter:\s*$", f"greeter:\n    theme: {name}", text, count=1, flags=re.M)
    WEB_GREETER_CONF.write_text(text)
    log(f"Web greeter theme set to '{name}'.")


def cmd_use_greeter(args):
    """Make LightDM use a different greeter (e.g. lightdm-gtk-greeter)."""
    session = args.greeter
    if (
        not re.fullmatch(r"[a-z0-9-]+", session)
        or not Path(f"/usr/share/xgreeters/{session}.desktop").is_file()
    ):
        raise HelperError(f"Greeter '{session}' is not installed")
    _set_ini(LIGHTDM_DROPIN, "Seat:*", {"greeter-session": session})
    log(f"LightDM will use {session} from the next login.")


def cmd_display_manager(args):
    """Switch the system's login manager. Takes effect after a reboot."""
    dm = args.name
    if dm not in DISPLAY_MANAGERS or not Path(DISPLAY_MANAGERS[dm]).exists():
        raise HelperError(f"Display manager '{dm}' is not installed")
    Path("/etc/X11/default-display-manager").write_text(DISPLAY_MANAGERS[dm] + "\n")
    systemctl = which("systemctl")
    if systemctl:
        run([systemctl, "enable", "--force", f"{dm}.service"])
    log(f"{dm} will be the login screen after you restart.")


# Package installation and removal tracking


def cmd_apt_install(args):
    bad = [p for p in args.packages if p not in PACKAGES]
    if bad:
        raise HelperError(f"Not allowed to install: {', '.join(bad)}")
    apt = which("apt-get")
    if not apt:
        raise HelperError(
            "apt-get not found - install the packages with your distribution's tools."
        )
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
    # Drape::Install marks this in apt's history, so drape can later remove exactly what it added
    cmd = [
        apt,
        "install",
        "-y",
        "--no-install-recommends",
        "-o",
        "APT::Status-Fd=1",
        "-o",
        "Drape::Install=1",
    ] + args.packages
    log("$ " + " ".join(cmd))
    proc = subprocess.Popen(
        cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
    )
    for line in proc.stdout:
        report = apt_progress(line)
        if report:
            progress(*report)
        elif line.strip():
            log(line.rstrip())
    if proc.wait() != 0:
        raise HelperError(f"apt-get failed with exit code {proc.returncode}")
    progress(100, "Done")


APT_HISTORY = Path("/var/log/apt")


def _history_entries():
    """apt's history, oldest first, as dicts of its fields (Commandline, Install, ...)."""
    import gzip

    files = sorted(
        APT_HISTORY.glob("history.log.*.gz"), key=lambda p: int(p.name.split(".")[2]), reverse=True
    )
    files.append(APT_HISTORY / "history.log")
    for path in files:
        try:
            text = (
                gzip.open(path, "rt", errors="replace").read()
                if path.suffix == ".gz"
                else path.read_text(errors="replace")
            )
        except OSError:
            continue
        for block in text.split("\n\n"):
            entry = {}
            for line in block.splitlines():
                key, sep, value = line.partition(": ")
                if sep:
                    entry[key] = value
            if entry:
                yield entry


def _by_drape(commandline):
    """True for apt runs made by drape's helper (marked, or the unmarked form earlier versions used)."""
    if "Drape::Install=1" in commandline:
        return True
    prefix = "apt-get install -y --no-install-recommends "
    if prefix not in commandline:
        return False
    names = commandline.split(prefix, 1)[1].split()
    return bool(names) and all(n in PACKAGES for n in names)


def drape_installed_packages(of=None):
    """Packages drape's own installs added (with their dependencies), still installed. `of` limits it
    to installs that asked for any of those packages (e.g. the Compiz ones)."""
    added = []
    for entry in _history_entries():
        cmd = entry.get("Commandline", "")
        if not _by_drape(cmd):
            continue
        if of and not set(cmd.split()) & set(of):
            continue
        for item in re.findall(
            r"([a-z0-9][a-z0-9+.-]*)(?::[a-z0-9]+)? \(", entry.get("Install", "")
        ):
            if item not in added:
                added.append(item)
    if not added:
        return []
    r = subprocess.run(
        ["dpkg-query", "-W", "-f", "${Package} ${db:Status-Abbrev}\n", *added],
        capture_output=True,
        text=True,
    )
    present = {
        line.split()[0]
        for line in r.stdout.splitlines()
        if len(line.split()) > 1 and line.split()[1] == "ii"
    }
    return [p for p in added if p in present]


def removal_plan(packages):
    """(what apt would remove, anything beyond `packages` it would also remove)."""
    r = subprocess.run(
        ["apt-get", "-s", "remove", *packages],
        capture_output=True,
        text=True,
        env=dict(os.environ, LC_ALL="C"),
    )
    removed = re.findall(r"^Remv (\S+)", r.stdout, re.M)
    return removed, [p for p in removed if p not in packages]


def cmd_remove_drape_packages(args):
    """Remove packages that drape's own installs added - nothing else, and never something another
    installed package still needs."""
    allowed = set(drape_installed_packages(args.of or None))
    extra = [p for p in args.packages if p not in allowed]
    if extra:
        raise HelperError(f"drape didn't install: {', '.join(extra)}")
    if not args.packages:
        log("Nothing to remove.")
        return
    _removed, beyond = removal_plan(args.packages)
    if beyond:
        raise HelperError("Other installed software needs these packages: " + ", ".join(beyond))
    apt = which("apt-get")
    cmd = [apt, "remove", "-y", "-o", "APT::Status-Fd=1", *args.packages]
    log("$ " + " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        env=dict(os.environ, DEBIAN_FRONTEND="noninteractive"),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    for line in proc.stdout:
        report = apt_progress(line)
        if report:
            progress(report[0], report[1])
        elif line.strip():
            log(line.rstrip())
    if proc.wait() != 0:
        raise HelperError(f"apt-get failed with exit code {proc.returncode}")
    progress(100, "Done")


# Progress protocol consumed by the GUI


def apt_progress(line):
    """(overall percent, text) from one of apt's status lines, or None. Downloading fills the first
    half of the bar and installing the second."""
    m = re.match(r"^(dlstatus|pmstatus):([^:]*):([\d.]+):(.*)$", line.strip())
    if not m:
        return None
    kind, _pkg, pct, text = m.groups()
    pct = float(pct)
    overall = pct / 2 if kind == "dlstatus" else 50 + pct / 2
    return min(overall, 100.0), text.strip()


def progress(percent, text):
    """A line drape shows as a progress bar (other output is just logged)."""
    print(f"PROGRESS {percent:.1f} {text}", flush=True)


# Privileged command dispatch


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("install")
    p.add_argument("kind", choices=sorted(DIRS))
    p.add_argument("source")
    p.add_argument("--name")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("uninstall")
    p.add_argument("kind", choices=sorted(DIRS))
    p.add_argument("name")
    p.set_defaults(func=cmd_uninstall)

    p = sub.add_parser("set-plymouth")
    p.add_argument("name")
    p.add_argument("--no-rebuild", dest="rebuild", action="store_false")
    p.set_defaults(func=cmd_set_plymouth)

    sub.add_parser("rebuild-initrd").set_defaults(func=cmd_rebuild_initrd)

    p = sub.add_parser("greeter-set")
    p.add_argument("greeter")
    p.add_argument("values", nargs="+", help="key=value")
    p.set_defaults(func=cmd_greeter_set)

    p = sub.add_parser("sddm-theme")
    p.add_argument("name")
    p.set_defaults(func=cmd_sddm_theme)

    p = sub.add_parser("web-greeter-theme")
    p.add_argument("name")
    p.set_defaults(func=cmd_web_greeter_theme)

    p = sub.add_parser("use-greeter")
    p.add_argument("greeter")
    p.set_defaults(func=cmd_use_greeter)

    p = sub.add_parser("display-manager")
    p.add_argument("name")
    p.set_defaults(func=cmd_display_manager)

    p = sub.add_parser("remove-drape-packages")
    p.add_argument(
        "--of", nargs="*", default=[], help="only installs that asked for these packages"
    )
    p.add_argument("packages", nargs="*")
    p.set_defaults(func=cmd_remove_drape_packages)

    p = sub.add_parser("apt-install")
    p.add_argument("packages", nargs="+")
    p.set_defaults(func=cmd_apt_install)

    argv = sys.argv[1:] if argv is None else argv
    # "batch a ... ;; b ..." runs several commands under one password prompt
    batches = [[]]
    if argv[:1] == ["batch"]:
        for a in argv[1:]:
            if a == ";;":
                batches.append([])
            else:
                batches[-1].append(a)
    else:
        batches = [argv]
    parsed = [ap.parse_args(b) for b in batches]
    if os.geteuid() != 0:
        print("This helper must run as root (drape launches it with pkexec).", file=sys.stderr)
        return 2
    try:
        with LOCK.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            for args in parsed:
                args.func(args)
    except (
        HelperError,
        OSError,
        ValueError,
        configparser.Error,
        subprocess.SubprocessError,
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
