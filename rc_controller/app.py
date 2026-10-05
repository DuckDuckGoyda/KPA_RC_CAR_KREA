"""Application entry point."""

from __future__ import annotations

import sys
import traceback
from datetime import datetime

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox

from . import __version__
from .paths import ensure_user_dirs, help_dir, logs_dir, macros_dir, profiles_dir

SMOKE_TEST_FLAG = "--smoke-test"
SMOKE_TEST_TIMEOUT_MS = 15_000


def _install_excepthook(interactive: bool) -> None:
    """Report unexpected errors instead of letting PyQt abort the whole app.

    The windowed exe has no console, so the traceback also goes to a file.
    """

    def hook(exc_type, exc, tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            directory = logs_dir()
            directory.mkdir(parents=True, exist_ok=True)
            with (directory / "errors.log").open("a", encoding="utf-8") as fh:
                fh.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} · {__version__}\n{text}")
        except OSError:
            pass
        if sys.stderr is not None:
            sys.stderr.write(text)
        app = QApplication.instance()
        if not interactive:
            if app is not None:
                app.exit(1)
            return
        if app is not None:
            QMessageBox.critical(
                None,
                "Непредвиденная ошибка",
                f"{exc_type.__name__}: {exc}\n\nПодробности записаны в {logs_dir() / 'errors.log'}",
            )

    sys.excepthook = hook


def _run_smoke_test(app: QApplication, window) -> None:
    """Used by CI on the built exe: resources present, serial stack works, echo recognised."""
    from .runtime.link import LinkState

    def fail(reason: str) -> None:
        raise RuntimeError(f"smoke test failed: {reason}")

    def check_files() -> None:
        if not list(help_dir().glob("*.md")):
            fail(f"no help pages in {help_dir()}")
        if not (profiles_dir() / "default.toml").is_file():
            fail(f"default profile not seeded into {profiles_dir()}")
        if not list(macros_dir().glob("*.txt")):
            fail(f"example macros not seeded into {macros_dir()}")

    def on_state(state: LinkState) -> None:
        if state == LinkState.OPEN:
            window.link.send_command("GET_TEL")
        elif state == LinkState.OK:
            window.transport.close()
            app.exit(0)

    check_files()
    window.link.state_changed.connect(on_state)
    window.transport.open_failed.connect(lambda message: fail(f"loop:// did not open: {message}"))
    window.transport.open("loop://", window.profile.link.baudrate)
    QTimer.singleShot(SMOKE_TEST_TIMEOUT_MS, lambda: fail("no echo from loop://"))


def main() -> int:
    smoke_test = SMOKE_TEST_FLAG in sys.argv
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("RC Controller")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("KREA")
    _install_excepthook(interactive=not smoke_test)
    ensure_user_dirs()

    from .gui.main_window import MainWindow

    window = MainWindow()
    window.show()
    if smoke_test:
        QTimer.singleShot(0, lambda: _run_smoke_test(app, window))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
