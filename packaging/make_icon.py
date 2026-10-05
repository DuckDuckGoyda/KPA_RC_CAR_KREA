"""Render the application icon to packaging/app.ico for the exe."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from rc_controller.gui.widgets import app_icon  # noqa: E402

app = QApplication(sys.argv)
target = ROOT / "packaging" / "app.ico"
if not app_icon().pixmap(256, 256).save(str(target), "ICO"):
    sys.exit(f"could not write {target}")
print(f"icon written to {target}")
