"""Application entry point."""
from __future__ import annotations

import os
import sys

# Must be set before torch is imported anywhere: lets rare ops that Metal lacks
# fall back to the CPU instead of failing.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


def _apply_platform_style(app) -> None:
    """Use each platform's native look, including automatic light/dark mode.

    macOS: the native style follows the system appearance by itself.
    Windows 11: Qt's "windows11" style is native and supports dark mode.
    Windows 10: the classic native style has no dark mode, so fall back to
    Fusion, which follows the system colour scheme and accent colour.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QStyleFactory

    if sys.platform == "win32":
        styles = [s.lower() for s in QStyleFactory.keys()]
        current = app.style().name().lower()
        if current != "windows11":
            if "windows11" in styles and sys.getwindowsversion().build >= 22000:
                app.setStyle("windows11")
            elif app.styleHints().colorScheme() == Qt.ColorScheme.Dark:
                app.setStyle("Fusion")

        def follow(scheme):
            if app.style().name().lower() in ("windowsvista", "fusion"):
                app.setStyle("Fusion" if scheme == Qt.ColorScheme.Dark else "windowsvista")
        app.styleHints().colorSchemeChanged.connect(follow)
    elif sys.platform.startswith("linux"):
        app.setStyle("Fusion")


def selftest(song: str, out_dir: str) -> int:
    """Headless check that the (possibly frozen) app can really separate audio:
    ``GuitarRemover --selftest SONG OUT_DIR``."""
    import threading
    from pathlib import Path

    from PySide6.QtCore import QCoreApplication

    from . import APP_NAME, ORG_NAME, hardware
    from .separator import JobConfig, Separator
    from .settings import Settings

    app = QCoreApplication(sys.argv[:1])  # needed for QStandardPaths/QSettings
    app.setOrganizationName(ORG_NAME)
    app.setApplicationName(APP_NAME)
    hw = hardware.detect()
    plan = hardware.plan_resources(hw)
    print(hw.summary, "->", plan.device, flush=True)
    sep = Separator(Settings().models_dir)
    last = [""]

    def progress(stage, frac):
        if stage != last[0]:
            print(stage, flush=True)
            last[0] = stage

    res = sep.run(Path(song), JobConfig(shifts=0, overlap=0.1, output_dir=Path(out_dir)),
                  plan, progress, threading.Event())
    print(f"OK {res.guitar}\nOK {res.backing}\n"
          f"{res.audio_seconds / res.seconds:.1f}x realtime on {res.device}", flush=True)
    try:  # playback library present and able to see an output device?
        import sounddevice as sd
        print("Audio output:", sd.query_devices(kind="output")["name"], flush=True)
    except Exception as exc:  # noqa: BLE001
        print("Audio output unavailable:", exc, flush=True)
    return 0


def export_icon(path: str) -> int:
    """Write the app icon as a 1024 px PNG (used by the installers)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # works without a display
    from PySide6.QtGui import QGuiApplication

    _app = QGuiApplication(sys.argv[:1])
    from .ui.icon import render
    ok = render(1024).save(path)
    del _app
    return 0 if ok else 1


def main() -> int:
    if len(sys.argv) >= 4 and sys.argv[1] == "--selftest":
        return selftest(sys.argv[2], sys.argv[3])
    if len(sys.argv) >= 3 and sys.argv[1] == "--export-icon":
        return export_icon(sys.argv[2])
    if sys.platform.startswith("linux"):
        # Lets Wayland compositors match the window to guitar-remover.desktop (icon).
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.setDesktopFileName("guitar-remover")

    from PySide6.QtCore import QCoreApplication, QEvent, Qt
    from PySide6.QtWidgets import QApplication

    from . import APP_ID, APP_NAME, ORG_NAME, __version__

    if sys.platform == "win32":
        try:  # own taskbar icon/group instead of python.exe's
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{ORG_NAME}.{APP_ID}")
        except Exception:
            pass

    QCoreApplication.setOrganizationName(ORG_NAME)
    QCoreApplication.setOrganizationDomain("guitarremover.app")
    QCoreApplication.setApplicationName(APP_NAME)
    QCoreApplication.setApplicationVersion(__version__)
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    class App(QApplication):
        """Handles files dropped on the Dock icon / "Open With" on macOS."""
        window = None
        pending: list = []

        def event(self, e):
            if e.type() == QEvent.Type.FileOpen:
                from pathlib import Path
                path = Path(e.file())
                if self.window is not None:
                    self.window.add_files([path])
                else:
                    self.pending.append(path)
                return True
            return super().event(e)

    app = App(sys.argv)
    app.setApplicationDisplayName(APP_NAME)
    _apply_platform_style(app)

    from .settings import Settings
    from .ui.icon import app_icon
    from .ui.main_window import MainWindow

    app.setWindowIcon(app_icon())
    win = MainWindow(Settings())
    win.show()
    app.window = win

    # Songs passed on the command line (or via "Open With" on Windows).
    from pathlib import Path
    files = app.pending + [Path(a) for a in sys.argv[1:] if Path(a).is_file()]
    if files:
        win.add_files(files)
    return app.exec()
