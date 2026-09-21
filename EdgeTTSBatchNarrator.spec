# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

playwright_data, playwright_bins, playwright_hidden = collect_all('playwright')
edge_data, edge_bins, edge_hidden = collect_all('edge_tts')

a = Analysis(
    ['src/app.py'],
    pathex=['src'],
    binaries=playwright_bins + edge_bins,
    datas=[
        ('assets/app.ico', 'assets'),
        ('assets/app.png', 'assets'),
    ] + playwright_data + edge_data,
    hiddenimports=playwright_hidden + edge_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='EdgeTTSBatchNarrator-v1.0.0-win64',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/app.ico',
    version='build/version_info.txt',
)
