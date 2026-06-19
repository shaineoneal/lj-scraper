# -*- mode: python ; coding: utf-8 -*-
import os
import importlib.util

# Dynamically locate the playwright driver path in a cross-platform manner
playwright_spec = importlib.util.find_spec('playwright')
if playwright_spec and playwright_spec.submodule_search_locations:
    driver_dir = os.path.join(playwright_spec.submodule_search_locations[0], 'driver')
else:
    # Fallback if package lookup fails
    driver_dir = os.path.join('.venv', 'Lib' if os.name == 'nt' else 'lib', 'site-packages', 'playwright', 'driver')

a = Analysis(
    ['lj_scraper.py'],
    pathex=[],
    binaries=[],
    datas=[
        (driver_dir, 'playwright/driver'),
        ('ms-playwright', 'ms-playwright')
    ],
    hiddenimports=[],
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
    name='lj_scraper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
