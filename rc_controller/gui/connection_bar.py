"""Top bar: port choice, baud rate, connect button and link indicators."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIntValidator
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QWidget,
)

from ..ports import KIND_BLUETOOTH, KIND_LABELS, KIND_LOOP, KIND_USB, PortInfo, list_ports
from ..runtime.link import LinkState, LinkStats
from .widgets import BLUE, GREEN, GREY, PURPLE, RED, TEAL, YELLOW, Badge, StatusLed

BAUD_RATES = ("9600", "19200", "38400", "57600", "115200")
KIND_COLORS = {KIND_USB: BLUE, KIND_BLUETOOTH: PURPLE, KIND_LOOP: TEAL}

STATE_VIEW = {
    LinkState.CLOSED: (GREY, "Нет подключения"),
    LinkState.CONNECTING: (YELLOW, "Подключение…"),
    LinkState.OPEN: (YELLOW, "Порт открыт, ответов пока нет"),
    LinkState.OK: (GREEN, "Связь есть"),
    LinkState.LOST: (RED, "Связь потеряна"),
}


class PortCombo(QComboBox):
    about_to_show = pyqtSignal()

    def showPopup(self) -> None:  # noqa: N802
        self.about_to_show.emit()
        super().showPopup()


class ConnectionBar(QWidget):
    connect_requested = pyqtSignal(str, int)
    disconnect_requested = pyqtSignal()
    keepalive_toggled = pyqtSignal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = LinkState.CLOSED

        self.port_combo = PortCombo()
        self.port_combo.setEditable(True)
        self.port_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.port_combo.setMinimumWidth(340)
        self.port_combo.setToolTip(
            "COM-порт машинки. Bluetooth-порты HC-05 появляются после сопряжения в Windows.\n"
            "Можно ввести вручную, например COM7 или loop:// (петля для проверки программы)."
        )
        self.port_combo.about_to_show.connect(self.refresh_ports)

        self.refresh_button = QToolButton()
        self.refresh_button.setText("⟳")
        self.refresh_button.setToolTip("Обновить список портов")
        self.refresh_button.clicked.connect(self.refresh_ports)

        self.baud_combo = QComboBox()
        self.baud_combo.setEditable(True)
        self.baud_combo.addItems(BAUD_RATES)
        self.baud_combo.setValidator(QIntValidator(300, 4_000_000, self))
        self.baud_combo.setToolTip("Скорость UART, бод (по протоколу — 19200)")

        self.connect_button = QPushButton("Подключить")
        self.connect_button.setMinimumWidth(110)
        self.connect_button.clicked.connect(self._on_connect_clicked)

        self.kind_badge = Badge("—")
        self.kind_badge.setToolTip("Через что идёт связь сейчас")
        self.led = StatusLed()
        self.status_label = QLabel()
        self.keepalive_check = QCheckBox("Опрос связи")
        self.keepalive_check.setToolTip(
            "Периодически отправлять запрос из профиля (link.keepalive_command),\n"
            "чтобы следить за связью. Для эхо-теста и AT-команд лучше выключить."
        )
        self.keepalive_check.toggled.connect(self.keepalive_toggled)
        self.stats_label = QLabel()
        self.stats_label.setStyleSheet(f"color: {GREY};")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.addWidget(QLabel("Порт:"))
        layout.addWidget(self.port_combo, 1)
        layout.addWidget(self.refresh_button)
        layout.addSpacing(8)
        layout.addWidget(QLabel("Скорость:"))
        layout.addWidget(self.baud_combo)
        layout.addWidget(self.connect_button)
        layout.addSpacing(12)
        layout.addWidget(self.kind_badge)
        layout.addSpacing(8)
        layout.addWidget(self.led)
        layout.addWidget(self.status_label)
        layout.addSpacing(12)
        layout.addWidget(self.keepalive_check)
        layout.addSpacing(12)
        layout.addWidget(self.stats_label)
        layout.addStretch(0)

        self.refresh_ports()
        self.set_state(LinkState.CLOSED, None)

    # ------------------------------------------------------------------ ports

    def refresh_ports(self) -> None:
        current = self.current_device()
        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        try:
            ports = list_ports()
        except Exception:  # noqa: BLE001 - enumeration trouble must not break the UI
            ports = []
        for port in ports:
            self.port_combo.addItem(port.label, port.device)
            self.port_combo.setItemData(
                self.port_combo.count() - 1, port.hwid or port.description, Qt.ItemDataRole.ToolTipRole
            )
        self.port_combo.blockSignals(False)
        if current:
            self.select_device(current)
        elif ports:
            self.port_combo.setCurrentIndex(0)
        if not ports:
            self.port_combo.lineEdit().setPlaceholderText("портов нет — подключите машинку или введите COM-порт")

    def select_device(self, device: str) -> None:
        for i in range(self.port_combo.count()):
            if str(self.port_combo.itemData(i)).lower() == device.lower():
                self.port_combo.setCurrentIndex(i)
                return
        self.port_combo.setEditText(device)

    def current_device(self) -> str:
        text = self.port_combo.currentText().strip()
        index = self.port_combo.currentIndex()
        if index >= 0 and text == self.port_combo.itemText(index):
            return str(self.port_combo.itemData(index))
        return text.split(" · ")[0].strip()

    def baudrate(self) -> int:
        try:
            return int(self.baud_combo.currentText())
        except ValueError:
            return 19200

    def set_baudrate(self, baud: int) -> None:
        self.baud_combo.setCurrentText(str(baud))

    # ------------------------------------------------------------------ state

    def set_state(self, state: LinkState, port: PortInfo | None) -> None:
        self._state = state
        color, text = STATE_VIEW[state]
        self.led.set_color(color)
        self.status_label.setText(text)
        busy = state != LinkState.CLOSED
        self.port_combo.setEnabled(not busy)
        self.refresh_button.setEnabled(not busy)
        self.baud_combo.setEnabled(not busy)
        self.connect_button.setText(
            "Отменить" if state == LinkState.CONNECTING else "Отключить" if busy else "Подключить"
        )
        if busy and port is not None:
            self.kind_badge.set_badge(KIND_LABELS[port.kind], KIND_COLORS.get(port.kind, GREY))
            self.kind_badge.setToolTip(
                f"{port.device}: {port.description or port.hwid or 'связь через ' + KIND_LABELS[port.kind]}"
            )
        else:
            self.kind_badge.set_badge("—", GREY)
            self.kind_badge.setToolTip("Нет подключения")
        if not busy:
            self.stats_label.clear()

    def set_stats(self, stats: LinkStats) -> None:
        if self._state == LinkState.CLOSED:
            return
        parts = []
        if stats.avg_rtt_ms is not None:
            parts.append(f"RTT {stats.avg_rtt_ms:.0f} мс")
        parts.append(f"пакетов ↑{stats.tx_packets} ↓{stats.rx_packets}")
        errors = stats.timeouts + stats.bad_frames + stats.nacks
        if errors:
            parts.append(f"ошибок {errors}")
        self.stats_label.setText(" · ".join(parts))
        self.stats_label.setToolTip(
            f"Отправлено байт: {stats.tx_bytes}, принято байт: {stats.rx_bytes}\n"
            f"Таймаутов: {stats.timeouts}, битых пакетов: {stats.bad_frames}, отказов (ACK≠0): {stats.nacks}"
        )

    def _on_connect_clicked(self) -> None:
        if self._state == LinkState.CLOSED:
            device = self.current_device()
            if device:
                self.connect_requested.emit(device, self.baudrate())
        else:
            self.disconnect_requested.emit()
