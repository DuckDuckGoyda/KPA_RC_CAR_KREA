"""Small shared widgets and helpers."""

from __future__ import annotations

from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QLabel, QWidget

GREY = "#9e9e9e"
YELLOW = "#f9a825"
GREEN = "#2e7d32"
RED = "#c62828"
BLUE = "#1565c0"
PURPLE = "#5e35b1"
TEAL = "#00897b"


def mono_font(point_size: int = 0) -> QFont:
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    if point_size:
        font.setPointSize(point_size)
    return font


class StatusLed(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = QColor(GREY)
        self.setFixedSize(14, 14)

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(self._color.darker(140))
        painter.setBrush(self._color)
        painter.drawEllipse(QRectF(1, 1, self.width() - 2, self.height() - 2))


class Badge(QLabel):
    """Rounded coloured label, used for the USB / Bluetooth indicator."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumWidth(90)
        self.set_badge(text, GREY)

    def set_badge(self, text: str, color: str) -> None:
        self.setText(text)
        self.setStyleSheet(
            f"QLabel {{ background: {color}; color: white; border-radius: 9px; padding: 2px 10px; font-weight: bold; }}"
        )


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pixmap = QPixmap(QSize(size, size))
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(BLUE))
        painter.drawRoundedRect(QRectF(0, 0, size, size), size * 0.2, size * 0.2)
        painter.setPen(QColor("white"))
        font = QFont()
        font.setBold(True)
        font.setPixelSize(max(6, int(size * 0.45)))
        painter.setFont(font)
        painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, "RC")
        painter.end()
        icon.addPixmap(pixmap)
    return icon
