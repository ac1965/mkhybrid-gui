# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller用ビルド定義。

Qtプラグイン（platforms/libqcocoa.dylib等）の取りこぼしを避けるため、
PySide6用のフックが有効な状態でビルドすること:

    pyinstaller mkhybrid_gui.spec

ビルド後、生成された .app の
``Contents/MacOS/PySide6/Qt/plugins/platforms/libqcocoa.dylib``
（バージョンによりパスは異なる）が同梱されているか必ず確認する。
"""

block_cipher = None

a = Analysis(
    ["src/mkhybrid_gui/app.py"],
    pathex=["src"],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="mkhybrid-gui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="mkhybrid-gui",
)

app = BUNDLE(
    coll,
    name="mkhybrid-gui.app",
    icon=None,
    bundle_identifier="net.ty07.mkhybrid-gui",
    info_plist={
        "CFBundleName": "mkhybrid-gui",
        "CFBundleShortVersionString": "0.1.0",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "12.0",
    },
)
