"""Where things live, both when run from source and as a PyInstaller exe.

From source, profiles and macros are edited in place under
``rc_controller/resources`` (tracked by git). The exe keeps its own editable
copies next to itself (seeded from the bundled defaults on first start), or
in %APPDATA%/RC_Controller if the exe sits in a read-only folder.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
RESOURCES = PACKAGE_DIR / "resources"
APP_DIR_NAME = "RC_Controller"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".write_test"
        probe.write_text("ok", encoding="ascii")
        probe.unlink()
        return True
    except OSError:
        return False


def _compute_home() -> Path:
    if not is_frozen():
        return PACKAGE_DIR.parent
    beside_exe = Path(sys.executable).resolve().parent
    if _writable(beside_exe):
        return beside_exe
    base = Path(os.environ.get("APPDATA") or Path.home())
    return base / APP_DIR_NAME


_HOME: Path | None = None


def app_home() -> Path:
    global _HOME
    if _HOME is None:
        _HOME = _compute_home()
    return _HOME


def profiles_dir() -> Path:
    return app_home() / "profiles" if is_frozen() else RESOURCES / "profiles"


def macros_dir() -> Path:
    return app_home() / "macros" if is_frozen() else RESOURCES / "macros"


def logs_dir() -> Path:
    return app_home() / "logs"


def help_dir() -> Path:
    return RESOURCES / "help"


def settings_path() -> Path:
    return app_home() / "settings.ini"


def ensure_user_dirs() -> None:
    """Seed editable profiles/macros next to the exe; never overwrite user edits."""
    if not is_frozen():
        return
    for name in ("profiles", "macros"):
        target = app_home() / name
        target.mkdir(parents=True, exist_ok=True)
        for source in (RESOURCES / name).glob("*"):
            destination = target / source.name
            if source.is_file() and not destination.exists():
                shutil.copy2(source, destination)
