# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('C:/Program Files/Tesseract-OCR', 'tesseract'),
        ('tesseract/por.traineddata', 'tesseract/tessdata'),
        ('assets/icone_controle_contas.ico', 'assets'),
        ('assets/icone_controle_contas.png', 'assets'),
        ('assets/ui_icons', 'assets/ui_icons'),
    ],
    hiddenimports=[
        'pypdf',
        'fitz',
        'pymupdf',
        'pytesseract',
        'cv2',
        'numpy',
        'PySide6.QtSvg',
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
    [],
    exclude_binaries=True,
    name='Controle de Contas',
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
    version='version_info_1.4.5.txt',
    icon='assets/icone_controle_contas.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Controle de Contas',
)
