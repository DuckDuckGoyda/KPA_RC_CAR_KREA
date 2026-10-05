"""Smoke tests of the main window (offscreen), including a loopback session."""

import pytest
from PyQt6.QtCore import QEvent, QSettings, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication

from rc_controller.gui import main_window as main_window_module
from rc_controller.gui.help_window import HelpWindow, profile_markdown
from rc_controller.gui.main_window import MainWindow
from rc_controller.paths import help_dir
from rc_controller.runtime.link import LinkState


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(main_window_module, "logs_dir", lambda: tmp_path / "logs")
    monkeypatch.setattr(main_window_module, "macros_dir", lambda: tmp_path / "macros")
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    win = MainWindow(settings)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    win.activateWindow()
    win.drive_panel.setFocus()
    yield win
    if win.transport.is_open:
        win.transport.close()


def test_window_builds(window):
    assert window.tabs.count() == 4
    assert window.profile.name in window.windowTitle()
    assert window.connection_bar.baudrate() == 19200
    assert window.connection_bar.kind_badge.text() == "—"


def test_keyboard_drives_and_releases(qtbot, window):
    qtbot.keyPress(window.drive_panel, Qt.Key.Key_W)
    qtbot.waitUntil(lambda: window.drive.output.values.speed > 0)
    assert window.drive.held == frozenset({"forward"})
    qtbot.keyRelease(window.drive_panel, Qt.Key.Key_W)
    qtbot.waitUntil(lambda: window.drive.output.values.speed == 0, timeout=2000)


def test_russian_layout_key_still_drives(qtbot, window):
    def send(kind):
        # Physical W with the Russian layout active: Qt reports the key as "Ц".
        event = QKeyEvent(kind, ord("Ц"), Qt.KeyboardModifier.NoModifier, "ц")
        QApplication.sendEvent(window.drive_panel, event)

    send(QEvent.Type.KeyPress)
    assert window.drive.held == frozenset({"forward"})
    send(QEvent.Type.KeyRelease)
    assert window.drive.held == frozenset()


def test_typing_in_terminal_does_not_drive(qtbot, window):
    window.tabs.setCurrentWidget(window.terminal)
    window.terminal.input.setFocus()
    qtbot.waitUntil(lambda: window.terminal.input.hasFocus())
    qtbot.keyClicks(window.terminal.input, "wasd")
    assert window.terminal.input.text() == "wasd"
    assert window.drive.held == frozenset()
    qtbot.keyClick(window.terminal.input, Qt.Key.Key_Escape)  # Esc hands the keyboard back
    assert not window.terminal.input.hasFocus()


def test_loopback_session(qtbot, window):
    bar = window.connection_bar
    bar.port_combo.setEditText("loop://")
    qtbot.mouseClick(bar.connect_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window.link.state == LinkState.OPEN, timeout=3000)
    assert bar.kind_badge.text() == "Петля"
    assert bar.connect_button.text() == "Отключить"

    window.tabs.setCurrentWidget(window.terminal)
    window.terminal.input.setFocus()
    window.terminal.input.setText("GET_TEL")
    qtbot.keyClick(window.terminal.input, Qt.Key.Key_Return)
    # loop:// sends the packet straight back: the app must recognise its own echo.
    qtbot.waitUntil(lambda: window.link.state == LinkState.OK, timeout=3000)
    assert any(e.status == "эхо" for e in window.log_panel.model.entries)
    assert "GET_TEL" in window.terminal.output.toPlainText()

    qtbot.mouseClick(bar.connect_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window.link.state == LinkState.CLOSED, timeout=3000)
    assert window.session_log.path is not None and window.session_log.path.exists()


def test_help_pages(qtbot, profile):
    help_window = HelpWindow(help_dir(), profile)
    qtbot.addWidget(help_window)
    titles = [help_window.pages.item(i).text() for i in range(help_window.pages.count())]
    assert titles[-1] == "Команды текущего профиля"
    assert len(titles) > 1
    text = profile_markdown(profile)
    assert "`SS_SA`" in text and "0x03" in text and "CRC-8/MAXIM-DOW" in text


def test_typed_percentages_apply_and_return_to_driving(qtbot, window):
    panel = window.drive_panel
    spin = panel.throttle_sens.spin
    spin.setFocus()
    qtbot.waitUntil(spin.hasFocus)
    spin.selectAll()
    qtbot.keyClicks(spin, "150")
    assert window.drive._throttle_gain == 1.0  # nothing applied mid-typing
    qtbot.keyClick(spin, Qt.Key.Key_Return)
    assert window.drive._throttle_gain == 1.5
    assert panel.throttle_sens.slider.value() == 150
    assert window.settings.value("sensitivity/throttle") == 150
    assert panel.hasFocus()  # Enter hands the keyboard back to driving

    panel.speed_limit.slider.setValue(40)  # dragging the slider updates the box
    assert panel.speed_limit.spin.value() == 40
    assert window.drive.speed_limit == 0.4
