"""Lock de archivo entre procesos que anda en macOS, Linux y Windows.

En macOS/Linux usa flock (fcntl); en Windows, msvcrt.locking sobre el primer byte del archivo. Los dos
bloquean hasta conseguir el lock, así el uso es el mismo en todos lados:

    with locks.locked(path):
        ...
"""
import os
import time
from contextlib import contextmanager
from pathlib import Path

if os.name == "nt":  # pragma: no cover - se prueba en el CI de Windows
    import msvcrt

    def _acquire(f) -> None:
        f.seek(0)
        while True:
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                time.sleep(0.05)

    def _release(f) -> None:
        f.seek(0)
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:
    import fcntl

    def _acquire(f) -> None:
        fcntl.flock(f, fcntl.LOCK_EX)

    def _release(f) -> None:
        fcntl.flock(f, fcntl.LOCK_UN)


@contextmanager
def locked(path: Path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # "a+" no trunca ni falla si otro proceso lo tiene abierto; en Windows hace falta al menos 1 byte
    with open(path, "a+") as f:
        if os.name == "nt" and f.tell() == 0:
            f.write("\0")
            f.flush()
        _acquire(f)
        try:
            yield
        finally:
            _release(f)
