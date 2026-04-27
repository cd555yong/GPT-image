# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

import tkinterdnd2


project_root = Path(__file__).resolve().parent
tkdnd_dir = Path(tkinterdnd2.__file__).resolve().parent / "tkdnd"
tkdnd_datas = []
if tkdnd_dir.exists():
    tkdnd_datas.append((str(tkdnd_dir), "tkinterdnd2/tkdnd"))

a = Analysis(
    [str(project_root / "gpt_image_app.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=tkdnd_datas,
    hiddenimports=[
        "tkinterdnd2",
        "PIL._tkinter_finder",
        "httpx",
        "h11",
        "anyio",
        "httpcore",
        "sniffio",
        "certifi",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="GPT图片生成器",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
