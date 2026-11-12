"""Cooperative, process-safe exclusion for display writes."""

from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

_local = threading.RLock()
_held: dict[str, tuple[int, object]] = {}


class DisplayBusy(RuntimeError):
    pass


class DisplayLock:
    """Reentrant on one thread; all other threads and processes fail promptly."""

    def __init__(self, identity: str) -> None:
        self.key = hashlib.sha256(identity.encode()).hexdigest()
        self.owner = threading.get_ident()
        self.stream = None
        self.nested = False

    def __enter__(self):
        import fcntl
        with _local:
            held = _held.get(self.key)
            if held:
                if held[0] != self.owner:
                    raise DisplayBusy("This display is being calibrated")
                self.nested = True
                return self
            preferred = Path(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/lumen-{os.getuid()}")) / "lumen-locks"
            fallback = Path(f"/tmp/lumen-{os.getuid()}") / "lumen-locks"
            for directory in dict.fromkeys((preferred, fallback)):
                try:
                    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                    if directory.is_symlink() or directory.stat().st_uid != os.getuid():
                        raise DisplayBusy("Display lock directory is not owned by this user")
                    descriptor = os.open(directory / self.key, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                    break
                except OSError:
                    if directory == fallback:
                        raise
            self.stream = os.fdopen(descriptor, "a")
            try:
                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self.stream.close()
                raise DisplayBusy("This display is being calibrated") from None
            _held[self.key] = self.owner, self.stream
        return self

    def __exit__(self, *_):
        import fcntl
        if self.nested:
            return
        with _local:
            if self.stream is not None:
                _held.pop(self.key, None)
                fcntl.flock(self.stream, fcntl.LOCK_UN)
                self.stream.close()


def display_lock(output: str | None, monitor_id: str = "") -> DisplayLock:
    return DisplayLock(output or monitor_id)
