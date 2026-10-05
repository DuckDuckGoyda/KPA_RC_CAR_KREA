# PyInstaller spec: one windowed RC_Controller.exe.
# Build from the repository root:  pyinstaller --noconfirm packaging/rc_controller.spec
# (build_exe.bat does this, including tests and the icon.)

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
ICON = ROOT / "packaging" / "app.ico"

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "rc_controller" / "resources"), "rc_controller/resources")],
    # pyserial imports its URL handlers (loop://, socket://) by name at runtime.
    hiddenimports=collect_submodules("serial.urlhandler"),
    excludes=["tkinter", "unittest", "pytest", "pytestqt"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="RC_Controller",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ICON) if ICON.exists() else None,
)
