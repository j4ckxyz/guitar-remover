"""Drive the player's practice tools (silently) and save screenshots.

usage: python tests/player_smoke.py GUITAR.wav BACKING.wav OUT_DIR [light|dark]
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from PySide6.QtCore import QCoreApplication, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

guitar, backing, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
scheme = sys.argv[4] if len(sys.argv) > 4 else "light"
out.mkdir(parents=True, exist_ok=True)
QCoreApplication.setOrganizationName("GuitarRemoverTest")
QCoreApplication.setApplicationName("Guitar Remover Test")
app = QApplication(sys.argv)
app.styleHints().setColorScheme(Qt.ColorScheme.Dark if scheme == "dark" else Qt.ColorScheme.Light)
from guitar_remover.songdata import SongData  # noqa: E402
from guitar_remover.ui.player import PlayerWindow  # noqa: E402

SongData(guitar).path.unlink(missing_ok=True)  # start fresh


def wait(cond, timeout=20.0):
    t0 = time.monotonic()
    while not cond() and time.monotonic() - t0 < timeout:
        app.processEvents()
        time.sleep(0.02)
    return cond()


def open_player():
    w = PlayerWindow([("Guitar", guitar), ("Backing Track", backing)], guitar.stem.split(" - ")[0])
    w.resize(1180, 520)
    w.show()
    return w


w = open_player()
assert wait(lambda: w.timeline.tempo is not None), "tempo not detected"
tp = w.timeline.tempo
print(f"tempo {tp.bpm:.1f} BPM, {len(tp.beats)} beats, bar offset {tp.bar_offset}")
w._edit_tempo(scale=2.0)
print("after x2:", w.tempo_btn.text())

# Snapping: a rough drag lands on bar lines.
sr = w.player.samplerate
a, b = w.timeline._snap(int(58.3 * sr), Qt.KeyboardModifier.NoModifier), \
    w.timeline._snap(int(66.9 * sr), Qt.KeyboardModifier.NoModifier)
bars = w.timeline.tempo.bar_starts
print("snapped to bar lines:", any(abs(a / sr - x) < 1e-3 for x in bars),
      any(abs(b / sr - x) < 1e-3 for x in bars))
w.timeline.selection = (a, b)
w._selection((a, b))
loop = w.song.add_loop(w._default_loop_name(a / sr, b / sr), a / sr, b / sr)
w._refresh_loops(select=loop)
w._use_loop(loop)
print("saved loop:", loop["name"])

w._set_key(1)
assert wait(lambda: w.key_label.text() != "Transposing…"), "transpose timed out"
print("key:", w.key_label.text(), "| audio changed:",
      not (w.tracks[0].data is w.originals[0]))

# Silent playback with a count-in: music must start after exactly one bar.
w.master.setValue(0)
w.countin_btn.setChecked(True)
start = w.player.position
bar_len = w.timeline.tempo.beat_period() * w.timeline.tempo.beats_per_bar
w.toggle()
t0 = time.monotonic()
wait(lambda: w.player.counting_in, 2)
seen_count = w.time.text()
wait(lambda: not w.player.counting_in, bar_len + 3)
t_music = time.monotonic() - t0
wait(lambda: False, 0.5)
moved = (w.player.position - start) / sr
print(f"count-in label '{seen_count}', music started after {t_music:.2f}s "
      f"(one bar = {bar_len:.2f}s), then moved {moved:.2f}s")
app.processEvents()
w.grab().save(str(out / f"{scheme}-player-practice.png"))
w.toggle()
w.close()

# Reopen: loop, tempo correction and key are remembered.
w2 = open_player()
wait(lambda: w2.timeline.tempo is not None and w2.key_label.text() != "Transposing…")
print("remembered:", [lp["name"] for lp in w2.song.loops], w2.tempo_btn.text(),
      w2.key_label.text())
w2.close()
SongData(guitar).path.unlink(missing_ok=True)
