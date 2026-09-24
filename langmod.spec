# PyInstaller build of the standalone Langmod Manager, the copy to hand round.
#
#   python tools\release.py                       builds with this, checks it, and zips it
#   python -m PyInstaller --noconfirm langmod.spec   just the build
#
# The result is dist\Langmod Manager\Langmod Manager.exe. One folder rather than one file:
# a one-file build unpacks itself into a temporary folder at every start, which is slower
# and is what antivirus programs distrust most.
import re
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                 VarFileInfo, VarStruct, VSVersionInfo)

ROOT = Path(SPECPATH)
VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT / "langmod" / "__init__.py").read_text()).group(1)
NUMBERS = (tuple(int(n) for n in re.findall(r"\d+", VERSION)) + (0, 0, 0, 0))[:4]

a = Analysis(
    ["launcher.py"],
    pathex=[str(ROOT)],
    binaries=[],
    # The icons, for the window and for the shortcuts it makes.
    datas=[("langmod/resources", "langmod/resources")],
    hiddenimports=["compression.zstd", "PySide6.QtSvg"],
    hookspath=[],
    runtime_hooks=[],
    # Qt modules the manager never touches; left to themselves some are pulled in anyway.
    excludes=[
        "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
        "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.Qt3DCore",
        "PySide6.Qt3DRender", "PySide6.QtCharts", "PySide6.QtDataVisualization",
        "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning", "PySide6.QtSensors",
        "PySide6.QtSerialPort", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner",
        "PySide6.QtHelp", "PySide6.QtUiTools", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
        "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtNetworkAuth", "PySide6.QtRemoteObjects",
        "PySide6.QtScxml", "PySide6.QtSpatialAudio", "PySide6.QtStateMachine", "PySide6.QtTextToSpeech",
        "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtNetwork",
        "tkinter", "unittest", "pydoc_data", "PIL", "numpy",
    ],
    noarchive=False,
)

# What Qt brings along that a widgets window with no OpenGL, PDF, QML or on-screen keyboard
# never loads, about half the build: the software OpenGL fallback, the image and input
# plugins that pull in Qt Quick, QML and PDF, and Qt's own translations but for the five
# languages the manager speaks besides English (Qt's buttons and file dialogs then speak them
# too). The image plugins kept read icons, SVG and the pictures a player may choose
# for Your picture (JPEG, WebP, GIF; PNG and BMP are built in). tools\release.py checks that
# every DLL the rest imports is still there.
DROP = re.compile(r"(^|[\\/])(opengl32sw\.dll"
                  r"|Qt6(Quick|Qml|QmlModels|QmlMeta|QmlWorkerScript|Pdf|VirtualKeyboard|OpenGL|Network)\.dll"
                  r"|plugins[\\/](platforminputcontexts|generic)[\\/][^\\/]+"
                  r"|plugins[\\/]imageformats[\\/](?!q(ico|svg|jpeg|webp|gif)\.dll$)[^\\/]+"
                  r"|plugins[\\/]platforms[\\/]q(direct2d|minimal)\.dll"
                  r"|translations[\\/](?!qtbase_(de|fr|pl|ru|zh_CN)\.qm$)[^\\/]+)$", re.I)
a.binaries = [b for b in a.binaries if not DROP.search(b[0])]
a.datas = [d for d in a.datas if not DROP.search(d[0])]

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Langmod Manager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # a window, not a console; the command line borrows the prompt's
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="langmod/resources/icon-standard.ico",
    version=VSVersionInfo(
        ffi=FixedFileInfo(filevers=NUMBERS, prodvers=NUMBERS),
        kids=[StringFileInfo([StringTable("040904B0", [
                  StringStruct("FileDescription", "Langmod Manager"),
                  StringStruct("ProductName", "Langmod Manager"),
                  StringStruct("InternalName", "Langmod Manager"),
                  StringStruct("OriginalFilename", "Langmod Manager.exe"),
                  StringStruct("FileVersion", VERSION),
                  StringStruct("ProductVersion", VERSION),
                  StringStruct("Comments", "Load several War Thunder language mods at once"),
              ])]),
              VarFileInfo([VarStruct("Translation", [0x0409, 1200])])],
    ),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Langmod Manager",
)
