"""OS-level single-instance lock (auto-released if the process dies)."""
from __future__ import annotations

import os
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:  # pragma: no cover
    import fcntl


class SingleInstanceLock:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._fh = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+")
        try:
            self._fh.seek(0)
            if os.name == "nt":
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover
                fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._fh.close()
            self._fh = None
            return False
        self._fh.seek(0)
        self._fh.truncate()
        self._fh.write(str(os.getpid()))
        self._fh.flush()
        return True

    def release(self) -> None:
        if not self._fh:
            return
        try:
            self._fh.seek(0)
            if os.name == "nt":
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover
                fcntl.flock(self._fh, fcntl.LOCK_UN)
        except OSError:
            pass
        self._fh.close()
        self._fh = None

    def __enter__(self) -> "SingleInstanceLock":
        return self

    def __exit__(self, *exc) -> None:
        self.release()
