"""Macro tab: list of macro files, editor with live validation, run / stop."""

from __future__ import annotations

import re
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QKeySequence, QShortcut, QTextCursor, QTextFormat
from PyQt6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..control.macro import FlatStep, MacroError, flatten, parse_macro, total_duration
from ..protocol.commands import encode_command
from ..protocol.profile import Profile
from .widgets import GREEN, GREY, PURPLE, RED, mono_font

NEW_MACRO_TEMPLATE = """# Новый макрос. Скорость и угол — в процентах от максимума.
вперёд 1.5 скорость=60
тормоз
назад 1.5 скорость=60
"""

CHEAT_SHEET = """<table cellspacing="2">
<tr><td><code>вперёд 1.5 скорость=80 угол=-20</code></td><td>ехать вперёд 1,5 с</td></tr>
<tr><td><code>назад 2</code></td><td>назад 2 с</td></tr>
<tr><td><code>стоп 1</code></td><td>остановиться и стоять 1 с</td></tr>
<tr><td><code>тормоз 0.5</code></td><td>торможение (drive.brake в профиле)</td></tr>
<tr><td><code>пауза 1</code></td><td>ничего не отправлять 1 с</td></tr>
<tr><td><code>скорость 70</code></td><td>скорость по умолчанию, %</td></tr>
<tr><td><code>команда LED 0</code></td><td>любая команда протокола</td></tr>
<tr><td><code>текст "AT\\r\\n"</code>, <code>hex AC 53</code></td><td>сырые данные</td></tr>
<tr><td><code>повтор 3</code> … <code>конец</code></td><td>повторить блок</td></tr>
<tr><td><code># …</code></td><td>комментарий</td></tr>
</table>
<p style="color:gray">Время: <code>1.5</code>, <code>1,5с</code>, <code>500мс</code>. Английские слова тоже
работают (forward, backward, stop, brake, wait, send, repeat, end).<br>
Любая клавиша управления, геймпад или Esc прерывает макрос; в конце машинка останавливается.</p>"""

_BAD_FILENAME = re.compile(r'[\\/:*?"<>|]')


class MacroPanel(QWidget):
    run_requested = pyqtSignal(list, str)  # flat steps, macro name
    stop_requested = pyqtSignal()

    def __init__(self, directory: Path, profile: Profile, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dir = directory
        self._profile = profile
        self._current: Path | None = None
        self._modified = False
        self._loading = False
        self._running = False
        self._flat: list[FlatStep] = []
        self._error_line: int | None = None
        self._run_line: int | None = None

        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._on_select)
        new_button = QPushButton("Новый")
        new_button.clicked.connect(self._new)
        delete_button = QPushButton("Удалить")
        delete_button.clicked.connect(self._delete)
        folder_button = QPushButton("Папка")
        folder_button.setToolTip(str(directory))
        folder_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._dir))))
        list_buttons = QHBoxLayout()
        list_buttons.addWidget(new_button)
        list_buttons.addWidget(delete_button)
        list_buttons.addWidget(folder_button)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("Макросы"))
        left_layout.addWidget(self.list, 1)
        left_layout.addLayout(list_buttons)

        self.title = QLabel()
        self.title.setStyleSheet("font-weight: bold;")
        self.editor = QPlainTextEdit()
        self.editor.setFont(mono_font())
        self.editor.setTabStopDistance(32)
        self.editor.textChanged.connect(self._on_text_changed)
        self.status = QLabel()
        self.status.setWordWrap(True)

        self.save_button = QPushButton("Сохранить")
        self.save_button.setToolTip("Ctrl+S")
        self.save_button.clicked.connect(self.save)
        self.run_button = QPushButton("▶ Запустить")
        self.run_button.setToolTip("F5")
        self.run_button.clicked.connect(self._run)
        self.stop_button = QPushButton("■ Стоп")
        self.stop_button.setToolTip("Esc")
        self.stop_button.clicked.connect(self.stop_requested)
        self.stop_button.setEnabled(False)
        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setFormat("")
        self.step_label = QLabel()
        self.step_label.setStyleSheet(f"color: {GREY};")

        buttons = QHBoxLayout()
        buttons.addWidget(self.save_button)
        buttons.addStretch(1)
        buttons.addWidget(self.run_button)
        buttons.addWidget(self.stop_button)

        editor_box = QWidget()
        editor_layout = QVBoxLayout(editor_box)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.addWidget(self.title)
        editor_layout.addWidget(self.editor, 1)
        editor_layout.addWidget(self.status)
        editor_layout.addLayout(buttons)
        editor_layout.addWidget(self.progress)
        editor_layout.addWidget(self.step_label)

        cheat = QLabel(CHEAT_SHEET)
        cheat.setWordWrap(True)
        cheat.setTextFormat(Qt.TextFormat.RichText)
        cheat.setAlignment(Qt.AlignmentFlag.AlignTop)
        cheat_box = QGroupBox("Шпаргалка")
        cheat_layout = QVBoxLayout(cheat_box)
        cheat_layout.addWidget(cheat)
        cheat_layout.addStretch(1)

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(editor_box)
        splitter.addWidget(cheat_box)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 2)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(splitter)

        self._validate_timer = QTimer(self)
        self._validate_timer.setSingleShot(True)
        self._validate_timer.setInterval(250)
        self._validate_timer.timeout.connect(self.validate)

        context = Qt.ShortcutContext.WidgetWithChildrenShortcut
        QShortcut(QKeySequence.StandardKey.Save, self, self.save, context=context)
        QShortcut(QKeySequence("F5"), self, self._run, context=context)

        self.reload_list()

    # ------------------------------------------------------------------ files

    def set_profile(self, profile: Profile) -> None:
        self._profile = profile
        self.validate()

    def reload_list(self, select: str | None = None) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        current = select or (self._current.stem if self._current else None)
        self.list.blockSignals(True)
        self.list.clear()
        for path in sorted(self._dir.glob("*.txt"), key=lambda p: p.stem.lower()):
            item = QListWidgetItem(path.stem)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.list.addItem(item)
        self.list.blockSignals(False)
        target = next(
            (self.list.item(i) for i in range(self.list.count()) if self.list.item(i).text() == current), None
        )
        if target is None and self.list.count():
            target = self.list.item(0)
        if target is not None:
            self.list.setCurrentItem(target)
            self._load(Path(target.data(Qt.ItemDataRole.UserRole)))
        else:
            self._load(None)

    def _on_select(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        path = Path(current.data(Qt.ItemDataRole.UserRole))
        if path == self._current:
            return
        if not self._confirm_discard():
            self.list.blockSignals(True)
            self.list.setCurrentItem(previous)
            self.list.blockSignals(False)
            return
        self._load(path)

    def _load(self, path: Path | None) -> None:
        self._loading = True
        self._current = path
        if path is None:
            self.editor.setPlainText("")
            self.editor.setEnabled(False)
        else:
            try:
                self.editor.setPlainText(path.read_text(encoding="utf-8-sig"))
            except OSError as exc:
                self.editor.setPlainText(f"# не удалось открыть файл: {exc}")
            self.editor.setEnabled(True)
        self._loading = False
        self._set_modified(False)
        self.validate()

    def save(self) -> bool:
        if self._current is None:
            return False
        try:
            self._current.write_text(self.editor.toPlainText(), encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "Макросы", f"Не удалось сохранить:\n{exc}")
            return False
        self._set_modified(False)
        return True

    def _new(self) -> None:
        name, ok = QInputDialog.getText(self, "Новый макрос", "Название:")
        name = name.strip()
        if not ok or not name:
            return
        if _BAD_FILENAME.search(name):
            QMessageBox.warning(self, "Новый макрос", 'В названии нельзя использовать \\ / : * ? " < > |')
            return
        path = self._dir / f"{name}.txt"
        if path.exists():
            QMessageBox.warning(self, "Новый макрос", "Макрос с таким названием уже есть.")
            return
        if not self._confirm_discard():
            return
        path.write_text(NEW_MACRO_TEMPLATE, encoding="utf-8")
        self._current = None
        self.reload_list(select=name)

    def _delete(self) -> None:
        if self._current is None or self._running:
            return
        answer = QMessageBox.question(self, "Удалить макрос", f"Удалить «{self._current.stem}»?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._current.unlink()
        except OSError as exc:
            QMessageBox.critical(self, "Макросы", f"Не удалось удалить:\n{exc}")
            return
        self._current = None
        self._set_modified(False)
        self.reload_list()

    def _confirm_discard(self) -> bool:
        if not self._modified:
            return True
        answer = QMessageBox.question(
            self,
            "Несохранённые изменения",
            f"Сохранить изменения в «{self._current.stem if self._current else ''}»?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    def _set_modified(self, modified: bool) -> None:
        self._modified = modified
        name = self._current.stem if self._current else "нет макросов — нажмите «Новый»"
        self.title.setText(name + (" *" if modified else ""))
        self.save_button.setEnabled(modified)

    @property
    def modified(self) -> bool:
        return self._modified

    # ------------------------------------------------------------------ validation

    def _on_text_changed(self) -> None:
        if self._loading:
            return
        self._set_modified(True)
        self._validate_timer.start()

    def validate(self) -> bool:
        self._error_line = None
        self._flat = []
        if self._current is None:
            self.status.clear()
            self._update_highlight()
            self.run_button.setEnabled(False)
            return False
        try:
            steps = parse_macro(self.editor.toPlainText(), lambda text: encode_command(self._profile, text))
            self._flat = flatten(steps)
        except MacroError as exc:
            self._error_line = exc.line
            self.status.setText(f"✗ {exc}")
            self.status.setStyleSheet(f"color: {RED};")
            self._update_highlight()
            self.run_button.setEnabled(False)
            return False
        seconds = total_duration(self._flat)
        self.status.setText(f"✓ Шагов: {len(self._flat)}, длительность ≈ {seconds:.1f} с")
        self.status.setStyleSheet(f"color: {GREEN};")
        self._update_highlight()
        self.run_button.setEnabled(not self._running and bool(self._flat))
        return bool(self._flat)

    def _update_highlight(self) -> None:
        selections = []
        for line, color in ((self._error_line, RED), (self._run_line, PURPLE)):
            if line is None:
                continue
            block = self.editor.document().findBlockByNumber(line - 1)
            if not block.isValid():
                continue
            selection = QTextEdit.ExtraSelection()
            selection.format.setBackground(QColor(color).lighter(175))
            selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            selection.cursor = QTextCursor(block)
            selections.append(selection)
        self.editor.setExtraSelections(selections)

    # ------------------------------------------------------------------ running

    def _run(self) -> None:
        if self._running or self._current is None:
            return
        if self._modified and not self.save():
            return
        if self.validate():
            self.run_requested.emit(list(self._flat), self._current.stem)

    def on_started(self, name: str) -> None:
        self._running = True
        self.editor.setReadOnly(True)
        self.run_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.list.setEnabled(False)
        self.progress.setValue(0)

    def on_step(self, index: int, step: FlatStep) -> None:
        self._run_line = step.line
        self._update_highlight()
        self.step_label.setText(f"Шаг {index + 1} из {len(self._flat)}: {step.describe()}")

    def on_progress(self, elapsed: float, total: float) -> None:
        self.progress.setMaximum(max(1, int(total * 10)))
        self.progress.setValue(int(elapsed * 10))
        self.progress.setFormat(f"{elapsed:.1f} / {total:.1f} с")

    def on_finished(self, ok: bool, message: str) -> None:
        self._running = False
        self._run_line = None
        self.editor.setReadOnly(False)
        self.stop_button.setEnabled(False)
        self.list.setEnabled(True)
        self._update_highlight()
        self.run_button.setEnabled(bool(self._flat))
        self.step_label.setText(f"Макрос {message}")
        self.step_label.setStyleSheet(f"color: {GREEN if ok else RED};")
        if ok:
            self.progress.setValue(self.progress.maximum())
