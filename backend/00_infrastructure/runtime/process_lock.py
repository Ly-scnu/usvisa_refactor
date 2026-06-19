from __future__ import annotations

from pathlib import Path
import os
import time
from typing import IO


class FileProcessLock:
    """Small cross-process lock for Windows/Linux runtime coordination.

    Python threading locks only protect one process.  The scheduler can be
    started from the API and manually from shells, so critical sections such as
    producer singleton and query-gate reserve/complete need an OS file lock.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.handle: IO[bytes] | None = None

    def acquire(self, *, blocking: bool = True, timeout: float | None = None) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        start = time.monotonic()
        while True:
            try:
                try:
                    self.handle.seek(0)
                except Exception:
                    pass
                self._lock_nonblocking()
                try:
                    self.handle.seek(0)
                    self.handle.truncate()
                    self.handle.write(f"pid={os.getpid()} acquired_at={time.time()}\n".encode("utf-8"))
                    self.handle.flush()
                except Exception:
                    pass
                return True
            except BlockingIOError:
                if not blocking:
                    self.close()
                    return False
                if timeout is not None and time.monotonic() - start >= timeout:
                    self.close()
                    return False
                time.sleep(0.05)

    def release(self) -> None:
        if not self.handle:
            return
        try:
            self._unlock()
        finally:
            self.close()

    def close(self) -> None:
        try:
            if self.handle:
                self.handle.close()
        finally:
            self.handle = None

    def __enter__(self) -> "FileProcessLock":
        self.acquire(blocking=True)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

    def _lock_nonblocking(self) -> None:
        if os.name == "nt":
            import msvcrt

            assert self.handle is not None
            try:
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise BlockingIOError(str(exc)) from exc
            return
        import fcntl

        assert self.handle is not None
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise BlockingIOError(str(exc)) from exc

    def _unlock(self) -> None:
        if os.name == "nt":
            import msvcrt

            assert self.handle is not None
            try:
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            return
        import fcntl

        assert self.handle is not None
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
