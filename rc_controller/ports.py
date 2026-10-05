"""Serial port discovery and USB / Bluetooth classification.

On Windows a paired HC-05 shows up as a Bluetooth virtual COM port whose
hardware id starts with ``BTHENUM``. Windows usually creates two of them:
an *outgoing* one (the hwid carries the module's MAC address; this is the one
to open) and an *incoming* one (all-zero address; opening it just hangs).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

KIND_USB = "usb"
KIND_BLUETOOTH = "bluetooth"
KIND_LOOP = "loop"
KIND_NETWORK = "network"
KIND_OTHER = "other"

KIND_LABELS = {
    KIND_USB: "USB",
    KIND_BLUETOOTH: "Bluetooth",
    KIND_LOOP: "Петля",
    KIND_NETWORK: "Сеть",
    KIND_OTHER: "Порт",
}

_BT_ADDRESS = re.compile(r"&([0-9A-F]{12})_")


@dataclass(frozen=True)
class PortInfo:
    device: str
    description: str = ""
    hwid: str = ""
    kind: str = KIND_OTHER
    bt_address: str | None = None  # "98:D3:31:F5:B1:C2" for an outgoing BT port

    @property
    def bt_incoming(self) -> bool:
        return self.kind == KIND_BLUETOOTH and self.bt_address is None and "BTHENUM" in self.hwid.upper()

    @property
    def label(self) -> str:
        kind = KIND_LABELS[self.kind]
        if self.kind == KIND_BLUETOOTH:
            if self.bt_address:
                kind += f" {self.bt_address}"
            elif self.bt_incoming:
                kind += ", входящий — не подойдёт"
        description = self.description
        # Windows puts "(COM5)" at the end of the description; it is redundant here.
        if description.endswith(f"({self.device})"):
            description = description[: -len(self.device) - 2].rstrip()
        if description and description != "n/a":
            return f"{self.device} · {kind} · {description}"
        return f"{self.device} · {kind}"


def classify(device: str, description: str = "", hwid: str = "", vid: int | None = None) -> PortInfo:
    hw = (hwid or "").upper()
    desc = (description or "").lower()
    dev = device.lower()

    if dev.startswith("loop://"):
        return PortInfo(device, description, hwid, KIND_LOOP)
    if dev.startswith(("socket://", "rfc2217://")):
        return PortInfo(device, description, hwid, KIND_NETWORK)

    if "BTHENUM" in hw or "bluetooth" in desc or "rfcomm" in dev:
        address = None
        match = _BT_ADDRESS.search(hw)
        if match and match.group(1) != "0" * 12:
            raw = match.group(1)
            address = ":".join(raw[i : i + 2] for i in range(0, 12, 2))
        return PortInfo(device, description, hwid, KIND_BLUETOOTH, address)

    if vid is not None or hw.startswith("USB") or "USB" in hw:
        return PortInfo(device, description, hwid, KIND_USB)

    return PortInfo(device, description, hwid, KIND_OTHER)


def _sort_key(port: PortInfo) -> tuple[int, int, str]:
    order = {KIND_BLUETOOTH: 0, KIND_USB: 1, KIND_OTHER: 2}.get(port.kind, 3)
    if port.bt_incoming:
        order = 4
    digits = "".join(ch for ch in port.device if ch.isdigit())
    return order, int(digits) if digits else 0, port.device


def list_ports() -> list[PortInfo]:
    from serial.tools import list_ports as serial_list_ports

    ports = [classify(p.device, p.description or "", p.hwid or "", p.vid) for p in serial_list_ports.comports()]
    return sorted(ports, key=_sort_key)
