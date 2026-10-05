"""Writes every log entry of a run to a CSV file.

The protocol asks for all exchange data to be handed to the developer when
something goes wrong, so this is on by default. The file uses ';' and a BOM
so that Excel with Russian regional settings opens it correctly.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import IO

from .link import LogEntry

DIRECTIONS = {"tx": "→", "rx": "←", "event": "•"}
HEADER = ["время", "направление", "источник", "тип", "SQN", "содержание", "байты", "статус", "RTT, мс"]


def entry_row(entry: LogEntry) -> list[str]:
    return [
        entry.time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        DIRECTIONS.get(entry.direction, entry.direction),
        entry.source,
        entry.kind,
        "" if entry.sqn is None else str(entry.sqn),
        entry.text,
        entry.data.hex(" ").upper(),
        entry.status,
        "" if entry.rtt_ms is None else f"{entry.rtt_ms:.1f}".replace(".", ","),
    ]


def write_csv(path: Path, entries: list[LogEntry]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(HEADER)
        writer.writerows(entry_row(e) for e in entries)


class SessionLogger:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.enabled = True
        self.path: Path | None = None
        self._fh: IO[str] | None = None
        self._writer: csv._writer | None = None  # type: ignore[name-defined]

    def write(self, entry: LogEntry) -> None:
        if not self.enabled:
            return
        if self._writer is None:
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                self.path = self.directory / f"session_{datetime.now():%Y-%m-%d_%H-%M-%S}.csv"
                self._fh = self.path.open("w", encoding="utf-8-sig", newline="")
            except OSError:
                self.enabled = False  # read-only folder etc.; the in-app log still works
                return
            self._writer = csv.writer(self._fh, delimiter=";")
            self._writer.writerow(HEADER)
        self._writer.writerow(entry_row(entry))
        assert self._fh is not None
        self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
        self._fh = None
        self._writer = None
