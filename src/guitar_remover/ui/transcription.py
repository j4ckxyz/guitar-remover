"""Guitar → tab and MIDI window (Basic Pitch)."""
from __future__ import annotations

import threading
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout,
                               QWidget)

from .. import APP_NAME
from .. import transcribe as T
from .widgets import StepSlider, secondary, small


class _Bridge(QObject):
    progress = Signal(float)
    done = Signal(object, str)  # model output or None, error message


class TranscriptionWindow(QWidget):
    def __init__(self, audio: np.ndarray, samplerate: int, title: str, tempo=None,
                 save_dir: Path | None = None, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(f"{title} · Tab · {APP_NAME}")
        self.title = title
        self.tempo = tempo
        self.save_dir = save_dir or Path.home()
        self.output = None
        self.notes: list[T.Note] = []
        self.resize(900, 620)

        self.tuning = QComboBox()
        for name in T.TUNINGS:
            self.tuning.addItem(name)
        self.tuning.setToolTip("The tuning your guitar is in. Songs recorded in E♭ tuning "
                               "read like standard tab when you pick E♭ standard here.")
        self.tuning.currentIndexChanged.connect(self._render)
        self.detail = StepSlider("Fewer notes", "More notes", 5)
        self.detail.setValue(2)
        self.detail.valueChanged.connect(self._decode)
        self.detail.setMaximumWidth(360)
        self.detail.caption.hide()
        self.detail.caption.setMinimumHeight(0)

        top = QHBoxLayout()
        top.addWidget(QLabel("Tuning:"))
        top.addWidget(self.tuning)
        top.addStretch(1)
        top.addWidget(self.detail)

        self.status = small(secondary(QLabel("Listening to the guitar…")), 0.9)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setMaximumHeight(8)
        self.bar.setTextVisible(False)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.text.setPlaceholderText("The tab appears here.")

        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.text.toPlainText()))
        self.save_tab = QPushButton("Save Tab…")
        self.save_tab.clicked.connect(self._save_tab)
        self.save_midi = QPushButton("Save MIDI…")
        self.save_midi.setToolTip("Open it in a notation app such as MuseScore or Guitar Pro, "
                                  "or in your DAW")
        self.save_midi.clicked.connect(self._save_midi)
        for b in (copy, self.save_tab, self.save_midi):
            b.setEnabled(False)
        self._buttons = (copy, self.save_tab, self.save_midi)
        bottom = QHBoxLayout()
        bottom.addWidget(small(secondary(QLabel(
            "Automatic transcriptions are a starting point: check them by ear.")), 0.88), 1)
        for b in self._buttons:
            bottom.addWidget(b)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.addLayout(top)
        lay.addWidget(self.bar)
        lay.addWidget(self.status)
        lay.addWidget(self.text, 1)
        lay.addLayout(bottom)

        self._bridge = _Bridge()
        self._bridge.progress.connect(lambda f: self.bar.setValue(int(f * 100)))
        self._bridge.done.connect(self._model_done)
        threading.Thread(target=self._run_model, args=(audio, samplerate), daemon=True).start()

    def _run_model(self, audio, samplerate):
        try:
            out = T.model_output(audio, samplerate, progress=self._bridge.progress.emit)
            self._bridge.done.emit(out, "")
        except Exception as exc:  # noqa: BLE001
            self._bridge.done.emit(None, str(exc) or type(exc).__name__)

    def _model_done(self, out, err: str):
        self.bar.hide()
        if out is None:
            self.status.setText(f"Couldn't transcribe: {err}")
            return
        self.output = out
        self._decode()

    def _decode(self, *_):
        if self.output is None:
            return
        onset, frame, min_ms = T.detail_to_thresholds(self.detail.value() / 4)
        self.notes = T.notes_from_output(self.output, onset, frame, min_ms)
        self._render()

    def _render(self, *_):
        if self.output is None:
            return
        tuning_name = self.tuning.currentText()
        tuning = T.TUNINGS[tuning_name]
        fingered = T.fingering(self.notes, tuning)
        placed = sum(len(g) for g, _ in fingered)
        tp = self.tempo
        tab = T.render_tab(
            fingered, tuning, tp.beats if tp else None, tp.beats_per_bar if tp else 4,
            tp.bar_offset if tp else 0, title=self.title, tuning_name=tuning_name,
            bpm=tp.bpm if tp else None)
        self.text.setPlainText(tab)
        dropped = len(self.notes) - placed
        msg = f"{len(self.notes)} notes"
        if dropped:
            msg += (f" · {dropped} left out (outside this tuning's range or "
                    "impossible to play together)")
        self.status.setText(msg)
        for b in self._buttons:
            b.setEnabled(bool(self.notes))

    def _save_tab(self):
        name, _ = QFileDialog.getSaveFileName(self, "Save Tab",
                                              str(self.save_dir / f"{self.title} - Tab.txt"),
                                              "Text (*.txt)")
        if name:
            Path(name).write_text(self.text.toPlainText() + "\n", encoding="utf-8")

    def _save_midi(self):
        name, _ = QFileDialog.getSaveFileName(self, "Save MIDI",
                                              str(self.save_dir / f"{self.title} - Guitar.mid"),
                                              "MIDI (*.mid)")
        if not name:
            return
        try:
            T.write_midi(Path(name), self.notes, self.tempo.bpm if self.tempo else 120.0)
        except OSError as exc:
            QMessageBox.warning(self, "Couldn't save", str(exc))
