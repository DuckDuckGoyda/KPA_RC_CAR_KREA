from rc_controller.ports import KIND_BLUETOOTH, KIND_LOOP, KIND_OTHER, KIND_USB, classify

BT_OUT = r"BTHENUM\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002\7&2B5F5A1E&0&98D331F5B1C2_C00000000"
BT_IN = r"BTHENUM\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0000\7&2B5F5A1E&0&000000000000_00000000"


def test_bluetooth_outgoing_port():
    port = classify("COM5", "Standard Serial over Bluetooth link (COM5)", BT_OUT)
    assert port.kind == KIND_BLUETOOTH
    assert port.bt_address == "98:D3:31:F5:B1:C2"
    assert not port.bt_incoming
    assert port.label == "COM5 · Bluetooth 98:D3:31:F5:B1:C2 · Standard Serial over Bluetooth link"


def test_bluetooth_incoming_port_is_flagged():
    port = classify("COM6", "Стандартный последовательный порт по соединению Bluetooth (COM6)", BT_IN)
    assert port.kind == KIND_BLUETOOTH
    assert port.bt_incoming
    assert "входящий" in port.label


def test_usb_port():
    port = classify("COM3", "USB-SERIAL CH340 (COM3)", "USB VID:PID=1A86:7523 SER= LOCATION=1-2", vid=0x1A86)
    assert port.kind == KIND_USB
    assert port.label == "COM3 · USB · USB-SERIAL CH340"


def test_urls_and_unknown():
    assert classify("loop://").kind == KIND_LOOP
    assert classify("COM1", "Communications Port (COM1)", "ACPI\\PNP0501\\1").kind == KIND_OTHER
