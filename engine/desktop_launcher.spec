# PyInstaller spec for the packaged desktop app - Windows and Linux both
# build from this exact file (desktop_launcher.py touches nothing OS-specific).
# Build (from engine/, with requirements.txt + pyinstaller installed):
#     pyinstaller desktop_launcher.spec
# Produces dist/Interpreter/Interpreter(.exe on Windows) (a folder build, not
# --onefile - the ML dependencies are large enough that a onefile build would
# be slow to unpack on every launch). See .github/workflows/build-windows.yml
# and build-linux.yml for the CI builds that produce the downloadable
# artifacts (PyInstaller doesn't cross-compile, so each needs its own
# native runner).
#
# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all

datas = [
    ("app/static", "app/static"),
    ("../config.yaml", "."),
    ("../glossary.yaml", "."),
]
binaries = []
hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
]

# These packages do dynamic imports / ship native binaries or data files that
# PyInstaller's static analysis won't find on its own.
for pkg in ("faster_whisper", "ctranslate2", "onnxruntime", "kokoro_onnx", "piper", "silero_vad"):
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports

a = Analysis(
    ["desktop_launcher.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Interpreter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # keep a console window so users can see engine logs/errors
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Interpreter",
)
