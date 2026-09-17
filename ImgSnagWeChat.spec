# -*- mode: python ; coding: utf-8 -*-
# ImgSnag 微信公众号版 打包配置（onedir 模式，与 ChemCal 一致）
# - onedir：不再解压到 %TEMP%，启动快、杀软误报少；分发用 Inno Setup 或便携 zip
# - 无 playwright / 无 Chromium：体积相比旧版通用 ImgSnag 大幅缩小

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('version.py', '.'), ('ImgSnag.ico', '.'), ('ImgSnag.png', '.')],
    hiddenimports=[
        'PySide6.QtCore', 'PySide6.QtWidgets', 'PySide6.QtGui',
        'sqlite3', 'hashlib', 're', 'json', 'os', 'sys',
        'requests', 'urllib3', 'certifi', 'idna', 'charset_normalizer',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 微信专精版用不到的重量级依赖，一律排除（防止误带入）
    excludes=[
        'PyQt5', 'PyQt6', 'PySide2', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineCore',
        'PySide6.Qt3DCore', 'PySide6.QtMultimedia', 'PySide6.QtQuick', 'PySide6.QtQml',
        'PySide6.QtSql', 'PySide6.QtTest', 'PySide6.QtDesigner', 'PySide6.QtCharts',
        'PySide6.QtDataVisualization', 'PySide6.QtBluetooth', 'PySide6.QtNfc',
        'scipy', 'pandas', 'matplotlib', 'numpy', 'tkinter', 'unittest', 'pytest',
        'playwright', 'PIL', 'lxml', 'bs4',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # onedir：二进制交给 COLLECT，不塞进 exe
    name='ImgSnagWeChat',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['ImgSnag.ico'],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ImgSnagWeChat',
)
