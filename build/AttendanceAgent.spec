# -*- mode: python ; coding: utf-8 -*-
"""
AttendanceAgent.spec — PyInstaller build spec.

Build from the repo root with:
  pyinstaller build/AttendanceAgent.spec --distpath dist --workpath build/work

The resulting dist/AttendanceAgent.exe is a single-file, no-console Windows
executable.  Models are NOT bundled — they are downloaded on first run.
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent  # attendance-agent/ root

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # Include all wizard / service / sync / scheduler / config packages
        (str(ROOT / "setup_wizard"), "setup_wizard"),
        (str(ROOT / "service"),      "service"),
        (str(ROOT / "sync"),         "sync"),
        (str(ROOT / "scheduler"),    "scheduler"),
        (str(ROOT / "config"),       "config"),
    ],
    hiddenimports=[
        # customtkinter bundles its own theme JSON — must be declared
        "customtkinter",
        "customtkinter.windows",
        "customtkinter.windows.widgets",
        "PIL",
        "PIL._tkinter_finder",
        # cryptography backend
        "cryptography",
        "cryptography.hazmat.primitives.kdf.pbkdf2",
        "cryptography.hazmat.backends.openssl",
        # requests TLS support
        "requests",
        "urllib3",
        "certifi",
        # websockets
        "websockets",
        "websockets.legacy",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude heavy packages not needed at runtime
        "matplotlib",
        "scipy",
        "pandas",
        "IPython",
        "jupyter",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="AttendanceAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,        # no terminal window — GUI only
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,            # TODO: add icon.ico here
    onefile=True,
)
