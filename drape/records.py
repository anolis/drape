"""Locked installation records with durable atomic writes and a previous-version backup."""

import fcntl
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path


class InstallError(Exception):
    """An installation or record update could not be completed safely."""


_mutexes = {}
_mutex_guard = threading.Lock()
_held = threading.local()


@contextmanager
def file_lock(path):
    """Serialize threads and processes; nested operations in one thread are reentrant."""
    path = Path(path)
    key = str(path.resolve())
    with _mutex_guard:
        mutex = _mutexes.setdefault(key, threading.RLock())
    with mutex:
        held = getattr(_held, "paths", set())
        _held.paths = held
        if key in held:
            yield
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            lock = path.open("a+b")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX)
            except OSError:
                lock.close()
                raise
        except OSError as exc:
            raise InstallError(f"Could not lock installation records: {exc}") from exc
        with lock:
            held.add(key)
            try:
                yield
            finally:
                held.remove(key)
                fcntl.flock(lock, fcntl.LOCK_UN)


def _validate(data):
    if not isinstance(data, dict) or any(not isinstance(entry, dict) for entry in data.values()):
        raise ValueError("expected an object containing installation records")
    for entry in data.values():
        if not isinstance(entry.get("paths"), list) or any(not isinstance(p, str) for p in entry["paths"]):
            raise ValueError("invalid installation paths")
        if not isinstance(entry.get("components"), list):
            raise ValueError("invalid installation components")
    return data


def _atomic_write(path, text):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class ManifestStore:
    def __init__(self, path):
        self.path = Path(path)
        self.backup = self.path.with_suffix(self.path.suffix + ".bak")

    def load(self):
        try:
            return _validate(json.loads(self.path.read_text()))
        except FileNotFoundError:
            if self.backup.exists():
                raise InstallError(f"Installation records are missing at {self.path}. "
                                   f"A backup exists at {self.backup}; restore it before continuing.")
            return {}
        except (OSError, ValueError) as exc:
            backup = f" A previous-version backup is at {self.backup}." if self.backup.exists() else ""
            raise InstallError(f"Cannot read installation records at {self.path}: {exc}. "
                               f"The records have not been reset or overwritten.{backup}") from exc

    def _save(self, data):
        try:
            _validate(data)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Validate the existing file before touching either it or its backup.
            self.load()
            if self.path.exists():
                _atomic_write(self.backup, self.path.read_text())
            _atomic_write(self.path, json.dumps(data, indent=2, sort_keys=True))
        except (OSError, ValueError) as exc:
            raise InstallError(f"Could not save installation records at {self.path}: {exc}") from exc

    def save(self, data):
        """Explicit replacement, for seeding/importing records. Use edit() for mutations."""
        with file_lock(self.path.with_suffix(".lock")):
            self._save(data)

    @contextmanager
    def edit(self):
        """Read the latest records under the write lock and commit only successful edits."""
        with file_lock(self.path.with_suffix(".lock")):
            data = self.load()
            yield data
            self._save(data)

    def operations(self):
        """Serialize file installation/removal; metadata edits use a separate brief lock."""
        return file_lock(self.path.with_suffix(".operations.lock"))
