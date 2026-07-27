# PyInstaller spec for the one-file Windows build.
#
#   pyinstaller WindowPet.spec --noconfirm
#
# Only the rig parts, rig.json and the icon are bundled. The original photo, the
# cached cutout and the asset-building tools are build-time material and are left
# out, which keeps the exe considerably smaller.

block_cipher = None

datas = [
    ("assets/parts", "assets/parts"),
    ("assets/rig.json", "assets"),
    ("assets/icon.png", "assets"),
]

# Nothing here is needed at runtime: the pet only needs PySide6 + numpy.
excludes = [
    "scipy",
    "PIL",
    "rembg",
    "onnxruntime",
    "matplotlib",
    "tkinter",
    "pytest",
    "setuptools",
    "pip",
    "PySide6.QtNetwork",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.Qt3DCore",
    "PySide6.QtMultimedia",
    "PySide6.QtWebEngineCore",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtPdf",
    "PySide6.QtDesigner",
    "PySide6.QtCharts",
]

a = Analysis(
    ["run_pet.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="WindowPet",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # no console window: it is a desktop toy
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
)
