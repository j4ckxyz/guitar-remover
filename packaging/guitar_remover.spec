# PyInstaller spec for macOS (.app), Windows (folder with GuitarRemover.exe) and
# Linux (folder with GuitarRemover). Build with the packaging/build_* scripts.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
sys.path.insert(0, str(ROOT / "src"))
from guitar_remover import APP_NAME, __version__  # noqa: E402

ICON = str(ROOT / "packaging" / "build" / "icon.png")

hiddenimports = (
    collect_submodules("demucs", filter=lambda m: not m.startswith(("demucs.grids", "demucs.train",
                                                                     "demucs.solver", "demucs.evaluate"))) +
    ["huggingface_hub", "httpx", "yaml", "safetensors", "safetensors.torch", "soundfile",
     "sounddevice", "PySide6.QtDBus", "onnxruntime", "python_stretch"]
)
datas = (collect_data_files("demucs") + collect_data_files("imageio_ffmpeg") +
         collect_data_files("_sounddevice_data") + collect_data_files("onnxruntime") +
         # Our own data: the Basic Pitch model and its licence.
         [(str(p), "guitar_remover/data") for p in (ROOT / "src/guitar_remover/data").iterdir()])

# PortAudio ships inside the sounddevice wheel on macOS/Windows; on Linux bundle
# the system copy (apt install libportaudio2) so playback works out of the box.
binaries = []
if sys.platform.startswith("linux"):
    import glob
    for pattern in ("/usr/lib/*/libportaudio.so.2*", "/usr/lib64/libportaudio.so.2*",
                    "/usr/lib/libportaudio.so.2*"):
        for lib in glob.glob(pattern):
            if not Path(lib).is_symlink():
                binaries.append((lib, "."))
                break

# Things torch/demucs can pull in that we never use at runtime.
excludes = [
    "torchaudio", "torchvision", "tensorboard", "matplotlib", "scipy", "pandas", "IPython",
    "jupyter", "notebook", "tkinter", "sphn", "lameenc", "openunmix", "dora", "hydra",
    "torch.utils.tensorboard", "triton", "PySide6.QtWebEngineCore", "PySide6.QtQml",
    "PySide6.QtQuick", "PySide6.QtPdf", "PySide6.Qt3DCore", "PySide6.QtMultimedia",
    "pytest", "PyInstaller",
]

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="GuitarRemover",
    console=False,
    icon=ICON,
    upx=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=None,
)

coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="GuitarRemover")

if sys.platform == "darwin":
    audio_types = ["mp3", "wav", "flac", "m4a", "aac", "ogg", "opus", "aif", "aiff", "wma"]
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=ICON,
        bundle_identifier="app.guitarremover.GuitarRemover",
        version=__version__,
        info_plist={
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": __version__,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
            "LSApplicationCategoryType": "public.app-category.music",
            "NSRequiresAquaSystemAppearance": False,  # follow light/dark mode
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": "Audio",
                "CFBundleTypeRole": "Viewer",
                "LSHandlerRank": "Alternate",
                "LSItemContentTypes": ["public.audio", "public.movie"],
                "CFBundleTypeExtensions": audio_types,
            }],
        },
    )
