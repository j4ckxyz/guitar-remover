"""Render the app icon to packaging/build/icon.png (PyInstaller converts it to
.icns / .ico with Pillow)."""
import os
import sys
from pathlib import Path

# No display needed (CI machines don't have one).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from PySide6.QtGui import QGuiApplication  # noqa: E402

app = QGuiApplication(sys.argv[:1])
from guitar_remover.ui.icon import render  # noqa: E402

out = Path(__file__).parent / "build"
out.mkdir(exist_ok=True)
render(1024).save(str(out / "icon.png"))
print(out / "icon.png")
