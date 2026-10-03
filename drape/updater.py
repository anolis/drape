"""Update official Git checkouts, with explicit consent handled by the UI."""

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent.parent
OFFICIAL_REMOTES = {
    "https://github.com/anolis/drape",
    "https://github.com/anolis/drape.git",
    "git@github.com:anolis/drape.git",
    "ssh://git@github.com/anolis/drape.git",
}


class UpdateError(Exception):
    pass


@dataclass(frozen=True)
class Update:
    head: str
    target: str
    count: int
    summary: str
    blocked: str = ""


# Bounded Git execution


def git(root, *args):
    env = dict(
        os.environ,
        GIT_TERMINAL_PROMPT="0",
        GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=10",
    )
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            timeout=45,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError(str(exc)) from exc
    if proc.returncode:
        raise UpdateError(proc.stderr.strip() or "Git could not complete the update check.")
    return proc.stdout.strip()


# Checkout eligibility and update discovery


def supported(root):
    if not shutil.which("git") or not (root / ".git").exists():
        return False
    return (
        git(root, "rev-parse", "--show-toplevel") == str(root.resolve())
        and git(root, "remote", "get-url", "origin") in OFFICIAL_REMOTES
        and git(root, "branch", "--show-current") == "main"
        and git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
        == "origin/main"
    )


def check(root=ROOT):
    """None means current; unsupported installs need manual updates."""
    if not supported(root):
        raise UpdateError(
            "Automatic updates require an official Drape Git checkout on main, tracking origin/main."
        )
    git(root, "fetch", "--quiet", "origin", "refs/heads/main:refs/remotes/origin/main")
    head = git(root, "rev-parse", "HEAD")
    target = git(root, "rev-parse", "origin/main")
    count = int(git(root, "rev-list", "--count", "HEAD..origin/main"))
    if not count:
        return None
    blocked = ""
    if git(root, "status", "--porcelain", "--untracked-files=all"):
        blocked = "This checkout has local changes. Save or commit them before updating."
    elif git(root, "rev-list", "--count", "origin/main..HEAD") != "0":
        blocked = "This checkout has local commits. Update it manually to keep your work."
    summary = git(root, "log", "-5", "--format=%h %s", "HEAD..origin/main")
    return Update(head, target, count, summary, blocked)


# Revalidation after user consent


def apply(update, root=ROOT):
    # Recheck after consent, so changes made while the prompt was open are protected.
    if not supported(root):
        raise UpdateError("The checkout's branch or remote changed. Check for updates again.")
    if update.blocked or git(root, "status", "--porcelain", "--untracked-files=all"):
        raise UpdateError("This checkout has local changes or commits. Update it manually.")
    if git(root, "rev-parse", "HEAD") != update.head:
        raise UpdateError("The checkout changed since the update check. Check for updates again.")
    # Merge the exact reviewed commit, without resetting files or creating a merge commit.
    git(root, "merge", "--ff-only", update.target)
    if git(root, "rev-parse", "HEAD") != update.target:
        raise UpdateError("Git did not reach the expected update revision.")
