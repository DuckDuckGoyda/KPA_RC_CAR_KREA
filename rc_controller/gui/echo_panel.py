"""Echo test tab: checks that the car sends back exactly what it received."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..runtime.echo_test import EchoStats, EchoTester
from ..runtime.link import NotConnectedError
from .widgets import GREEN, GREY, RED, mono_font

INTRO = (
    "Программа отправляет блоки данных и ждёт, что машинка вернёт их без изменений "
    "(прошивка в режиме эха). Проверяются целостность данных и время отклика (RTT) "
    "для текущего соединения — USB или Bluetooth. На время теста опрос и команды движения "
    "приостанавливаются."
)


def _ms(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f} мс"


class EchoPanel(QWidget):
    finished = pyqtSignal(str, bool)  # summary, success

    def __init__(self, tester: EchoTester, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tester = tester

        intro = QLabel(INTRO)
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {GREY};")

        self.count = QSpinBox()
        self.count.setRange(1, 100_000)
        self.count.setValue(100)
        self.size = QSpinBox()
        self.size.setRange(1, 1024)
        self.size.setValue(16)
        self.size.setSuffix(" байт")
        self.interval = QSpinBox()
        self.interval.setRange(0, 10_000)
        self.interval.setValue(20)
        self.interval.setSuffix(" мс")
        self.timeout = QSpinBox()
        self.timeout.setRange(10, 60_000)
        self.timeout.setValue(1000)
        self.timeout.setSuffix(" мс")
        self.kind = QComboBox()
        self.kind.addItems(["Случайные байты", "Текст (ASCII + \\n)"])
        self.kind.setToolTip("Текст — если прошивка отвечает построчно или эхо проверяют глазами в терминале")

        form = QFormLayout()
        form.addRow("Количество блоков:", self.count)
        form.addRow("Размер блока:", self.size)
        form.addRow("Пауза между блоками:", self.interval)
        form.addRow("Ожидание ответа:", self.timeout)
        form.addRow("Данные:", self.kind)
        settings = QGroupBox("Параметры")
        settings.setLayout(form)

        self.start_button = QPushButton("▶ Начать")
        self.start_button.clicked.connect(self._start)
        self.stop_button = QPushButton("■ Остановить")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(lambda: self._tester.stop("остановлен"))
        self.progress = QProgressBar()

        self.values: dict[str, QLabel] = {}
        grid = QGridLayout()
        for row, (key, label) in enumerate(
            (
                ("sent", "Отправлено"),
                ("ok", "Совпало"),
                ("mismatched", "Искажено"),
                ("lost", "Без ответа"),
                ("rtt", "RTT мин / сред / макс"),
            )
        ):
            grid.addWidget(QLabel(label + ":"), row, 0)
            value = QLabel("—")
            value.setStyleSheet("font-weight: bold;")
            self.values[key] = value
            grid.addWidget(value, row, 1)
        results = QGroupBox("Результат")
        results.setLayout(grid)

        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setFont(mono_font())
        self.details.setMaximumBlockCount(2000)
        self.details.setPlaceholderText("Здесь появятся блоки с ошибками")

        buttons = QHBoxLayout()
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.stop_button)
        buttons.addWidget(self.progress, 1)

        top = QHBoxLayout()
        top.addWidget(settings, 1)
        top.addWidget(results, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(intro)
        layout.addLayout(top)
        layout.addLayout(buttons)
        layout.addWidget(self.details, 1)

        tester.progress.connect(self._show_stats)
        tester.sample.connect(self._on_sample)
        tester.finished.connect(self._on_finished)

    def _start(self) -> None:
        self.details.clear()
        self.progress.setMaximum(self.count.value())
        self.progress.setValue(0)
        self._show_stats(EchoStats(total=self.count.value()))
        try:
            self._tester.start(
                count=self.count.value(),
                size=self.size.value(),
                interval_ms=self.interval.value(),
                timeout_ms=self.timeout.value(),
                text=self.kind.currentIndex() == 1,
            )
        except NotConnectedError as exc:
            self.details.appendPlainText(f"✗ {exc}")
            return
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)

    def _show_stats(self, stats: EchoStats) -> None:
        self.values["sent"].setText(str(stats.sent))
        self.values["ok"].setText(str(stats.ok))
        self.values["mismatched"].setText(str(stats.mismatched))
        self.values["lost"].setText(str(stats.lost))
        self.values["rtt"].setText(f"{_ms(stats.rtt_min)} / {_ms(stats.rtt_avg)} / {_ms(stats.rtt_max)}")
        self.values["ok"].setStyleSheet(f"font-weight: bold; color: {GREEN if stats.ok else GREY};")
        bad = stats.mismatched + stats.lost
        for key in ("mismatched", "lost"):
            self.values[key].setStyleSheet(f"font-weight: bold; color: {RED if bad else GREY};")
        self.progress.setValue(stats.ok + stats.mismatched + stats.lost)

    def _on_sample(self, index: int, result: str, detail: str) -> None:
        if result != "ok":
            label = "искажён" if result == "mismatch" else "потерян"
            self.details.appendPlainText(f"блок {index}: {label} — {detail}")

    def _on_finished(self, stats: EchoStats, message: str) -> None:
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self._show_stats(stats)
        summary = (
            f"эхо-тест {message}: совпало {stats.ok} из {stats.sent}, искажено {stats.mismatched}, "
            f"без ответа {stats.lost}, RTT сред. {_ms(stats.rtt_avg)}"
        )
        self.details.appendPlainText(summary)
        self.finished.emit(summary, stats.sent > 0 and stats.ok == stats.sent)
