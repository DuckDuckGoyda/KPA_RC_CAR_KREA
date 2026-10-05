"""Command log: every packet, reply, timeout and event, decoded."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    QTimer,
)
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ..runtime.link import LogEntry
from ..runtime.session_log import DIRECTIONS, write_csv
from .widgets import GREEN, RED, YELLOW, mono_font

SOURCE_NAMES = {
    "terminal": "терминал",
    "drive": "движение",
    "keepalive": "опрос",
    "macro": "макрос",
    "stop": "стоп",
    "echo": "эхо-тест",
    "link": "связь",
}
LEVEL_COLORS = {"ok": GREEN, "warn": "#e65100", "error": RED}
COLUMNS = ("Время", "", "Источник", "SQN", "Содержание", "Байты", "Статус", "RTT, мс")
MAX_ROWS = 20_000
TRIM_ROWS = 2_000


class LogModel(QAbstractTableModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.entries: list[LogEntry] = []
        self._mono = mono_font()

    def rowCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        return 0 if parent is not None and parent.isValid() else len(self.entries)

    def columnCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        entry = self.entries[index.row()]
        column = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if column == 0:
                return entry.time.strftime("%H:%M:%S.%f")[:-3]
            if column == 1:
                return DIRECTIONS.get(entry.direction, "")
            if column == 2:
                return SOURCE_NAMES.get(entry.source, entry.source)
            if column == 3:
                return "" if entry.sqn is None else str(entry.sqn)
            if column == 4:
                return entry.text
            if column == 5:
                return entry.data.hex(" ").upper()
            if column == 6:
                return entry.status
            if column == 7:
                return "" if entry.rtt_ms is None else f"{entry.rtt_ms:.1f}"
        elif role == Qt.ItemDataRole.ForegroundRole:
            color = LEVEL_COLORS.get(entry.level)
            if color and column in (1, 4, 6):
                return QColor(color)
        elif role == Qt.ItemDataRole.BackgroundRole:
            if entry.level == "error":
                return QColor(RED).lighter(185)
            if entry.status == "эхо":
                return QColor(YELLOW).lighter(175)
        elif role == Qt.ItemDataRole.FontRole and column == 5:
            return self._mono
        elif role == Qt.ItemDataRole.ToolTipRole and column in (4, 5):
            return entry.text if column == 4 else entry.data.hex(" ").upper()
        elif role == Qt.ItemDataRole.TextAlignmentRole and column in (1, 3, 7):
            return Qt.AlignmentFlag.AlignCenter
        return None

    def append(self, entry: LogEntry) -> None:
        if len(self.entries) >= MAX_ROWS:
            self.beginRemoveRows(QModelIndex(), 0, TRIM_ROWS - 1)
            del self.entries[:TRIM_ROWS]
            self.endRemoveRows()
        row = len(self.entries)
        self.beginInsertRows(QModelIndex(), row, row)
        self.entries.append(entry)
        self.endInsertRows()

    def clear(self) -> None:
        self.beginResetModel()
        self.entries.clear()
        self.endResetModel()


class LogFilter(QSortFilterProxyModel):
    def __init__(self, model: LogModel) -> None:
        super().__init__()
        self.setSourceModel(model)
        self._model = model
        self.hidden_sources: set[str] = set()
        self.hide_junk = False
        self.text = ""

    def refresh(self) -> None:
        self.invalidateFilter()

    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:  # noqa: N802
        entry = self._model.entries[row]
        if entry.source in self.hidden_sources:
            return False
        if self.hide_junk and entry.kind == "junk":
            return False
        if self.text:
            haystack = f"{entry.text} {entry.status} {entry.data.hex(' ')}".lower()
            return self.text in haystack
        return True


class LogPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model = LogModel(self)
        self.proxy = LogFilter(self.model)

        self.view = QTableView()
        self.view.setModel(self.proxy)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setWordWrap(False)
        self.view.verticalHeader().setVisible(False)
        self.view.verticalHeader().setDefaultSectionSize(22)
        header = self.view.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        for column, width in enumerate((95, 24, 80, 44, 360, 250, 130, 64)):
            self.view.setColumnWidth(column, width)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)

        self.filters: dict[str, QCheckBox] = {}
        toolbar = QHBoxLayout()
        for key, label, sources in (
            ("drive", "Движение", {"drive"}),
            ("keepalive", "Опрос", {"keepalive"}),
            ("macro", "Макросы", {"macro"}),
            ("echo", "Эхо-тест", {"echo"}),
        ):
            box = QCheckBox(label)
            box.setChecked(True)
            box.setProperty("sources", sources)
            box.toggled.connect(self._apply_filters)
            self.filters[key] = box
            toolbar.addWidget(box)
        self.junk_check = QCheckBox("Данные вне пакетов")
        self.junk_check.setChecked(True)
        self.junk_check.setToolTip("Байты, не похожие на пакет протокола: текст, ответы AT, мусор")
        self.junk_check.toggled.connect(self._apply_filters)
        toolbar.addWidget(self.junk_check)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filters)
        toolbar.addWidget(self.search, 1)

        self.autoscroll = QCheckBox("Автопрокрутка")
        self.autoscroll.setChecked(True)
        toolbar.addWidget(self.autoscroll)
        clear_button = QPushButton("Очистить")
        clear_button.clicked.connect(self.model.clear)
        toolbar.addWidget(clear_button)
        save_button = QPushButton("Сохранить…")
        save_button.setToolTip("Сохранить журнал в CSV (открывается в Excel)")
        save_button.clicked.connect(self._save)
        toolbar.addWidget(save_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(toolbar)
        layout.addWidget(self.view)

        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.setInterval(50)
        self._scroll_timer.timeout.connect(self.view.scrollToBottom)

    def append(self, entry: LogEntry) -> None:
        self.model.append(entry)
        if self.autoscroll.isChecked() and not self._scroll_timer.isActive():
            self._scroll_timer.start()

    def _apply_filters(self) -> None:
        hidden: set[str] = set()
        for box in self.filters.values():
            if not box.isChecked():
                hidden |= box.property("sources")
        self.proxy.hidden_sources = hidden
        self.proxy.hide_junk = not self.junk_check.isChecked()
        self.proxy.text = self.search.text().strip().lower()
        self.proxy.refresh()

    def _save(self) -> None:
        default = Path.home() / f"журнал_{datetime.now():%Y-%m-%d_%H-%M-%S}.csv"
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить журнал", str(default), "CSV (*.csv)")
        if not path:
            return
        try:
            write_csv(Path(path), list(self.model.entries))
        except OSError as exc:
            QMessageBox.critical(self, "Журнал", f"Не удалось сохранить файл:\n{exc}")
