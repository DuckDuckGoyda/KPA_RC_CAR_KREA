"""Built-in UART terminal: send protocol commands, text (AT commands) or raw hex."""

from __future__ import annotations

import codecs
from datetime import datetime

from PyQt6.QtCore import QStringListModel, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QKeyEvent, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..control.macro import decode_escapes
from ..protocol.commands import CommandError
from ..protocol.profile import Profile
from ..runtime.link import LinkManager, NotConnectedError
from .widgets import BLUE, GREY, RED, mono_font

MODE_COMMAND, MODE_TEXT, MODE_HEX = range(3)
VIEW_AUTO, VIEW_TEXT, VIEW_HEX = range(3)
LINE_ENDINGS = (("без окончания", b""), ("CR  \\r", b"\r"), ("LF  \\n", b"\n"), ("CR+LF  \\r\\n", b"\r\n"))

# HC-05 in AT mode: KEY (PIN34) high at power-up, 38400 baud, every command ends with \r\n.
AT_SNIPPETS = (
    ("AT", "проверка связи, ответ OK"),
    ("AT+VERSION?", "версия прошивки модуля"),
    ("AT+NAME?", "имя модуля"),
    ("AT+NAME=RC_CAR", "задать имя"),
    ("AT+PSWD?", "PIN-код"),
    ("AT+PSWD=1234", "задать PIN-код"),
    ("AT+UART?", "скорость UART в режиме данных"),
    ("AT+UART=19200,0,0", "19200 бод, 1 стоп-бит, без чётности — как в протоколе"),
    ("AT+ROLE?", "роль: 0 — ведомый, 1 — ведущий"),
    ("AT+ROLE=0", "ведомый (к нему подключается ПК)"),
    ("AT+ADDR?", "Bluetooth-адрес модуля"),
    ("AT+STATE?", "состояние модуля"),
    ("AT+ORGL", "сброс к заводским настройкам"),
    ("AT+RESET", "перезагрузка модуля"),
)


def looks_like_text(data: bytes) -> bool:
    """UTF-8 with hardly any control characters (other than line breaks and tabs)."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    controls = sum(1 for ch in text if ord(ch) < 32 and ch not in "\r\n\t")
    return controls * 10 <= len(text)


class HistoryLineEdit(QLineEdit):
    submitted = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._history: list[str] = []
        self._position = 0
        self.returnPressed.connect(self._submit)

    def _submit(self) -> None:
        text = self.text()
        if not text.strip():
            return
        if not self._history or self._history[-1] != text:
            self._history.append(text)
        self._position = len(self._history)
        self.submitted.emit(text)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        popup = self.completer().popup() if self.completer() else None
        if popup is not None and popup.isVisible():
            super().keyPressEvent(event)
            return
        if event.key() == Qt.Key.Key_Up and self._history:
            self._position = max(0, self._position - 1)
            self.setText(self._history[self._position])
        elif event.key() == Qt.Key.Key_Down and self._history:
            self._position = min(len(self._history), self._position + 1)
            self.setText(self._history[self._position] if self._position < len(self._history) else "")
        else:
            super().keyPressEvent(event)


class TerminalPanel(QWidget):
    def __init__(self, link: LinkManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._link = link
        self._profile = link.profile
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._rx_line_open = False
        self._last_cr = False

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(mono_font())
        self.output.setMaximumBlockCount(5000)
        self.output.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)

        self.view_mode = QComboBox()
        self.view_mode.addItems(["Авто", "Текст", "HEX"])
        self.view_mode.setToolTip(
            "Как показывать принятые байты.\nАвто — текст показывается текстом, пакеты и прочие двоичные данные — в HEX."
        )
        self.timestamps = QCheckBox("Время")
        self.timestamps.setChecked(True)
        clear_button = QPushButton("Очистить")
        clear_button.clicked.connect(self._clear)

        top = QHBoxLayout()
        top.addWidget(QLabel("Приём:"))
        top.addWidget(self.view_mode)
        top.addWidget(self.timestamps)
        top.addStretch(1)
        top.addWidget(clear_button)

        self.input_mode = QComboBox()
        self.input_mode.addItems(["Команда", "Текст", "HEX"])
        self.input_mode.setToolTip(
            "Команда — команда протокола из профиля (SS_SA 50 -20) или байты команды (0x05 1 2),\n"
            "           программа сама соберёт пакет: синхрослово, LEN, SQN, ADDR, CRC.\n"
            "Текст — строка как есть (AT-команды HC-05); \\r \\n \\t \\xNN — спецсимволы.\n"
            "HEX — произвольные байты: AC 53 04 00 01 04 AB"
        )
        self.input_mode.currentIndexChanged.connect(self._on_mode_changed)
        self.line_ending = QComboBox()
        for label, _ in LINE_ENDINGS:
            self.line_ending.addItem(label)
        self.line_ending.setCurrentIndex(3)
        self.line_ending.setToolTip("Что добавить в конец строки в режиме «Текст»")

        self.input = HistoryLineEdit()
        self.input.setFont(mono_font())
        self.input.submitted.connect(self._send)
        self._completion_model = QStringListModel(self)
        self._empty_model = QStringListModel(self)
        completer = QCompleter(self._completion_model, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.input.setCompleter(completer)
        send_button = QPushButton("Отправить")
        send_button.clicked.connect(lambda: self.input.returnPressed.emit())

        bottom = QHBoxLayout()
        bottom.addWidget(self.input_mode)
        bottom.addWidget(self.input, 1)
        bottom.addWidget(self.line_ending)
        bottom.addWidget(send_button)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addLayout(top)
        left_layout.addWidget(self.output, 1)
        left_layout.addLayout(bottom)

        self.snippets = QTreeWidget()
        self.snippets.setHeaderLabels(["Быстрые команды", ""])
        self.snippets.setColumnCount(1)
        self.snippets.setRootIsDecorated(True)
        self.snippets.itemDoubleClicked.connect(self._on_snippet)
        snippets_box = QWidget()
        snippets_layout = QVBoxLayout(snippets_box)
        snippets_layout.setContentsMargins(0, 0, 0, 0)
        hint = QLabel("Двойной щелчок — вставить в строку ввода")
        hint.setStyleSheet(f"color: {GREY};")
        hint.setWordWrap(True)
        snippets_layout.addWidget(self.snippets, 1)
        snippets_layout.addWidget(hint)

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(snippets_box)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(splitter)

        self._formats = {
            "tx": self._format(BLUE),
            "rx": self._format(None),
            "info": self._format(GREY),
            "error": self._format(RED),
        }
        self.set_profile(link.profile)
        self._on_mode_changed(MODE_COMMAND)

    # ------------------------------------------------------------------ public

    def set_profile(self, profile: Profile) -> None:
        self._profile = profile
        self._completion_model.setStringList([spec.name for spec in profile.commands])
        self.snippets.clear()
        commands = QTreeWidgetItem(self.snippets, [f"Команды профиля ({profile.name})"])
        for spec in profile.commands:
            item = QTreeWidgetItem(commands, [spec.signature])
            item.setToolTip(0, f"0x{spec.code:02X} — {spec.description}" if spec.description else f"0x{spec.code:02X}")
            item.setData(0, Qt.ItemDataRole.UserRole, ("command", spec.name + (" " if spec.params else "")))
        at = QTreeWidgetItem(self.snippets, ["HC-05, AT-режим (38400 бод, KEY = 1)"])
        for command, description in AT_SNIPPETS:
            item = QTreeWidgetItem(at, [command])
            item.setToolTip(0, description)
            item.setData(0, Qt.ItemDataRole.UserRole, ("text", command))
        commands.setExpanded(True)
        at.setExpanded(True)

    def on_rx(self, data: bytes) -> None:
        view = self.view_mode.currentIndex()
        if view == VIEW_HEX or (view == VIEW_AUTO and not looks_like_text(data)):
            self._close_rx_line()
            self._write_line("rx", "← " + data.hex(" ").upper())
            return
        text = self._decoder.decode(data)
        for ch in text:
            if ch == "\n" and self._last_cr:
                self._last_cr = False
                continue
            self._last_cr = ch == "\r"
            if ch in "\r\n":
                if not self._rx_line_open:
                    self._open_rx_line()
                self._rx_line_open = False
                continue
            if not self._rx_line_open:
                self._open_rx_line()
            shown = ch if ch == "\t" or ord(ch) >= 32 else f"⟨{ord(ch):02X}⟩"
            self._insert(shown, "rx")

    # ------------------------------------------------------------------ sending

    def _send(self, text: str) -> None:
        mode = self.input_mode.currentIndex()
        try:
            if mode == MODE_COMMAND:
                command = self._link.send_command(text, "terminal")
                self._write_line("tx", f"→ {command.text}    (CMD = {command.payload.hex(' ').upper()})")
            else:
                if mode == MODE_TEXT:
                    data = decode_escapes(text) + LINE_ENDINGS[self.line_ending.currentIndex()][1]
                else:
                    cleaned = text.replace(",", " ").replace("0x", "").replace("0X", "")
                    try:
                        data = bytes.fromhex(cleaned)
                    except ValueError:
                        raise CommandError("HEX: ожидались пары шестнадцатеричных цифр, например AC 53 04") from None
                if not data:
                    raise CommandError("нечего отправлять")
                self._link.send_raw(data, "terminal")
                shown = text if mode == MODE_TEXT else data.hex(" ").upper()
                self._write_line("tx", f"→ {shown}")
        except (CommandError, NotConnectedError) as exc:
            self._write_line("error", f"✗ {exc}")
            return
        self.input.clear()

    def _on_mode_changed(self, mode: int) -> None:
        self.line_ending.setVisible(mode == MODE_TEXT)
        self.input.completer().setModel(self._completion_model if mode == MODE_COMMAND else self._empty_model)
        placeholders = {
            MODE_COMMAND: "SS_SA 50 -20   или   0x05 1 2",
            MODE_TEXT: "AT+VERSION?",
            MODE_HEX: "AC 53 04 00 01 04 AB",
        }
        self.input.setPlaceholderText(placeholders[mode])

    def _on_snippet(self, item: QTreeWidgetItem) -> None:
        payload = item.data(0, Qt.ItemDataRole.UserRole)
        if not payload:
            return
        kind, text = payload
        if kind == "command":
            self.input_mode.setCurrentIndex(MODE_COMMAND)
        else:
            self.input_mode.setCurrentIndex(MODE_TEXT)
            self.line_ending.setCurrentIndex(3)
        self.input.setText(text)
        self.input.setFocus()

    # ------------------------------------------------------------------ output helpers

    @staticmethod
    def _format(color: str | None) -> QTextCharFormat:
        fmt = QTextCharFormat()
        if color:
            fmt.setForeground(QColor(color))
        return fmt

    def _stamp(self) -> str:
        return datetime.now().strftime("%H:%M:%S.%f")[:-3] + "  " if self.timestamps.isChecked() else ""

    def _insert(self, text: str, kind: str) -> None:
        cursor = self.output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text, self._formats[kind])
        scrollbar = self.output.verticalScrollBar()
        if scrollbar.value() >= scrollbar.maximum() - 4:
            self.output.setTextCursor(cursor)
            self.output.ensureCursorVisible()

    def _open_rx_line(self) -> None:
        if not self.output.document().isEmpty():
            self._insert("\n", "rx")
        self._insert(self._stamp() + "← ", "rx")
        self._rx_line_open = True

    def _close_rx_line(self) -> None:
        self._rx_line_open = False

    def _write_line(self, kind: str, text: str) -> None:
        self._close_rx_line()
        if not self.output.document().isEmpty():
            self._insert("\n", kind)
        self._insert(self._stamp() + text, kind)

    def _clear(self) -> None:
        self.output.clear()
        self._rx_line_open = False
