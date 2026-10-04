"""Atomic text settings shared by session appearance backends."""

import os
import pwd
import re
import tempfile
from pathlib import Path


def login_profiles(home):
    """Display managers use different startup files for sh, bash and zsh sessions.

    Do not create a bash_profile: its presence would stop bash reading .profile.
    Debian Xsession reads .xsessionrc; other display managers read .xprofile.
    """
    paths = [home / ".profile", home / ".xprofile", home / ".xsessionrc"]
    shell = Path(pwd.getpwuid(os.getuid()).pw_shell).name
    if shell == "zsh":
        zhome = Path(os.environ.get("ZDOTDIR") or home)
        paths.append(zhome / ".zprofile")
    elif shell == "bash":
        paths.extend(
            path for path in (home / ".bash_profile", home / ".bash_login") if path.exists()
        )
    return list(dict.fromkeys(paths))


def profile_block(text, marker, body=None):
    pattern = rf"(?m)^# BEGIN {re.escape(marker)}\n.*?^# END {re.escape(marker)}\n?"
    text = re.sub(pattern, "", text, flags=re.DOTALL)
    if body is None:
        return text
    return text.rstrip("\n") + f"\n\n# BEGIN {marker}\n{body}\n# END {marker}\n"


def atomic_text(path, text):
    """Keep the original file readable if a write fails halfway through."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".drape-", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, path.stat().st_mode & 0o777 if path.exists() else 0o600)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
