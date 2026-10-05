"""Left panel: what the car is being told, key indicators, speed limit, STOP."""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..control.drive import DriveValues
from ..protocol.profile import Profile
from ..runtime.drive_controller import DriveOutput
from .widgets import BLUE, GREEN, GREY, PURPLE, RED

SENSITIVITY_RANGE = (10, 300)  # percent of the profile's ramp rates

SOURCE_NAMES = {"idle": "—", "keyboard": "клавиатура", "gamepad": "геймпад", "macro": "макрос"}


class DriveIndicator(QWidget):
    """Crosshair with a dot: vertical = throttle, horizontal = steering."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._throttle = 0.0
        self._steer = 0.0
        self._color = QColor(GREY)
        self.setMinimumSize(180, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def set_values(self, throttle: float, steer: float, color: str) -> None:
        self._throttle, self._steer, self._color = throttle, steer, QColor(color)
        self.update()

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return width

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height()) - 8
        rect = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
        center = rect.center()

        painter.setPen(QPen(self.palette().mid().color(), 1))
        painter.setBrush(self.palette().base())
        painter.drawRoundedRect(rect, 10, 10)
        painter.setPen(QPen(self.palette().mid().color(), 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(rect.left(), center.y()), QPointF(rect.right(), center.y()))
        painter.drawLine(QPointF(center.x(), rect.top()), QPointF(center.x(), rect.bottom()))

        painter.setPen(self.palette().text().color())
        painter.drawText(rect.adjusted(0, 4, 0, 0), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, "вперёд")
        painter.drawText(
            rect.adjusted(0, 0, 0, -4), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, "назад"
        )

        radius = side / 2 - 12
        dot = QPointF(center.x() + self._steer * radius, center.y() - self._throttle * radius)
        painter.setPen(QPen(self._color, 3))
        painter.drawLine(center, dot)
        painter.setBrush(self._color)
        painter.drawEllipse(dot, 9, 9)


class KeyCap(QLabel):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(46, 40)
        self.set_down(False)

    def set_down(self, down: bool) -> None:
        if down:
            style = f"background: {BLUE}; color: white; border: 1px solid {BLUE};"
        else:
            style = "border: 1px solid palette(mid); background: palette(button);"
        self.setStyleSheet(f"QLabel {{ {style} border-radius: 6px; font-weight: bold; font-size: 15px; }}")


class PercentSetting(QWidget):
    """Title, a box to type the exact percentage into, and a slider under them, kept in sync."""

    value_changed = pyqtSignal(int)
    edit_committed = pyqtSignal()  # Enter pressed in the box

    def __init__(self, title: str, lo: int, hi: int, step: int, tooltip: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.title = QLabel(title)
        self.detail = QLabel()
        self.detail.setStyleSheet(f"color: {GREY};")
        self.detail.hide()

        self.spin = QSpinBox()
        self.spin.setRange(lo, hi)
        self.spin.setSuffix(" %")
        self.spin.setKeyboardTracking(False)  # typing "150" applies 150, not 1 and 15 on the way
        self.spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.spin.setMinimumWidth(80)
        self.spin.valueChanged.connect(self._on_spin)
        self.spin.editingFinished.connect(self._on_editing_finished)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(lo, hi)
        self.slider.setSingleStep(step)
        self.slider.setPageStep(step)
        self.slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # arrow keys must not steal driving focus
        self.slider.valueChanged.connect(self._on_slider)

        for widget in (self.title, self.spin, self.slider):
            widget.setToolTip(tooltip)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self.title, 1)
        header.addWidget(self.spin)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addLayout(header)
        layout.addWidget(self.slider)
        layout.addWidget(self.detail)

    def value(self) -> int:
        return self.spin.value()

    def set_value(self, value: int) -> None:
        """Set without emitting value_changed."""
        for widget in (self.spin, self.slider):
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)

    def set_detail(self, text: str) -> None:
        self.detail.setText(text)
        self.detail.setVisible(bool(text))

    def _on_spin(self, value: int) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(value)
        self.slider.blockSignals(False)
        self.value_changed.emit(value)

    def _on_slider(self, value: int) -> None:
        self.spin.blockSignals(True)
        self.spin.setValue(value)
        self.spin.blockSignals(False)
        self.value_changed.emit(value)

    def _on_editing_finished(self) -> None:
        # Also fires when focus just moves elsewhere; only Enter should hand the keyboard back.
        if self.spin.hasFocus():
            self.edit_committed.emit()


class DrivePanel(QWidget):
    stop_clicked = pyqtSignal()
    speed_limit_changed = pyqtSignal(float)
    sensitivity_changed = pyqtSignal(float, float)  # throttle, steering multipliers

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mode = "speed_angle"
        self._accel = 2.0
        self._steer_rate = 5.0

        self.indicator = DriveIndicator()
        self.values_label = QLabel()
        self.values_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.values_label.setStyleSheet("font-size: 14px;")
        self.source_label = QLabel()
        self.source_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.source_label.setStyleSheet(f"color: {GREY};")

        self.caps = {
            "forward": KeyCap("W"),
            "left": KeyCap("A"),
            "backward": KeyCap("S"),
            "right": KeyCap("D"),
        }
        keys = QGridLayout()
        keys.setSpacing(4)
        keys.addWidget(self.caps["forward"], 0, 1)
        keys.addWidget(self.caps["left"], 1, 0)
        keys.addWidget(self.caps["backward"], 1, 1)
        keys.addWidget(self.caps["right"], 1, 2)
        keys_box = QWidget()
        keys_box.setLayout(keys)

        self.keyboard_label = QLabel()
        self.keyboard_label.setWordWrap(True)
        self.keyboard_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.gamepad_label = QLabel()
        self.gamepad_label.setWordWrap(True)
        self.gamepad_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.speed_limit = PercentSetting(
            "Ограничение скорости", 5, 100, 5, "Максимальная скорость для клавиатуры, геймпада и макросов"
        )
        self.throttle_sens = PercentSetting(
            "Чувствительность газа", *SENSITIVITY_RANGE, 10, "Как быстро нарастает газ (клавиатура и геймпад)"
        )
        self.steer_sens = PercentSetting(
            "Чувствительность руля", *SENSITIVITY_RANGE, 10, "Как быстро поворачиваются колёса (клавиатура и геймпад)"
        )
        self.speed_limit.value_changed.connect(self._on_limit)
        for setting in (self.throttle_sens, self.steer_sens):
            setting.value_changed.connect(self._on_sensitivity)
        for setting in (self.speed_limit, self.throttle_sens, self.steer_sens):
            setting.edit_committed.connect(self.setFocus)  # Enter: back to driving

        self.stop_button = QPushButton("СТОП\nПробел / Esc")
        self.stop_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.stop_button.setMinimumHeight(64)
        self.stop_button.setStyleSheet(
            f"QPushButton {{ background: {RED}; color: white; font-size: 18px; font-weight: bold;"
            " border-radius: 8px; }"
            f"QPushButton:pressed {{ background: {QColor(RED).darker(130).name()}; }}"
        )
        self.stop_button.clicked.connect(self.stop_clicked)

        box = QGroupBox("Управление")
        inner = QVBoxLayout(box)
        inner.addWidget(self.indicator, 1)
        inner.addWidget(self.values_label)
        inner.addWidget(self.source_label)
        inner.addWidget(keys_box, 0, Qt.AlignmentFlag.AlignHCenter)
        inner.addWidget(self.keyboard_label)
        inner.addWidget(self.gamepad_label)
        inner.addSpacing(6)
        inner.addWidget(self.speed_limit)
        inner.addWidget(self.throttle_sens)
        inner.addWidget(self.steer_sens)
        inner.addSpacing(6)
        inner.addWidget(self.stop_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(box)

        self.set_speed_limit(100)
        self.set_sensitivity(100, 100)
        self.set_output(DriveOutput(DriveValues(), "idle"))
        self.set_keyboard_active(True)
        self.set_gamepad_status(False, "")

    # ------------------------------------------------------------------ updates

    def set_profile(self, profile: Profile) -> None:
        self._mode = profile.drive.mode
        self._accel = profile.drive.accel
        self._steer_rate = profile.drive.steer_rate
        self._update_sensitivity_labels()
        names = profile.keys.as_dict()
        for action, cap in self.caps.items():
            cap.setText(names[action].upper() if len(names[action]) == 1 else names[action])

    def set_output(self, output: DriveOutput) -> None:
        values = output.values
        color = {"keyboard": BLUE, "gamepad": GREEN, "macro": PURPLE}.get(output.source, GREY)
        self.indicator.set_values(values.throttle, values.steer, color)
        if self._mode == "differential":
            self.values_label.setText(f"Левый борт: <b>{values.left}</b> &nbsp; Правый: <b>{values.right}</b>")
        else:
            self.values_label.setText(f"Скорость: <b>{values.speed}</b> &nbsp; Угол: <b>{values.angle}</b>")
        self.source_label.setText(f"Источник: {SOURCE_NAMES.get(output.source, output.source)}")

    def set_keys(self, held: frozenset[str]) -> None:
        for action, cap in self.caps.items():
            cap.set_down(action in held)

    def set_keyboard_active(self, active: bool) -> None:
        if active:
            self.keyboard_label.setText("⌨ Клавиатура управляет машинкой")
            self.keyboard_label.setStyleSheet(f"color: {GREEN};")
        else:
            self.keyboard_label.setText("⌨ Идёт ввод текста — Esc вернёт управление")
            self.keyboard_label.setStyleSheet(f"color: {GREY};")

    def set_gamepad_status(self, connected: bool, text: str) -> None:
        if connected:
            self.gamepad_label.setText(f"🎮 Геймпад: {text}")
            self.gamepad_label.setStyleSheet(f"color: {GREEN};")
        else:
            self.gamepad_label.setText(f"🎮 Геймпад: {text or 'не найден'}")
            self.gamepad_label.setStyleSheet(f"color: {GREY};")

    def set_speed_limit(self, percent: int) -> None:
        self.speed_limit.set_value(percent)
        self._on_limit()

    def _on_limit(self) -> None:
        self.speed_limit_changed.emit(self.speed_limit.value() / 100)

    def set_sensitivity(self, throttle_percent: int, steer_percent: int) -> None:
        self.throttle_sens.set_value(throttle_percent)
        self.steer_sens.set_value(steer_percent)
        self._on_sensitivity()

    def _on_sensitivity(self) -> None:
        self._update_sensitivity_labels()
        self.sensitivity_changed.emit(self.throttle_sens.value() / 100, self.steer_sens.value() / 100)

    def _update_sensitivity_labels(self) -> None:
        to_full = 1 / (self._accel * self.throttle_sens.value() / 100)
        to_lock = 1 / (self._steer_rate * self.steer_sens.value() / 100)
        self.throttle_sens.set_detail(f"до максимума за {to_full:.2f} с".replace(".", ","))
        self.steer_sens.set_detail(f"до упора за {to_lock:.2f} с".replace(".", ","))
