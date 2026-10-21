# -*- mode: python ; coding: utf-8 -*-
import os

# The Android app goes inside Motorcycle Man.exe, so phones can update it from the PC. Build it first with
# Android\build_apk.ps1, which signs every build with the same key.
phone_app = os.path.normpath(os.path.join(SPECPATH, '..', 'Android', 'MotorcycleMan.apk'))
datas = [('motorcycle_man.ico', '.'), ('motorcycle_man_icon.png', '.'), ('motorcycle_man_app_icon.png', '.')]
# version.json: the build number the GitHub build writes, which the app compares with its releases (app_update.py).
version_file = os.path.join(SPECPATH, 'version.json')
if os.path.isfile(version_file):
    datas.append((version_file, '.'))
if os.path.isfile(phone_app):
    datas.append((phone_app, '.'))
else:
    print('WARNING: ' + phone_app + ' is missing, so this Motorcycle Man.exe can\'t update the Android app.')

a = Analysis(
    ['motorcycle_man.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    # The UI Automation wrapper is imported inside a function; list it so it is bundled.
    hiddenimports=['comtypes.gen.UIAutomationClient', 'comtypes.gen._944DE083_8FB8_45CF_BCB7_C477ACB2F897_0_1_0'],
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
    name='Motorcycle Man',
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
    icon='motorcycle_man.ico',
)
