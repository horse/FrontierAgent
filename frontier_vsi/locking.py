from __future__ import annotations

import errno
import time
from pathlib import Path
from types import TracebackType

from .errors import ProjectLockedError
from .layout import ProjectLayout

try:
    import fcntl
except ImportError as exc:  # pragma: no cover - FrontierAgent v1 targets macOS/Linux/WSL
    fcntl = None  # type: ignore[assignment]
    _FCNTL_IMPORT_ERROR = exc
else:
    _FCNTL_IMPORT_ERROR = None


class ProjectLock:
    def __init__(self, root: Path | str, *, timeout_s: float = 10.0) -> None:
        self.layout = ProjectLayout(root)
        self.timeout_s = timeout_s
        self._handle = None

    def __enter__(self) -> ProjectLock:
        if fcntl is None:
            raise RuntimeError("FrontierVSI mutation locking requires fcntl on this platform") from _FCNTL_IMPORT_ERROR
        self.layout.locks_dir.mkdir(parents=True, exist_ok=True)
        path = self.layout.locks_dir / "mutation.lock"
        self._handle = path.open("a+b")
        deadline = time.monotonic() + self.timeout_s
        while True:
            try:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    self._handle.close()
                    self._handle = None
                    raise
                if time.monotonic() >= deadline:
                    self._handle.close()
                    self._handle = None
                    raise ProjectLockedError(f"project is locked: {self.layout.root}") from exc
                time.sleep(0.02)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del exc_type, exc, tb
        if self._handle is None:
            return
        assert fcntl is not None
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None
