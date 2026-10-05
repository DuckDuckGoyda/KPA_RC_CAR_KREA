"""Help / FAQ window: Markdown pages from resources/help plus a page generated from the profile."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QListWidget, QListWidgetItem, QSplitter, QTextBrowser, QWidget

from .. import __version__
from ..protocol.profile import Profile

PROFILE_PAGE_TITLE = "Команды текущего профиля"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ") or "—"


def profile_markdown(profile: Profile) -> str:
    lines = [f"# {PROFILE_PAGE_TITLE}", ""]
    lines.append(f"**Профиль:** {profile.name}  ")
    if profile.description:
        lines.append(f"{profile.description}  ")
    lines.append(f"**Файл:** `{profile.source}`")
    lines += ["", "Эта страница собирается из файла профиля, поэтому всегда совпадает с тем, что знает программа.", ""]

    lines += ["## Команды", "", "| Команда | CODE | Параметры | Описание |", "|---|---|---|---|"]
    for spec in profile.commands:
        params = ", ".join(
            f"{p.name}: {p.type} ({p.min}…{p.max})" + (f" — {p.description}" if p.description else "")
            for p in spec.params
        )
        lines.append(f"| `{spec.name}` | 0x{spec.code:02X} | {_cell(params)} | {_cell(spec.description)} |")

    replies = [spec for spec in profile.commands if spec.reply]
    if replies:
        lines += ["", "## Разбор ответов (поле DATA)", ""]
        for spec in replies:
            fields = ", ".join(f"`{f.name}` ({f.type})" for f in spec.reply)
            lines.append(f"- **{spec.name}**: {fields}")

    lines += ["", "## Коды подтверждения (ACK)", "", "| ACK | Значение |", "|---|---|"]
    for code, text in sorted(profile.acks.items()):
        lines.append(f"| {code} | {_cell(text)} |")

    packet = profile.packet
    link = profile.link
    drive = profile.drive
    mode = "скорость + угол поворота" if drive.mode == "speed_angle" else "левый / правый борт"
    lines += [
        "",
        "## Пакет и связь",
        "",
        f"- Синхрослово: `{packet.sync.hex(' ').upper()}`, адрес: {packet.address}, "
        f"SQN по модулю {packet.sqn_modulo}, порядок байт: {profile.byte_order}",
        f"- CRC: {packet.crc.name} (poly 0x{packet.crc.poly:02X}, init 0x{packet.crc.init:02X}, "
        f'отражение {"да" if packet.crc.refin else "нет"}); контроль "123456789" = 0x{packet.crc.check:02X}',
        f"- Скорость UART: {link.baudrate} бод; ожидание ответа: "
        + (
            f"{link.reply_timeout_ms} мс, повторов: {link.retries} через {link.retry_interval_ms} мс"
            if link.wait_for_reply
            else "выключено"
        ),
        f"- Опрос связи: `{link.keepalive_command or '—'}` каждые {link.keepalive_period_ms} мс",
        "",
        "## Движение",
        "",
        f"- Схема: {mode}",
        f"- При движении: {', '.join(f'`{t}`' for t in drive.command) or '—'}",
        f"- Стоп: {', '.join(f'`{t}`' for t in drive.stop) or '—'}; тормоз в макросах: "
        + (", ".join(f"`{t}`" for t in drive.brake) or "—"),
        f"- Максимумы: скорость {drive.max_speed}, угол {drive.max_angle}",
        f"- Команда движения — не чаще раза в {drive.min_interval_ms} мс, повтор при езде каждые {drive.resend_ms} мс",
        "",
        "## Клавиши",
        "",
        f"- Вперёд **{profile.keys.forward}**, назад **{profile.keys.backward}**, "
        f"влево **{profile.keys.left}**, вправо **{profile.keys.right}**, стоп **{profile.keys.stop}** и **Esc**",
        f"- Геймпад: газ — {profile.gamepad.throttle}, руль — {profile.gamepad.steering}, "
        f"стоп — кнопка {profile.gamepad.stop_button}",
    ]
    return "\n".join(lines)


class HelpWindow(QDialog):
    def __init__(self, help_dir: Path, profile: Profile, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Справка — RC Controller {__version__}")
        self.resize(1000, 700)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        self.pages = QListWidget()
        self.pages.setMaximumWidth(260)
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        self.pages.currentItemChanged.connect(self._show)

        splitter = QSplitter()
        splitter.addWidget(self.pages)
        splitter.addWidget(self.browser)
        splitter.setStretchFactor(1, 1)
        layout = QHBoxLayout(self)
        layout.addWidget(splitter)

        self._help_dir = help_dir
        self._profile = profile
        self._load()

    def set_profile(self, profile: Profile) -> None:
        self._profile = profile
        current = self.pages.currentRow()
        self._load()
        self.pages.setCurrentRow(max(0, current))

    def show_page(self, title_prefix: str) -> None:
        for i in range(self.pages.count()):
            if self.pages.item(i).text().startswith(title_prefix):
                self.pages.setCurrentRow(i)
                return

    def _load(self) -> None:
        self.pages.blockSignals(True)
        self.pages.clear()
        for path in sorted(self._help_dir.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            first = text.lstrip().splitlines()[0] if text.strip() else path.stem
            item = QListWidgetItem(first.lstrip("# ").strip())
            item.setData(Qt.ItemDataRole.UserRole, text)
            self.pages.addItem(item)
        item = QListWidgetItem(PROFILE_PAGE_TITLE)
        item.setData(Qt.ItemDataRole.UserRole, profile_markdown(self._profile))
        self.pages.addItem(item)
        self.pages.blockSignals(False)
        self.pages.setCurrentRow(0)
        self._show(self.pages.currentItem())

    def _show(self, item: QListWidgetItem | None) -> None:
        if item is not None:
            self.browser.setMarkdown(item.data(Qt.ItemDataRole.UserRole))
