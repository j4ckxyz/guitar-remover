"""The main window: drop songs, pick speed vs quality, get backing tracks."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QPalette
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel, QMainWindow,
                               QMessageBox, QPushButton, QScrollArea, QSizePolicy, QToolButton,
                               QVBoxLayout, QWidget)

from .. import APP_NAME, __version__, models
from ..audio_io import AUDIO_EXTENSIONS
from ..runner import Runner
from ..separator import QUALITY_PRESETS, preset_cost
from ..settings import Settings
from .widgets import mix as mix_color
from .widgets import (DropZone, ElidedLabel, JobRow, StepSlider, audio_files, fmt_duration, open_file,
                      secondary, small, theme_icon)


class _UpdateBridge(QObject):
    found = Signal(object, bool)  # Release or None, automatic
    failed = Signal(str, bool)
    staged = Signal(object, object, bool)  # release, staged path, then_quit
    progress = Signal(int, int)


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.rows: dict[int, JobRow] = {}
        self.started_at: dict[int, float] = {}
        self._sep_start: dict[int, float] = {}
        self.players: list = []
        self.hardware = None
        self.setWindowTitle(APP_NAME)
        self.setAcceptDrops(True)
        self.setMinimumSize(460, 520)
        self.resize(560, 660)

        # Worker thread: imports torch and detects hardware in the background.
        self.runner = Runner(settings, self)
        self.runner.ready.connect(self._on_ready)
        self.runner.started_job.connect(self._on_started)
        self.runner.progress.connect(self._on_progress)
        self.runner.finished_job.connect(self._on_finished)
        self.runner.failed_job.connect(self._on_failed)
        self.runner.cancelled_job.connect(self._on_cancelled)
        self.runner.idle.connect(self._update_title)
        self.runner.start()

        self._build_ui()
        self._build_menus()
        self._load_settings()
        self._pending_update = None  # (release, staged) to install when quitting
        QTimer.singleShot(4000, lambda: self.check_for_updates(automatic=True))

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 16, 18, 12)
        root.setSpacing(12)

        # "New version" banner, hidden until an update is found.
        self.update_bar = QWidget()
        ub = QHBoxLayout(self.update_bar)
        ub.setContentsMargins(12, 8, 8, 8)
        self.update_text = QLabel()
        self.update_text.setWordWrap(True)
        self.update_notes = QPushButton("What's New")
        self.update_now = QPushButton("Update Now")
        self.update_now.setDefault(True)
        self.update_later = QPushButton("Later")
        ub.addWidget(self.update_text, 1)
        for b in (self.update_notes, self.update_later, self.update_now):
            ub.addWidget(b)
        self.update_bar.setAutoFillBackground(True)
        pal = self.update_bar.palette()
        pal.setColor(QPalette.ColorRole.Window,
                     mix_color(pal.color(QPalette.ColorRole.Window),
                               pal.color(QPalette.ColorRole.Accent), 0.18))
        self.update_bar.setPalette(pal)
        self.update_bar.hide()
        self.update_later.clicked.connect(self._update_later)
        self.update_now.clicked.connect(self._update_now)
        self.update_notes.clicked.connect(
            lambda: self._release and QDesktopServices.openUrl(QUrl(self._release.url)))
        root.addWidget(self.update_bar)
        self._release = None

        self.drop = DropZone()
        self.drop.clicked.connect(self.add_files_dialog)
        root.addWidget(self.drop, 1)

        # Queue
        self.queue_host = QWidget()
        self.queue_layout = QVBoxLayout(self.queue_host)
        self.queue_layout.setContentsMargins(0, 0, 0, 0)
        self.queue_layout.setSpacing(8)
        self.queue_layout.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setWidget(self.queue_host)
        self.scroll.viewport().setAutoFillBackground(False)
        self.queue_host.setAutoFillBackground(False)
        self.scroll.hide()
        root.addWidget(self.scroll, 1)

        self.empty_hint = secondary(QLabel(
            "Your songs are split into two files: the <b>guitar on its own</b> and a "
            "<b>backing track</b> with everything else. Everything runs on this computer, "
            "and nothing is uploaded."))
        self.empty_hint.setWordWrap(True)
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.empty_hint)
        root.addSpacing(4)

        # Speed vs quality
        qlabel = QLabel("Speed and quality")
        f = qlabel.font()
        f.setWeight(f.Weight.DemiBold)
        qlabel.setFont(f)
        root.addWidget(qlabel)
        self.quality = StepSlider("Faster", "Better", len(QUALITY_PRESETS))
        self.quality.valueChanged.connect(self._quality_changed)
        root.addWidget(self.quality)

        # Output folder
        out_row = QHBoxLayout()
        out_row.setSpacing(8)
        out_label = QLabel("Save to:")
        self.out_path = ElidedLabel()
        self.out_change = QPushButton("Change…")
        self.out_change.clicked.connect(self.choose_output_dir)
        self.out_open = QToolButton()
        self.out_open.setAutoRaise(True)
        ic = theme_icon("FolderOpen")
        if ic.isNull():
            self.out_open.setText("Open")
        else:
            self.out_open.setIcon(ic)
        self.out_open.setToolTip("Open this folder")
        self.out_open.clicked.connect(self.open_output_dir)
        out_row.addWidget(out_label)
        out_row.addWidget(self.out_open)
        out_row.addWidget(self.out_path, 1)
        out_row.addWidget(self.out_change)
        root.addLayout(out_row)

        # Footer: hardware + settings
        foot = QHBoxLayout()
        self.hw_label = small(secondary(QLabel("Checking your computer…")), 0.88)
        self.hw_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.settings_btn = QPushButton("Settings…")
        gear = theme_icon("DocumentProperties")
        if not gear.isNull():
            self.settings_btn.setIcon(gear)
        self.settings_btn.clicked.connect(self.open_settings)
        self.clear_btn = QPushButton("Clear Finished")
        self.clear_btn.clicked.connect(self.clear_finished)
        self.clear_btn.hide()
        foot.addWidget(self.hw_label, 1)
        foot.addWidget(self.clear_btn)
        foot.addWidget(self.settings_btn)
        root.addLayout(foot)

    def _build_menus(self):
        mb = self.menuBar()
        file_menu = mb.addMenu("&File")
        add = QAction("Add Songs…", self)
        add.setShortcut(QKeySequence.StandardKey.Open)
        add.triggered.connect(self.add_files_dialog)
        file_menu.addAction(add)
        add_folder = QAction("Add Folder…", self)
        add_folder.setShortcut(QKeySequence("Ctrl+Shift+O"))
        add_folder.triggered.connect(self.add_folder_dialog)
        file_menu.addAction(add_folder)
        open_tracks = QAction("Open Tracks in Player…", self)
        open_tracks.setShortcut(QKeySequence("Ctrl+Shift+P"))
        open_tracks.triggered.connect(self.open_tracks_dialog)
        file_menu.addAction(open_tracks)
        file_menu.addSeparator()
        open_out = QAction("Open Output Folder", self)
        open_out.triggered.connect(self.open_output_dir)
        file_menu.addAction(open_out)
        file_menu.addSeparator()
        self.cancel_all_act = QAction("Cancel All", self)
        self.cancel_all_act.triggered.connect(self.cancel_all)
        file_menu.addAction(self.cancel_all_act)
        prefs = QAction("Settings…", self)
        prefs.setMenuRole(QAction.MenuRole.PreferencesRole)  # "Settings…" in the macOS app menu
        prefs.setShortcut(QKeySequence.StandardKey.Preferences
                          if sys.platform == "darwin" else QKeySequence("Ctrl+,"))
        prefs.triggered.connect(self.open_settings)
        file_menu.addAction(prefs)
        quit_act = QAction("Quit" if sys.platform == "darwin" else "Exit", self)
        quit_act.setMenuRole(QAction.MenuRole.QuitRole)
        quit_act.setShortcut(QKeySequence.StandardKey.Quit)
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

        help_menu = mb.addMenu("&Help")
        upd = QAction("Check for Updates…", self)
        upd.setMenuRole(QAction.MenuRole.ApplicationSpecificRole)  # macOS app menu
        upd.triggered.connect(lambda: self.check_for_updates(automatic=False))
        help_menu.addAction(upd)
        about = QAction(f"About {APP_NAME}", self)
        about.setMenuRole(QAction.MenuRole.AboutRole)
        about.triggered.connect(self.about)
        help_menu.addAction(about)

    def _load_settings(self):
        self.quality.setValue(self.settings.get("quality"))
        self._quality_changed(self.quality.value())
        self._refresh_output_label()

    # ------------------------------------------------------------ helpers
    def _refresh_output_label(self):
        if self.settings.get("save_next_to_original"):
            self.out_path.setText("Next to each original song")
            self.out_open.setEnabled(False)
        else:
            path = self.settings.output_dir
            home = Path.home()
            text = str(path)
            try:
                text = "~/" + str(path.relative_to(home)) if sys.platform != "win32" else text
            except ValueError:
                pass
            self.out_path.setText(text)
            self.out_path.setToolTip(str(path))
            self.out_open.setEnabled(True)

    def _quality_changed(self, v: int):
        self.settings.set("quality", v)
        shifts, overlap, label, blurb = QUALITY_PRESETS[v]
        if self.settings.get("custom_quality"):
            shifts, overlap = self.settings.quality_params()
            label, blurb = "Custom", "Using the custom quality from Advanced settings"
        cap = f"<b>{label}</b> · {blurb}"
        if self.hardware is not None:
            dev = self._device_for_estimate()
            per = self.settings.seconds_per_audio_second(dev, self.settings.get("model"))
            est = per * preset_cost(shifts, overlap) * 180  # a typical 3-minute song
            cap += f"<br>About {fmt_duration(max(est, 3))} for a 3-minute song on this computer"
        self.quality.setCaption(cap)
        self.quality.slider.setEnabled(not self.settings.get("custom_quality"))

    def _device_for_estimate(self) -> str:
        from ..hardware import plan_resources
        device = self.settings.get("device")
        if device == "auto" and not self.settings.get("use_gpu"):
            device = "cpu"
        return plan_resources(self.hardware, self.settings.get("power"), device).device

    def _update_title(self):
        active = sum(1 for r in self.rows.values() if r.state in ("waiting", "running"))
        self.setWindowTitle(APP_NAME if not active else f"{APP_NAME} · {active} left")
        self.clear_btn.setVisible(any(r.state in ("done", "failed", "cancelled")
                                      for r in self.rows.values()))

    def _show_queue(self):
        has = bool(self.rows)
        self.scroll.setVisible(has)
        self.empty_hint.setVisible(not has)
        self.drop.set_compact(has)

    # ------------------------------------------------------------ actions
    def add_files_dialog(self):
        exts = " ".join(f"*{e}" for e in sorted(AUDIO_EXTENSIONS))
        files, _ = QFileDialog.getOpenFileNames(
            self, "Choose Songs", "",
            f"Audio files ({exts});;All files (*)")
        if files:
            self.add_files([Path(f) for f in files])

    def add_folder_dialog(self):
        d = QFileDialog.getExistingDirectory(self, "Choose a Folder of Songs")
        if d:
            from PySide6.QtCore import QUrl
            self.add_files(audio_files([QUrl.fromLocalFile(d)]))

    def add_files(self, paths: list[Path]):
        if not paths:
            return
        for p in paths:
            cfg = self.settings.job_config(p)
            job_id = self.runner.submit(p, cfg)
            row = JobRow(job_id, p)
            row.cancel_requested.connect(self._cancel_job)
            row.open_player_requested.connect(self._open_player_for)
            row.export_again_requested.connect(self._retry_job)
            row.retry_requested.connect(self._retry_job)
            row.remove_requested.connect(self._remove_row)
            if self.hardware is None:
                row.set_waiting("Getting ready…")
            self.rows[job_id] = row
            self.queue_layout.insertWidget(self.queue_layout.count() - 1, row)
        self._show_queue()
        self._update_title()
        QTimer.singleShot(50, lambda: self.scroll.ensureWidgetVisible(
            self.rows[max(self.rows)]))

    def _open_player_for(self, job_id: int):
        row = self.rows.get(job_id)
        if row and row.result:
            self.open_player([("", row.result.guitar), ("", row.result.backing)],
                             row.src.stem)

    def open_player(self, tracks: list[tuple[str, Path]], title: str):
        from .player import PlayerWindow
        # Reuse an open player for the same song instead of opening another.
        for w in list(self.players):
            try:
                if w.isVisible() and w.title == title:
                    w.raise_()
                    w.activateWindow()
                    return w
            except RuntimeError:  # already deleted
                self.players.remove(w)
        named = [(name or p.stem.rsplit(" - ", 1)[-1], p) for name, p in tracks]
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            win = PlayerWindow(named, title)
        except Exception as exc:  # noqa: BLE001
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Couldn't open the player", str(exc))
            return None
        QApplication.restoreOverrideCursor()
        win.destroyed.connect(lambda *_: self.players.remove(win) if win in self.players
                              else None)
        self.players.append(win)
        win.show()
        return win

    def open_tracks_dialog(self):
        exts = " ".join(f"*{e}" for e in sorted(AUDIO_EXTENSIONS))
        files, _ = QFileDialog.getOpenFileNames(
            self, "Choose Tracks to Play Together", str(self.settings.output_dir),
            f"Audio files ({exts})")
        if files:
            paths = [Path(f) for f in files][:6]
            title = paths[0].stem.rsplit(" - ", 1)[0]
            self.open_player([("", p) for p in paths], title)

    def _cancel_job(self, job_id: int):
        self.runner.cancel(job_id)
        row = self.rows.get(job_id)
        if row and row.state == "running":
            row.set_running("Cancelling…", -1)

    def _retry_job(self, job_id: int):
        row = self.rows.pop(job_id, None)
        if not row:
            return
        src = row.src
        self.queue_layout.removeWidget(row)
        row.deleteLater()
        self.add_files([src])

    def _remove_row(self, job_id: int):
        row = self.rows.pop(job_id, None)
        if row:
            self.queue_layout.removeWidget(row)
            row.deleteLater()
        self._show_queue()
        self._update_title()

    def clear_finished(self):
        for job_id, row in list(self.rows.items()):
            if row.state in ("done", "failed", "cancelled"):
                self._remove_row(job_id)

    def cancel_all(self):
        for job_id, row in self.rows.items():
            if row.state in ("waiting", "running"):
                self._cancel_job(job_id)

    def choose_output_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Choose Where to Save Backing Tracks",
                                             str(self.settings.output_dir))
        if d:
            self.settings.set("output_dir", d)
            self.settings.set("save_next_to_original", False)
            self._refresh_output_label()

    def open_output_dir(self):
        d = self.settings.output_dir
        d.mkdir(parents=True, exist_ok=True)
        open_file(d)

    def open_settings(self):
        from .settings_dialog import SettingsDialog
        dlg = SettingsDialog(self.settings, self.hardware, self)
        dlg.exec()
        self._refresh_output_label()
        self._quality_changed(self.quality.value())
        if self.runner.separator is not None and dlg.models_deleted:
            self.runner.separator.unload()

    def about(self):
        import torch
        QMessageBox.about(
            self, f"About {APP_NAME}",
            f"<h3>{APP_NAME} {__version__}</h3>"
            "<p>Make guitar backing tracks from any song, entirely on your computer.</p>"
            "<p>Separation by <a href='https://github.com/adefossez/demucs'>Demucs</a> "
            f"(Meta AI, MIT licence). PyTorch {torch.__version__.split('+')[0]}, "
            "Qt for Python.</p>")

    # --------------------------------------------------------------- updates
    def check_for_updates(self, automatic: bool):
        import threading
        import time as _t

        from .. import updater
        mode = self.settings.get("update_mode")
        if automatic:
            if mode == "off" or updater.install_kind() == "source":
                return
            if _t.time() - self.settings.get("update_last_check") < 20 * 3600:
                return
        if not hasattr(self, "_ub"):
            self._ub = _UpdateBridge()
            self._ub.found.connect(self._update_found)
            self._ub.failed.connect(self._update_failed)
            self._ub.staged.connect(self._update_staged)
            self._ub.progress.connect(
                lambda d, t: self.update_text.setText(
                    f"Downloading version {self._release.version}… "
                    f"{d * 100 // t if t else 0}%"))

        def work():
            try:
                rel = updater.latest_release()
                self.settings.set("update_last_check", _t.time())
                self._ub.found.emit(rel if updater.is_newer(rel.version) else None, automatic)
            except updater.UpdateError as exc:
                self._ub.failed.emit(str(exc), automatic)
        threading.Thread(target=work, daemon=True).start()

    def _update_found(self, rel, automatic: bool):
        from .. import __version__, updater
        if rel is None:
            if not automatic:
                QMessageBox.information(self, "No updates",
                                        f"You have the latest version ({__version__}).")
            return
        if automatic and rel.version == self.settings.get("update_skip"):
            return
        self._release = rel
        if automatic and self.settings.get("update_mode") == "auto" \
                and updater.install_kind() != "uv":
            self._start_update(then_quit=False)  # download now, install when you quit
            return
        self.update_text.setText(f"<b>Guitar Remover {rel.version}</b> is available "
                                 f"(you have {__version__}).")
        self.update_bar.show()
        for b in (self.update_now, self.update_later, self.update_notes):
            b.setEnabled(True)

    def _update_failed(self, msg: str, automatic: bool):
        if not automatic:
            QMessageBox.warning(self, "Couldn't check for updates", msg)

    def _update_later(self):
        if self._release:
            self.settings.set("update_skip", self._release.version)
        self.update_bar.hide()

    def _update_now(self):
        busy = any(r.state in ("waiting", "running") for r in self.rows.values())
        if busy:
            QMessageBox.information(self, "Songs are still processing",
                                    "The update will install when you quit the app.")
            self._start_update(then_quit=False)
            return
        self._start_update(then_quit=True)

    def _start_update(self, then_quit: bool):
        import threading

        from .. import updater
        rel = self._release
        for b in (self.update_now, self.update_later):
            b.setEnabled(False)

        def work():
            try:
                staged = updater.prepare(rel, self._ub.progress.emit)
                self._ub.staged.emit(rel, staged, then_quit)
            except Exception as exc:  # noqa: BLE001
                self._ub.failed.emit(str(exc), False)
        threading.Thread(target=work, daemon=True).start()

    def _update_staged(self, rel, staged, then_quit: bool):
        from .. import updater
        self._pending_update = (rel, staged)
        if then_quit:
            try:
                updater.apply(rel, staged, relaunch=True)
            except updater.UpdateError as exc:
                QMessageBox.warning(self, "Couldn't update", str(exc))
                return
            self._pending_update = None
            self._force_quit = True
            self.close()
        else:
            self.update_text.setText(f"Guitar Remover {rel.version} will install when you "
                                     "quit.")
            self.update_bar.show()

    # ------------------------------------------------------ runner signals
    def _on_ready(self, hw):
        self.hardware = hw
        from ..hardware import plan_resources
        dev = self._device_for_estimate()
        where = {"mps": "Apple GPU", "cuda": f"{hw.gpu_vendor} GPU", "cpu": "CPU"}[dev]
        self.hw_label.setText(f"Using the {where} · {hw.summary}")
        plan = plan_resources(hw, self.settings.get("power"))
        self.hw_label.setToolTip("\n".join(plan.notes) or hw.summary)
        for row in self.rows.values():
            if row.state == "waiting":
                row.set_waiting()
        self._quality_changed(self.quality.value())
        if not models.is_downloaded(self.settings.models_dir, self.settings.get("model")):
            info = models.CATALOG.get(self.settings.get("model"))
            size = f" ({info.size_mb} MB)" if info else ""
            self.hw_label.setText(self.hw_label.text() +
                                  f"\nThe separation model{size} downloads the first time you use it.")

    def _on_started(self, job_id: int):
        self.started_at[job_id] = time.monotonic()
        if row := self.rows.get(job_id):
            row.set_running("Starting…", -1)
        self._update_title()

    def _on_progress(self, job_id: int, stage: str, frac: float):
        row = self.rows.get(job_id)
        if not row or row.state not in ("running", "waiting"):
            return
        if row.status.text() == "Cancelling…":
            return
        eta = None
        if stage == "Separating":
            if frac <= 0.001 or job_id not in self._sep_start:
                self._sep_start[job_id] = time.monotonic()
            elapsed = time.monotonic() - self._sep_start[job_id]
            if frac > 0.04 and elapsed > 1.5:
                eta = elapsed * (1 - frac) / frac + 1
        row.set_running(stage, frac, eta)

    def _on_finished(self, job_id: int, result):
        row = self.rows.get(job_id)
        if row:
            row.set_done(result, " ".join(n for n in result.notes if "clipping" in n
                                           or "GPU" in n))
        self._update_title()
        queue_empty = not any(r.state in ("waiting", "running") for r in self.rows.values())
        if row and queue_empty and self.settings.get("open_player_when_done"):
            self._open_player_for(job_id)
        if self.settings.get("reveal_when_done") and queue_empty:
            from .widgets import reveal_in_file_manager
            reveal_in_file_manager(result.backing)
        QApplication.alert(self)  # bounce the Dock icon / flash the taskbar
        self._quality_changed(self.quality.value())  # refresh learned estimate

    def _on_failed(self, job_id: int, message: str, details: str):
        if row := self.rows.get(job_id):
            row.set_failed(message, details)
        print(details, file=sys.stderr)
        self._update_title()

    def _on_cancelled(self, job_id: int):
        if row := self.rows.get(job_id):
            row.set_cancelled()
        self._update_title()

    # ------------------------------------------------------ drag and drop
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and audio_files(e.mimeData().urls()):
            e.acceptProposedAction()
            self.drop.set_drag_active(True)
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self.drop.set_drag_active(False)

    def dropEvent(self, e):
        self.drop.set_drag_active(False)
        files = audio_files(e.mimeData().urls())
        if files:
            e.acceptProposedAction()
            self.add_files(files)

    def changeEvent(self, e):
        if e.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange):
            self.update()
        super().changeEvent(e)

    def closeEvent(self, e):
        busy = any(r.state in ("waiting", "running") for r in self.rows.values())
        if busy and not getattr(self, "_force_quit", False):
            ans = QMessageBox.question(
                self, "Stop and quit?",
                "Songs are still being processed. Quit anyway?",
                QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
                QMessageBox.StandardButton.Cancel)
            if ans != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
        self.runner.stop()
        if self._pending_update:  # downloaded earlier: swap it in once we've quit
            from .. import updater
            try:
                updater.apply(*self._pending_update, relaunch=False)
            except updater.UpdateError:
                pass
        for w in list(self.players):
            try:
                w.close()
            except RuntimeError:
                pass
        e.accept()
