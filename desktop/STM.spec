# PyInstaller recipe for STM.exe. Build from the repo root:
#     pyinstaller desktop/STM.spec --noconfirm
# The result is dist/STM/ -- a folder with STM.exe and what it needs. A
# folder (not one self-extracting file) so it opens straight away instead of
# unpacking itself to a temp folder on every launch.
import os

HERE = SPECPATH
ROOT = os.path.dirname(HERE)

a = Analysis(
    [os.path.join(HERE, "stm_desktop.py")],
    pathex=[ROOT],
    datas=[
        (os.path.join(ROOT, "app", "templates"), os.path.join("app", "templates")),
        (os.path.join(ROOT, "app", "static"), os.path.join("app", "static")),
        (os.path.join(HERE, "templates"), os.path.join("desktop", "templates")),
    ],
    # Imported inside functions, where PyInstaller can't follow.
    hiddenimports=["desktop.core", "desktop.engine", "desktop.views", "desktop.local_models", "clr"],
    # Profile pictures are only resized on the website, so the PC doesn't
    # need the imaging library.
    excludes=["PIL", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="STM",
    icon=os.path.join(HERE, "stm.ico"),
    version=os.path.join(HERE, "version_info.txt"),
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="STM", upx=False)
