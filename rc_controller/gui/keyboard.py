"""Layout-independent key bindings.

With a Russian layout active, Qt reports the physical W key as "Ц". Bindings
therefore match the Qt key, the same physical key on ЙЦУКЕН, and on Windows
also the virtual-key code (which ignores the layout entirely).
"""

from __future__ import annotations

import sys

from PyQt6.QtGui import QKeyEvent, QKeySequence
from PyQt6.QtWidgets import QAbstractSpinBox, QLineEdit, QPlainTextEdit, QTextEdit, QWidget

_QWERTY = "QWERTYUIOPASDFGHJKLZXCVBNM"
_JCUKEN = "ЙЦУКЕНГШЩЗФЫВАПРОЛДЯЧСМИТЬ"
_RU_SAME_KEY = dict(zip(_QWERTY, _JCUKEN, strict=True))

_VK_SPECIAL = {"SPACE": 0x20, "UP": 0x26, "DOWN": 0x28, "LEFT": 0x25, "RIGHT": 0x27}


class KeyMap:
    def __init__(self, bindings: dict[str, str]) -> None:
        self._by_key: dict[int, str] = {}
        self._by_vk: dict[int, str] = {}
        for action, name in bindings.items():
            sequence = QKeySequence.fromString(name, QKeySequence.SequenceFormat.PortableText)
            if sequence.isEmpty() or sequence.count() != 1:
                raise ValueError(f"keys.{action}: не удалось распознать клавишу «{name}»")
            key = sequence[0].key().value
            self._by_key[key] = action
            upper = name.strip().upper()
            if len(upper) == 1 and upper in _RU_SAME_KEY:
                self._by_key[ord(_RU_SAME_KEY[upper])] = action
            if len(upper) == 1 and (upper.isascii() and upper.isalnum()):
                self._by_vk[ord(upper)] = action
            elif upper in _VK_SPECIAL:
                self._by_vk[_VK_SPECIAL[upper]] = action

    def action_for(self, event: QKeyEvent) -> str | None:
        action = self._by_key.get(event.key())
        if action is None and sys.platform == "win32":
            action = self._by_vk.get(event.nativeVirtualKey())
        return action


def is_text_input(widget: QWidget | None) -> bool:
    """True if keystrokes should go to the widget as typing rather than driving."""
    if isinstance(widget, QLineEdit):
        return not widget.isReadOnly()
    if isinstance(widget, (QPlainTextEdit, QTextEdit)):
        return not widget.isReadOnly()
    return isinstance(widget, QAbstractSpinBox)
