"""Drive the real UI: add a song, wait for it to finish, screenshot each stage.

usage: python tests/ui_smoke.py SONG OUT_DIR [light|dark]
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import QCoreApplication, Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

song, out = Path(sys.argv[1]), Path(sys.argv[2])
scheme = sys.argv[3] if len(sys.argv) > 3 else "light"
out.mkdir(parents=True, exist_ok=True)

QCoreApplication.setOrganizationName("GuitarRemoverTest")
QCoreApplication.setApplicationName("Guitar Remover Test")
app = QApplication(sys.argv)
app.styleHints().setColorScheme(Qt.ColorScheme.Dark if scheme == "dark" else Qt.ColorScheme.Light)

from guitar_remover.app import _apply_platform_style  # noqa: E402
from guitar_remover.settings import Settings  # noqa: E402
from guitar_remover.ui.icon import app_icon  # noqa: E402
from guitar_remover.ui.main_window import MainWindow  # noqa: E402
from guitar_remover.ui.settings_dialog import SettingsDialog  # noqa: E402

_apply_platform_style(app)
app.setWindowIcon(app_icon())
s = Settings()
s.reset()
s.set("models_dir", os.environ.get("MODELS_DIR", ""))
s.set("output_dir", str(out / "tracks"))
s.set("quality", int(os.environ.get("QUALITY", "1")))
win = MainWindow(s)
win.show()
t0 = time.monotonic()


def shot(name):
    win.grab().save(str(out / f"{scheme}-{name}.png"))
    print(f"[{time.monotonic() - t0:5.1f}s] shot {name}", flush=True)


state = {"stage": 0, "running_shot": False}


def tick():
    st = state["stage"]
    if st == 0 and win.hardware is not None:
        shot("1-empty")
        dlg = SettingsDialog(s, win.hardware, win)
        dlg.show()
        from PySide6.QtWidgets import QTabWidget
        tw = dlg.findChild(QTabWidget)
        for i in range(tw.count()):
            tw.setCurrentIndex(i)
            app.processEvents()
            dlg.grab().save(str(out / f"{scheme}-settings-{i}.png"))
        dlg.close()
        win.add_files([song])
        state["stage"] = 1
    elif st == 1:
        rows = list(win.rows.values())
        r = rows[0]
        if r.state == "running" and "Separating" in r.status.text() and r.bar.value() > 300 \
                and not state["running_shot"]:
            shot("2-running")
            state["running_shot"] = True
        if r.state in ("done", "failed", "cancelled"):
            print("row:", r.state, r.status.text().replace("\n", " | "), flush=True)
            shot("3-done")
            state["stage"] = 2
    elif st == 2 and win.players:
        pw = win.players[0]
        pw.resize(1000, 460)
        app.processEvents()
        pw.grab().save(str(out / f"{scheme}-4-player.png"))
        print("player tracks:", [t.name for t in pw.tracks], flush=True)
        # Select 60-70 s, mute the guitar, turn backing to 80%, zoom in.
        sr = pw.player.samplerate
        pw.timeline.selection = (60 * sr, 70 * sr)
        pw.timeline.selection_changed.emit(pw.timeline.selection)
        pw.loop_btn.setChecked(True)
        pw.headers[0].mute.setChecked(True)
        pw.headers[1].vol.setValue(80)
        pw.timeline.zoom(3, pw.timeline.frame_to_x(65 * sr))
        # Play silently to exercise the real audio stream.
        pw.master.setValue(0)
        pw.player.seek(int(68.5 * sr))
        pw.toggle()
        state["play_t"] = time.monotonic()
        state["stage"] = 3
    elif st == 3 and time.monotonic() - state["play_t"] > 1.5:
        pw = win.players[0]
        sr = pw.player.samplerate
        pos = pw.player.position / sr
        print(f"playing={pw.player.playing} pos={pos:.2f}s (loop 60-70, started 68.5) "
              f"err={pw.player.error}", flush=True)
        app.processEvents()
        pw.grab().save(str(out / f"{scheme}-5-player-loop.png"))
        pw.toggle()
        pw.close()
        state["stage"] = 4
        QTimer.singleShot(300, win.close)
        QTimer.singleShot(1500, app.quit)
    if time.monotonic() - t0 > 400:
        print("TIMEOUT", flush=True)
        app.quit()


timer = QTimer()
timer.timeout.connect(tick)
timer.start(200)
code = app.exec()
print("exit", code)
