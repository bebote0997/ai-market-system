"""Best-effort owner-only permissions for the app's own SQLite files. Stdlib only; never raises.

Only FILES are tightened (db, ``-wal``, ``-shm``, ``-journal``; symlinks skipped). The parent directory gets ``0o700``
only when the caller created it itself (``private_state_dir`` returned True): an existing directory is never chmod-ed.
On platforms without POSIX permissions (Windows) the calls are harmless no-ops.
"""
import os
from pathlib import Path
import stat

FILE_MODE = 0o600
DIR_MODE = 0o700
SQLITE_SUFFIXES = ("", "-wal", "-shm", "-journal")


def _chmod_if_needed(path, mode, *, directory=False):
    """Descriptor-based chmod: open WITHOUT following symlinks, validate the file type on the descriptor, then
    ``fchmod`` it (no pathname race between the check and the change)."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return False  # missing, a symlink (ELOOP) or not openable: leave it alone
    try:
        info = os.fstat(fd)
        wanted_type = stat.S_ISDIR if directory else stat.S_ISREG
        if not wanted_type(info.st_mode) or stat.S_IMODE(info.st_mode) == mode:
            return False
        os.fchmod(fd, mode)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def private_state_dir(directory):
    """``mkdir -p`` + ``0o700`` when the directory did not exist. True only when THIS call created it."""
    directory = Path(directory)
    if directory.exists():
        return False
    try:
        directory.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        return False  # created concurrently by someone else: leave it alone
    _chmod_if_needed(directory, DIR_MODE, directory=True)
    return True


def secure_sqlite_files(path, *, created_dir=False):
    """``0o600`` on ``path`` and its existing SQLite side files; ``0o700`` on the parent only if ``created_dir``.
    Returns the paths changed. Never raises."""
    changed = []
    try:
        path = Path(path)
        if os.name != "posix":
            return changed
        for suffix in SQLITE_SUFFIXES:
            target = Path(f"{path}{suffix}")
            if _chmod_if_needed(target, FILE_MODE):
                changed.append(target)
        if created_dir and _chmod_if_needed(path.parent, DIR_MODE, directory=True):
            changed.append(path.parent)
    except Exception:  # noqa: BLE001 - permissions hardening must never break the caller
        pass
    return changed
