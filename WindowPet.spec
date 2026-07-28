# PyInstaller spec for the Windows build.
#
#   pyinstaller WindowPet.spec --noconfirm                  -> dist/WindowPet.exe
#   PET_ONEDIR=1 pyinstaller WindowPet.spec --noconfirm     -> dist/WindowPet/
#
# The one-file build is the convenient one. The one-folder build is the fallback:
# it does not unpack itself into %TEMP% at startup, which is the usual reason a
# one-file build dies on a machine where antivirus or a locked-down temp directory
# interferes with the extracted DLLs.
#
# Only the rig parts, rig.json and the icon are bundled. The original photo, the
# cached cutout and the asset-building tools are build-time material.

import os

onedir = os.environ.get("PET_ONEDIR") == "1"

datas = [
    ("assets/parts", "assets/parts"),
    ("assets/rig.json", "assets"),
    ("assets/icon.png", "assets"),
]

# Dances keyframed in tools/pose_editor.py, if there are any. Optional on purpose:
# the file only exists once somebody has made one, and a missing data file is a
# hard PyInstaller error rather than a warning.
if os.path.exists("assets/poses.json"):
    datas.append(("assets/poses.json", "assets"))

# The pet needs PySide6 and nothing else. numpy in particular must stay out: it
# was only used for 3x3 affine maths, and its C extensions failed to import inside
# the frozen exe on a real machine.
excludes = [
    "numpy",
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
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)

# UPX is off on purpose. Compressing Qt's DLLs and Python extension modules is a
# well known source of "failed to load" errors at startup, and it saves little.
common = dict(
    name="WindowPet",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # no console window: it is a desktop toy
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
)

if onedir:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **common)
    coll = COLLECT(
        exe, a.binaries, a.zipfiles, a.datas,
        strip=False, upx=False, upx_exclude=[], name="WindowPet",
    )
else:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
        upx_exclude=[], runtime_tmpdir=None, **common,
    )
