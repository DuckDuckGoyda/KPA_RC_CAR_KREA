"""Main window: wires transport, link, drive loop, macros, gamepad and panels together."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QEvent, QObject, QSettings, Qt, QTimer, QUrl
from PyQt6.QtGui import QAction, QActionGroup, QCloseEvent, QDesktopServices, QKeyEvent, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..paths import RESOURCES, help_dir, logs_dir, macros_dir, profiles_dir, settings_path
from ..protocol.profile import Profile, ProfileError, load_profile
from ..runtime.drive_controller import DriveController
from ..runtime.echo_test import EchoTester
from ..runtime.gamepad import GamepadPoller, GamepadState, map_gamepad
from ..runtime.link import LinkManager, LinkState, LogEntry, NotConnectedError
from ..runtime.macro_runner import MacroRunner
from ..runtime.session_log import SessionLogger
from ..runtime.transport import SerialTransport
from .connection_bar import ConnectionBar
from .drive_panel import DrivePanel
from .echo_panel import EchoPanel
from .help_window import HelpWindow
from .keyboard import KeyMap, is_text_input
from .log_panel import LogPanel
from .macro_panel import MacroPanel
from .terminal_panel import TerminalPanel
from .widgets import app_icon

BUNDLED_PROFILE = RESOURCES / "profiles" / "default.toml"
STOP_BEFORE_CLOSE_MS = 150


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None) -> None:
        super().__init__()
        self.settings = settings or QSettings(str(settings_path()), QSettings.Format.IniFormat)
        self.setWindowIcon(app_icon())

        self.profile_path, self.profile = self._initial_profile()
        self.keymap = KeyMap(self.profile.keys.as_dict())

        # ---- runtime
        self.transport = SerialTransport(self)
        self.link = LinkManager(self.transport, self.profile, self)
        self.drive = DriveController(self.link, self.profile, self)
        self.runner = MacroRunner(self.link, self.drive, self)
        self.gamepad = GamepadPoller(parent=self)
        self.echo = EchoTester(self.transport, self.link, self)
        self.session_log = SessionLogger(logs_dir())
        self.help_window: HelpWindow | None = None
        self._gamepad_connected = False

        # ---- widgets
        self.connection_bar = ConnectionBar()
        self.drive_panel = DrivePanel()
        self.drive_panel.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.log_panel = LogPanel()
        self.terminal = TerminalPanel(self.link)
        self.macro_panel = MacroPanel(macros_dir(), self.profile)
        self.echo_panel = EchoPanel(self.echo)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.log_panel, "Журнал")
        self.tabs.addTab(self.terminal, "Терминал")
        self.tabs.addTab(self.macro_panel, "Макросы")
        self.tabs.addTab(self.echo_panel, "Эхо-тест")

        splitter = QSplitter()
        splitter.addWidget(self.drive_panel)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 900])

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(6, 0, 6, 6)
        layout.addWidget(self.connection_bar)
        layout.addWidget(splitter, 1)
        self.setCentralWidget(central)

        self.session_label = QLabel()
        self.statusBar().addPermanentWidget(self.session_label)

        self._build_menus()
        self._wire()
        self._restore_settings()
        self._apply_profile_to_widgets()

        app = QApplication.instance()
        app.installEventFilter(self)
        app.focusChanged.connect(self._on_focus_changed)
        app.applicationStateChanged.connect(self._on_app_state)

        self._start_gamepad()
        self.link.log_event(f"RC Controller {__version__}, профиль «{self.profile.name}»")
        QTimer.singleShot(0, self.drive_panel.setFocus)

    # ------------------------------------------------------------------ setup

    def _initial_profile(self) -> tuple[Path, Profile]:
        name = str(self.settings.value("profile", "default.toml"))
        candidates = [profiles_dir() / name, profiles_dir() / "default.toml"]
        for path in candidates:
            if path.is_file():
                profile = self._read_profile(path)
                if profile is not None:
                    return path, profile
        # Fall back to the bundled copy, which is covered by the tests.
        return BUNDLED_PROFILE, load_profile(BUNDLED_PROFILE)

    def _read_profile(self, path: Path) -> Profile | None:
        try:
            profile = load_profile(path)
            KeyMap(profile.keys.as_dict())
        except (ProfileError, ValueError) as exc:
            QMessageBox.critical(
                self,
                "Ошибка в профиле",
                f"Файл {path.name}:\n\n{exc}\n\nИсправьте файл и выберите «Профиль → Перезагрузить» (Ctrl+R).",
            )
            return None
        return profile

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&Файл")
        for text, directory in (
            ("Открыть папку профилей", profiles_dir),
            ("Открыть папку макросов", macros_dir),
            ("Открыть папку журналов сеансов", logs_dir),
        ):
            action = file_menu.addAction(text)
            action.triggered.connect(lambda _=False, d=directory: self._open_folder(d()))
        file_menu.addSeparator()
        quit_action = file_menu.addAction("Выход")
        quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        quit_action.triggered.connect(self.close)

        self.profile_menu = self.menuBar().addMenu("&Профиль")
        self.profile_menu.aboutToShow.connect(self._fill_profile_menu)
        self._profile_group = QActionGroup(self)

        help_menu = self.menuBar().addMenu("&Справка")
        help_action = help_menu.addAction("Справка и FAQ")
        help_action.setShortcut(QKeySequence("F1"))
        help_action.triggered.connect(lambda: self._show_help())
        about = help_menu.addAction("О программе")
        about.triggered.connect(self._about)

        # Profile reload must work even before the menu has been opened once.
        self.reload_action = QAction("Перезагрузить профиль", self)
        self.reload_action.setShortcut(QKeySequence("Ctrl+R"))
        self.reload_action.triggered.connect(self.reload_profile)
        self.addAction(self.reload_action)

    def _wire(self) -> None:
        bar = self.connection_bar
        bar.connect_requested.connect(self._connect)
        bar.disconnect_requested.connect(self._disconnect)
        bar.keepalive_toggled.connect(self.link.set_keepalive_enabled)

        self.transport.open_failed.connect(self._on_open_failed)
        self.transport.data_received.connect(self.terminal.on_rx)
        self.link.state_changed.connect(self._on_link_state)
        self.link.stats_changed.connect(bar.set_stats)
        self.link.log_entry.connect(self._on_log_entry)

        self.drive.output_changed.connect(self.drive_panel.set_output)
        self.drive.keys_changed.connect(self.drive_panel.set_keys)
        self.drive.emergency.connect(lambda: self.statusBar().showMessage("Аварийная остановка", 3000))
        self.drive_panel.stop_clicked.connect(self._emergency_stop)
        self.drive_panel.speed_limit_changed.connect(self._on_speed_limit)
        self.drive_panel.sensitivity_changed.connect(self._on_sensitivity)

        self.macro_panel.run_requested.connect(self._run_macro)
        self.macro_panel.stop_requested.connect(lambda: self.runner.abort("остановлен"))
        self.runner.started.connect(self.macro_panel.on_started)
        self.runner.started.connect(lambda name: self.link.log_event(f"макрос «{name}» запущен", "macro"))
        self.runner.step_started.connect(self.macro_panel.on_step)
        self.runner.progress.connect(self.macro_panel.on_progress)
        self.runner.finished.connect(self.macro_panel.on_finished)
        self.runner.finished.connect(
            lambda ok, message: self.link.log_event(f"макрос {message}", "macro", "ok" if ok else "warn")
        )
        self.echo_panel.finished.connect(lambda text, ok: self.link.log_event(text, "echo", "ok" if ok else "warn"))

        self.gamepad.state_changed.connect(self._on_gamepad_state)
        self.gamepad.button_pressed.connect(self._on_gamepad_button)
        self.gamepad.connection_changed.connect(self._on_gamepad_connection)

    def _restore_settings(self) -> None:
        geometry = self.settings.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(1280, 800)
        port = self.settings.value("port")
        if port:
            self.connection_bar.select_device(str(port))
        baud = self.settings.value("baudrate")
        self.connection_bar.set_baudrate(int(baud) if baud else self.profile.link.baudrate)
        self.drive_panel.set_speed_limit(int(self.settings.value("speed_limit", 100)))
        self.drive_panel.set_sensitivity(
            int(self.settings.value("sensitivity/throttle", 100)), int(self.settings.value("sensitivity/steer", 100))
        )
        self.tabs.setCurrentIndex(int(self.settings.value("tab", 0)))

    def _start_gamepad(self) -> None:
        if not self.gamepad.available:
            self.drive_panel.set_gamepad_status(False, "недоступен (нужен Windows с XInput)")
            return
        self.gamepad.start()
        self._on_gamepad_connection(False, "")

    # ------------------------------------------------------------------ profile

    def _fill_profile_menu(self) -> None:
        menu = self.profile_menu
        menu.clear()
        for action in self._profile_group.actions():
            self._profile_group.removeAction(action)
        files = sorted(profiles_dir().glob("*.toml"))
        for path in files:
            action = menu.addAction(path.stem)
            action.setCheckable(True)
            action.setChecked(path.resolve() == self.profile_path.resolve())
            action.triggered.connect(lambda _=False, p=path: self.switch_profile(p))
            self._profile_group.addAction(action)
        if not files:
            menu.addAction("(нет файлов профилей)").setEnabled(False)
        menu.addSeparator()
        menu.addAction(self.reload_action)
        edit = menu.addAction("Открыть файл профиля в редакторе")
        edit.triggered.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.profile_path))))
        folder = menu.addAction("Открыть папку профилей")
        folder.triggered.connect(lambda: self._open_folder(profiles_dir()))

    def reload_profile(self) -> None:
        self.switch_profile(self.profile_path)

    def switch_profile(self, path: Path) -> None:
        profile = self._read_profile(path)
        if profile is None:
            return
        self.runner.abort("профиль перезагружен")
        self.profile_path = path
        self.profile = profile
        self.keymap = KeyMap(profile.keys.as_dict())
        self.link.set_profile(profile)
        self.link.set_keepalive_enabled(self.connection_bar.keepalive_check.isChecked())
        self.drive.set_profile(profile)
        self.terminal.set_profile(profile)
        self.macro_panel.set_profile(profile)
        if self.help_window is not None:
            self.help_window.set_profile(profile)
        if not self.transport.is_open:
            self.connection_bar.set_baudrate(profile.link.baudrate)
        self.settings.setValue("profile", path.name)
        self._apply_profile_to_widgets()
        self.link.log_event(f"профиль загружен: «{profile.name}» ({path.name})", level="ok")
        self.statusBar().showMessage(f"Профиль «{profile.name}» загружен", 3000)

    def _apply_profile_to_widgets(self) -> None:
        self.setWindowTitle(f"RC Controller — {self.profile.name}")
        self.drive_panel.set_profile(self.profile)
        self.connection_bar.keepalive_check.blockSignals(True)
        self.connection_bar.keepalive_check.setChecked(self.link.keepalive_enabled)
        self.connection_bar.keepalive_check.setEnabled(bool(self.profile.link.keepalive_command))
        self.connection_bar.keepalive_check.blockSignals(False)
        if not self.profile.gamepad.enabled:
            self.drive.set_gamepad(0.0, 0.0)
            self.drive_panel.set_gamepad_status(False, "отключён в профиле")

    # ------------------------------------------------------------------ connection

    def _connect(self, device: str, baud: int) -> None:
        self.settings.setValue("port", device)
        self.settings.setValue("baudrate", baud)
        self.transport.open(device, baud)

    def _disconnect(self) -> None:
        if self.transport.is_connecting:
            self.transport.cancel_open()
            return
        moving = self.runner.running or not self.drive.output.values.is_zero
        self.runner.abort("порт закрыт")
        self.echo.stop("порт закрыт")
        if moving and self.transport.is_open:
            # Tell the car to stop and give the bytes a moment to leave before closing.
            self.drive.emergency_stop()
            QTimer.singleShot(STOP_BEFORE_CLOSE_MS, self.transport.close)
        else:
            self.transport.close()

    def _on_link_state(self, state: LinkState) -> None:
        self.connection_bar.set_state(state, self.transport.port)

    def _on_open_failed(self, message: str) -> None:
        if message == "подключение отменено":
            return
        hint = (
            "\n\nЕсли это Bluetooth: машинка должна быть включена, HC-05 сопряжён с компьютером, "
            "а выбран исходящий COM-порт (у входящего в списке есть пометка).\n"
            "Если порт занят — закройте другие программы, которые его используют (терминалы, IDE…)."
        )
        QMessageBox.warning(self, "Подключение", f"Не удалось открыть порт:\n{message}{hint}")

    def _on_log_entry(self, entry: LogEntry) -> None:
        self.log_panel.append(entry)
        self.session_log.write(entry)
        if self.session_log.path is not None and not self.session_label.text():
            self.session_label.setText(f"Журнал сеанса: {self.session_log.path.name}")
            self.session_label.setToolTip(str(self.session_log.path))

    # ------------------------------------------------------------------ driving

    def _emergency_stop(self) -> None:
        self.drive.emergency_stop()
        if self.echo.running:
            self.echo.stop("остановлен")

    def _on_speed_limit(self, fraction: float) -> None:
        self.drive.set_speed_limit(fraction)
        self.settings.setValue("speed_limit", round(fraction * 100))

    def _on_sensitivity(self, throttle: float, steer: float) -> None:
        self.drive.set_sensitivity(throttle, steer)
        self.settings.setValue("sensitivity/throttle", round(throttle * 100))
        self.settings.setValue("sensitivity/steer", round(steer * 100))

    def _run_macro(self, steps: list, name: str) -> None:
        if self.echo.running:
            QMessageBox.information(self, "Макрос", "Сначала дождитесь окончания эхо-теста.")
            return
        try:
            self.runner.run(steps, name)
        except NotConnectedError as exc:
            QMessageBox.information(self, "Макрос", str(exc).capitalize() + ".")
        except RuntimeError as exc:
            QMessageBox.information(self, "Макрос", str(exc))

    def _on_gamepad_state(self, state: GamepadState) -> None:
        if self.profile.gamepad.enabled:
            self.drive.set_gamepad(*map_gamepad(state, self.profile.gamepad))

    def _on_gamepad_button(self, name: str) -> None:
        if self.profile.gamepad.enabled and name == self.profile.gamepad.stop_button:
            self._emergency_stop()

    def _on_gamepad_connection(self, connected: bool, text: str) -> None:
        if connected != self._gamepad_connected:
            self._gamepad_connected = connected
            self.link.log_event(f"геймпад подключён ({text})" if connected else "геймпад отключён")
        if not self.profile.gamepad.enabled:
            self.drive_panel.set_gamepad_status(False, "отключён в профиле")
            return
        self.drive_panel.set_gamepad_status(connected, f"подключён ({text})" if connected else "не найден")

    # ------------------------------------------------------------------ keyboard

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        etype = event.type()
        if etype not in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            return False
        if not isinstance(obj, QWidget) or obj.window() is not self:
            return False
        focus = QApplication.focusWidget()
        if focus is not None and obj is not focus:
            return False  # a key event passes several objects on its way; handle it once
        assert isinstance(event, QKeyEvent)
        typing = is_text_input(focus)

        if event.key() == Qt.Key.Key_Escape:
            if etype == QEvent.Type.KeyPress and not event.isAutoRepeat():
                self._emergency_stop()
                if typing and focus is not None:
                    focus.clearFocus()
                    self.drive_panel.setFocus()
            return True

        action = self.keymap.action_for(event)
        if action is None:
            return False
        if etype == QEvent.Type.KeyRelease:
            if not event.isAutoRepeat():
                self.drive.release(action)
            return not typing
        if typing:
            return False
        if not event.isAutoRepeat():
            self.drive.press(action)
        return True

    def _on_focus_changed(self, old: QWidget | None, new: QWidget | None) -> None:
        typing = is_text_input(new)
        if typing:
            self.drive.release_all()
        self.drive_panel.set_keyboard_active(not typing)

    def _on_app_state(self, state: Qt.ApplicationState) -> None:
        if state != Qt.ApplicationState.ApplicationActive:
            self.drive.release_all()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow():
            self.drive.release_all()  # key releases go elsewhere once the window loses focus
        super().changeEvent(event)

    # ------------------------------------------------------------------ misc

    def _show_help(self, page: str | None = None) -> None:
        if self.help_window is None:
            self.help_window = HelpWindow(help_dir(), self.profile, self)
        self.help_window.show()
        self.help_window.raise_()
        self.help_window.activateWindow()
        if page:
            self.help_window.show_page(page)

    def _about(self) -> None:
        QMessageBox.about(
            self,
            "О программе",
            f"<b>RC Controller</b> {__version__}<br><br>"
            "Пульт управления радиоуправляемой машинкой по UART (USB или Bluetooth HC-05).<br>"
            "Курс «Конструирование РЭА».<br><br>"
            f"Профили: {profiles_dir()}<br>Макросы: {macros_dir()}<br>Журналы: {logs_dir()}",
        )

    @staticmethod
    def _open_folder(directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self.macro_panel.modified:
            answer = QMessageBox.question(
                self,
                "Несохранённый макрос",
                "Сохранить изменения в макросе перед выходом?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if answer == QMessageBox.StandardButton.Save and not self.macro_panel.save():
                event.ignore()
                return
        self.runner.abort("программа закрыта")
        self.echo.stop("программа закрыта")
        if self.transport.is_open:
            if not self.drive.output.values.is_zero:
                self.drive.emergency_stop()
                QApplication.processEvents()
            self.transport.close()
        self.gamepad.stop()
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("tab", self.tabs.currentIndex())
        self.settings.sync()
        self.session_log.close()
        QApplication.instance().removeEventFilter(self)
        super().closeEvent(event)
