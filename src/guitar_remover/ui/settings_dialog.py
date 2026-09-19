"""Settings window: sensible General/Performance tabs, and an Advanced tab for
people who want to tune the model directly."""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
                               QRadioButton, QScrollArea, QSpinBox, QTabWidget, QVBoxLayout,
                               QWidget)

from .. import models
from ..audio_io import FORMATS
from ..hardware import POWER_BALANCED, POWER_LABELS, POWER_LIGHT, POWER_MAX
from ..settings import Settings
from .widgets import StepSlider, secondary, small

POWER_BLURBS = {
    POWER_LIGHT: "Leaves your computer free for other things. Slower.",
    POWER_BALANCED: "Fast, while keeping your computer responsive.",
    POWER_MAX: "Uses everything available for the fastest results.",
}
CUSTOM = "__custom__"


class _Downloader(QThread):
    progress = Signal(int, int)
    done = Signal(str)  # error message or ""

    def __init__(self, models_dir: Path, ref: str):
        super().__init__()
        self.models_dir, self.ref = models_dir, ref
        self.cancel = threading.Event()

    def run(self):
        try:
            models.download(self.models_dir, self.ref, self.progress.emit, self.cancel)
            self.done.emit("")
        except models.Cancelled:
            self.done.emit("Cancelled")
        except Exception as exc:  # noqa: BLE001
            self.done.emit(str(exc).splitlines()[0][:200] or type(exc).__name__)


def _hint(text: str) -> QLabel:
    lab = small(secondary(QLabel(text)), 0.9)
    lab.setWordWrap(True)
    return lab


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, hardware, parent=None):
        super().__init__(parent)
        self.s = settings
        self.hw = hardware
        self.models_deleted = False
        self._dl: _Downloader | None = None
        self.setWindowTitle("Settings")
        self.setMinimumWidth(560)
        self.resize(600, 640)

        tabs = QTabWidget()
        tabs.addTab(self._general_tab(), "General")
        tabs.addTab(self._performance_tab(), "Performance")
        tabs.addTab(self._advanced_tab(), "Advanced")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        self.reset_btn = buttons.addButton("Restore Defaults",
                                           QDialogButtonBox.ButtonRole.ResetRole)
        self.reset_btn.clicked.connect(self._reset)

        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        lay.addWidget(buttons)
        # Only "Close" should react to Return, not every button in the tabs.
        for b in self.findChildren(QPushButton):
            b.setAutoDefault(False)
        close = buttons.button(QDialogButtonBox.StandardButton.Close)
        close.setDefault(True)
        self._load()

    # ------------------------------------------------------------- tabs
    def _general_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        # Where
        self.out_edit = QLineEdit()
        self.out_edit.setReadOnly(True)
        browse = QPushButton("Choose…")
        browse.clicked.connect(self._choose_out)
        row = QHBoxLayout()
        row.addWidget(self.out_edit, 1)
        row.addWidget(browse)
        self.next_to = QCheckBox("Save next to the original song instead")
        self.next_to.toggled.connect(lambda v: (self.s.set("save_next_to_original", v),
                                                self.out_edit.setEnabled(not v)))
        self.per_song = QCheckBox("Put each song in its own folder")
        self.per_song.toggled.connect(lambda v: self.s.set("folder_per_song", v))
        form.addRow("Save to:", row)
        form.addRow("", self.next_to)
        form.addRow("", self.per_song)

        # Format
        self.fmt = QComboBox()
        for key, (label, _) in FORMATS.items():
            self.fmt.addItem(label, key)
        self.fmt.currentIndexChanged.connect(
            lambda: self.s.set("format", self.fmt.currentData()))
        form.addRow("File format:", self.fmt)

        # What to remove
        self.keep_vocals = QRadioButton("Remove the guitar only")
        self.no_vocals = QRadioButton("Remove the guitar and the vocals")
        grp = QButtonGroup(w)
        grp.addButton(self.keep_vocals)
        grp.addButton(self.no_vocals)
        self.no_vocals.toggled.connect(lambda v: self.s.set("remove_vocals", v))
        box = QVBoxLayout()
        box.setSpacing(4)
        box.addWidget(self.keep_vocals)
        box.addWidget(self.no_vocals)
        box.addWidget(_hint("Removing vocals too gives you an instrumental to play and sing along with."))
        form.addRow("Backing track:", box)

        self.open_player = QCheckBox("Open the player when a song is ready")
        self.open_player.toggled.connect(lambda v: self.s.set("open_player_when_done", v))
        self.reveal = QCheckBox("Show the files when all songs are finished")
        self.reveal.toggled.connect(lambda v: self.s.set("reveal_when_done", v))
        done = QVBoxLayout()
        done.setSpacing(4)
        done.addWidget(self.open_player)
        done.addWidget(self.reveal)
        form.addRow("When done:", done)

        self.update_mode = QComboBox()
        self.update_mode.addItem("Check automatically and ask", "ask")
        self.update_mode.addItem("Install automatically when I quit", "auto")
        self.update_mode.addItem("Don't check", "off")
        self.update_mode.currentIndexChanged.connect(
            lambda: self.s.set("update_mode", self.update_mode.currentData()))
        form.addRow("Updates:", self.update_mode)
        return w

    def _performance_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lab = QLabel("How much of your computer to use")
        f = lab.font()
        f.setWeight(f.Weight.DemiBold)
        lab.setFont(f)
        lay.addWidget(lab)
        self.power = StepSlider("Lighter", "Faster", 3)
        self.power.valueChanged.connect(self._power_changed)
        lay.addWidget(self.power)
        lay.addSpacing(8)

        self.use_gpu = QCheckBox("Use the graphics card (GPU) when available")
        self.use_gpu.toggled.connect(lambda v: (self.s.set("use_gpu", v), self._refresh_plan()))
        lay.addWidget(self.use_gpu)
        lay.addWidget(_hint("Much faster on Apple Silicon Macs and on NVIDIA (or, on Linux, "
                            "AMD ROCm) graphics cards."))
        lay.addSpacing(8)

        store = QGroupBox("Remembered songs")
        sl = QVBoxLayout(store)
        row = QHBoxLayout()
        self.cache_size = QComboBox()
        for label, gb in [("Off", 0), ("1 GB (about 7 songs)", 1), ("3 GB (about 20 songs)", 3),
                          ("10 GB (about 70 songs)", 10), ("30 GB (about 200 songs)", 30)]:
            self.cache_size.addItem(label, gb)
        self.cache_size.currentIndexChanged.connect(self._cache_size_changed)
        self.cache_clear = QPushButton("Clear…")
        self.cache_clear.clicked.connect(self._clear_cache)
        row.addWidget(QLabel("Space to use:"))
        row.addWidget(self.cache_size)
        row.addStretch(1)
        row.addWidget(self.cache_clear)
        sl.addLayout(row)
        self.cache_info = _hint("")
        sl.addWidget(self.cache_info)
        lay.addWidget(store)
        lay.addSpacing(8)

        grp = QGroupBox("This computer")
        g = QVBoxLayout(grp)
        self.hw_label = QLabel()
        self.hw_label.setWordWrap(True)
        self.hw_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        g.addWidget(self.hw_label)
        lay.addWidget(grp)
        lay.addStretch(1)
        return w

    def _advanced_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(_hint("The defaults work well for almost everyone. These options are "
                            "here if you want to experiment."))

        # Model
        mg = QGroupBox("Separation model")
        mf = QFormLayout(mg)
        mf.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.model = QComboBox()
        for info in models.CATALOG.values():
            self.model.addItem(info.title, info.name)
        self.model.addItem("Other (Hugging Face or a folder)…", CUSTOM)
        self.model.currentIndexChanged.connect(self._model_changed)
        mf.addRow("Model:", self.model)
        self.custom_model = QLineEdit()
        self.custom_model.setPlaceholderText("namespace/model_name  or  /path/to/folder")
        self.custom_model.editingFinished.connect(self._custom_model_changed)
        cm_browse = QPushButton("Folder…")
        cm_browse.clicked.connect(self._choose_model_folder)
        self.custom_row = QWidget()
        cr = QHBoxLayout(self.custom_row)
        cr.setContentsMargins(0, 0, 0, 0)
        cr.addWidget(self.custom_model, 1)
        cr.addWidget(cm_browse)
        mf.addRow("", self.custom_row)
        self.model_desc = _hint("")
        mf.addRow("", self.model_desc)
        self.stem = QComboBox()
        for label, key in [("Automatic (guitar, or 'other' for 4-stem models)", "auto"),
                           ("Guitar", "guitar"), ("Other", "other"), ("Piano", "piano"),
                           ("Vocals", "vocals"), ("Bass", "bass"), ("Drums", "drums")]:
            self.stem.addItem(label, key)
        self.stem.currentIndexChanged.connect(
            lambda: self.s.set("target_stem", self.stem.currentData()))
        mf.addRow("Part to remove:", self.stem)
        dl_row = QHBoxLayout()
        self.dl_status = small(secondary(QLabel()), 0.9)
        self.dl_bar = QProgressBar()
        self.dl_bar.setMaximumHeight(8)
        self.dl_bar.setTextVisible(False)
        self.dl_bar.hide()
        self.dl_btn = QPushButton("Download Now")
        self.dl_btn.clicked.connect(self._download)
        self.del_btn = QPushButton("Delete Downloads…")
        self.del_btn.clicked.connect(self._delete_models)
        dl_row.addWidget(self.dl_status, 1)
        dl_row.addWidget(self.dl_btn)
        dl_row.addWidget(self.del_btn)
        mf.addRow("", self.dl_bar)
        mf.addRow("", dl_row)
        lay.addWidget(mg)

        # Quality
        qg = QGroupBox("Custom quality (replaces the Speed and Quality slider)")
        qg.setCheckable(True)
        qg.toggled.connect(lambda v: self.s.set("custom_quality", v))
        self.custom_quality = qg
        qf = QFormLayout(qg)
        self.shifts = QSpinBox()
        self.shifts.setRange(0, 20)
        self.shifts.valueChanged.connect(lambda v: self.s.set("shifts", v))
        qf.addRow("Shifts (extra passes):", self.shifts)
        self.overlap = QDoubleSpinBox()
        self.overlap.setRange(0.0, 0.95)
        self.overlap.setSingleStep(0.05)
        self.overlap.setDecimals(2)
        self.overlap.valueChanged.connect(lambda v: self.s.set("overlap", v))
        qf.addRow("Window overlap:", self.overlap)
        self.segment = QDoubleSpinBox()
        self.segment.setRange(0.0, 60.0)
        self.segment.setSingleStep(0.5)
        self.segment.setSpecialValueText("Automatic")
        self.segment.setSuffix(" s")
        self.segment.valueChanged.connect(lambda v: self.s.set("segment", v))
        qf.addRow("Window length:", self.segment)
        for sb in (self.shifts, self.overlap, self.segment):
            sb.setMinimumWidth(120)
        lay.addWidget(qg)

        # Output details
        og = QGroupBox("Output")
        of = QFormLayout(og)
        of.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.method = QComboBox()
        self.method.addItem("Mix of the other separated parts (cleanest)", "stems")
        self.method.addItem("Original minus guitar (nothing else is lost)", "residual")
        self.method.currentIndexChanged.connect(
            lambda: self.s.set("backing_method", self.method.currentData()))
        of.addRow("Backing track:", self.method)
        self.clip = QComboBox()
        self.clip.addItem("Lower both tracks equally (keeps them in sync)", "shared_gain")
        self.clip.addItem("Clip loud peaks", "clamp")
        self.clip.addItem("Do nothing", "none")
        self.clip.currentIndexChanged.connect(
            lambda: self.s.set("clip_mode", self.clip.currentData()))
        of.addRow("Avoid clipping:", self.clip)
        self.stems = QCheckBox("Also save every separated part (drums, bass, vocals…)")
        self.stems.toggled.connect(lambda v: self.s.set("export_stems", v))
        of.addRow("", self.stems)
        self.tags = QCheckBox("Copy title and artist tags from the original")
        self.tags.toggled.connect(lambda v: self.s.set("keep_tags", v))
        of.addRow("", self.tags)
        lay.addWidget(og)

        # Hardware overrides
        hg = QGroupBox("Hardware")
        hf = QFormLayout(hg)
        self.device = QComboBox()
        self.device.addItem("Automatic", "auto")
        if self.hw is not None and self.hw.gpu_kind:
            self.device.addItem({"cuda": f"{self.hw.gpu_vendor} GPU "
                                         f"({'ROCm' if self.hw.gpu_vendor == 'AMD' else 'CUDA'})",
                                 "mps": "Apple GPU (Metal)"}[self.hw.gpu_kind], self.hw.gpu_kind)
        self.device.addItem("CPU only", "cpu")
        self.device.currentIndexChanged.connect(
            lambda: (self.s.set("device", self.device.currentData()), self._refresh_plan()))
        hf.addRow("Run on:", self.device)
        self.threads = QSpinBox()
        self.threads.setRange(0, 256)
        self.threads.setSpecialValueText("Automatic")
        self.threads.valueChanged.connect(
            lambda v: (self.s.set("threads", v), self._refresh_plan()))
        self.threads.setMinimumWidth(120)
        hf.addRow("CPU threads:", self.threads)
        mdir = QHBoxLayout()
        self.models_dir = QLineEdit()
        self.models_dir.setReadOnly(True)
        mbrowse = QPushButton("Choose…")
        mbrowse.clicked.connect(self._choose_models_dir)
        mdir.addWidget(self.models_dir, 1)
        mdir.addWidget(mbrowse)
        hf.addRow("Model storage:", mdir)
        lay.addWidget(hg)
        lay.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(w)
        return scroll

    # ------------------------------------------------------------ loading
    def _load(self):
        s = self.s
        widgets = [self.next_to, self.per_song, self.fmt, self.keep_vocals, self.no_vocals,
                   self.reveal, self.open_player, self.power.slider, self.use_gpu, self.model, self.stem,
                   self.custom_quality, self.shifts, self.overlap, self.segment, self.method,
                   self.clip, self.stems, self.tags, self.device, self.threads,
                   self.cache_size, self.update_mode]
        for wd in widgets:
            wd.blockSignals(True)
        self.out_edit.setText(str(s.output_dir))
        self.next_to.setChecked(s.get("save_next_to_original"))
        self.out_edit.setEnabled(not s.get("save_next_to_original"))
        self.per_song.setChecked(s.get("folder_per_song"))
        self._select(self.fmt, s.get("format"))
        (self.no_vocals if s.get("remove_vocals") else self.keep_vocals).setChecked(True)
        self.reveal.setChecked(s.get("reveal_when_done"))
        self.open_player.setChecked(s.get("open_player_when_done"))
        self.power.setValue(s.get("power"))
        self.use_gpu.setChecked(s.get("use_gpu"))
        ref = s.get("model")
        if ref in models.CATALOG:
            self._select(self.model, ref)
            self.custom_model.clear()
        else:
            self._select(self.model, CUSTOM)
            self.custom_model.setText(ref)
        self._select(self.stem, s.get("target_stem"))
        self.custom_quality.setChecked(s.get("custom_quality"))
        self.shifts.setValue(s.get("shifts"))
        self.overlap.setValue(s.get("overlap"))
        self.segment.setValue(s.get("segment"))
        self._select(self.method, s.get("backing_method"))
        self._select(self.clip, s.get("clip_mode"))
        self.stems.setChecked(s.get("export_stems"))
        self.tags.setChecked(s.get("keep_tags"))
        self._select(self.device, s.get("device"))
        self.threads.setValue(s.get("threads"))
        self.models_dir.setText(str(s.models_dir))
        self._select(self.cache_size, s.get("cache_gb"))
        self._select(self.update_mode, s.get("update_mode"))
        for wd in widgets:
            wd.blockSignals(False)
        self._power_changed(self.power.value(), save=False)
        self._model_changed(save=False)
        self._refresh_cache_info()

    @staticmethod
    def _select(combo: QComboBox, data):
        i = combo.findData(data)
        combo.setCurrentIndex(max(0, i))

    # ------------------------------------------------------------ handlers
    def _choose_out(self):
        d = QFileDialog.getExistingDirectory(self, "Choose Where to Save Backing Tracks",
                                             str(self.s.output_dir))
        if d:
            self.s.set("output_dir", d)
            self.out_edit.setText(d)
            self.next_to.setChecked(False)

    def _power_changed(self, v: int, save: bool = True):
        if save:
            self.s.set("power", v)
        self.power.setCaption(f"<b>{POWER_LABELS[v]}</b> · {POWER_BLURBS[v]}")
        self._refresh_plan()

    def _refresh_plan(self):
        if self.hw is None:
            self.hw_label.setText("Still checking your hardware…")
            return
        from ..hardware import plan_resources
        dev = self.s.get("device")
        if dev == "auto" and not self.s.get("use_gpu"):
            dev = "cpu"
        plan = plan_resources(self.hw, self.s.get("power"), dev, self.s.get("threads"),
                              self.s.get("segment"))
        lines = [self.hw.summary,
                 f"Songs will be processed on the <b>{plan.device_label}</b> using "
                 f"{plan.threads} CPU thread{'s' if plan.threads != 1 else ''}"
                 + (f" and {plan.workers} parallel workers" if plan.workers else "") + ".",
                 f"Audio is processed in {plan.block_seconds:.0f}-second blocks to keep "
                 "memory use low."]
        if plan.low_priority:
            lines.append("Runs at low priority so other apps stay responsive.")
        lines += plan.notes
        self.hw_label.setText("<br>".join(lines))

    def _current_model_ref(self) -> str:
        data = self.model.currentData()
        if data == CUSTOM:
            return self.custom_model.text().strip() or models.DEFAULT_MODEL
        return data

    def _model_changed(self, *_, save: bool = True):
        custom = self.model.currentData() == CUSTOM
        self.custom_row.setVisible(custom)
        ref = self._current_model_ref()
        if save and (not custom or self.custom_model.text().strip()):
            self.s.set("model", ref)
        info = models.CATALOG.get(ref)
        if info:
            self.model_desc.setText(f"{info.description} Download size {info.size_mb} MB.")
        else:
            self.model_desc.setText(
                "Any Demucs model on the Hugging Face hub (e.g. "
                "<i>adefossez/htdemucs_6s</i>), or a folder with Demucs .th and .yaml "
                "files. 'Part to remove' should match one of its stems.")
        self._refresh_dl()

    def _custom_model_changed(self):
        if self.custom_model.text().strip():
            self.s.set("model", self.custom_model.text().strip())
            self._refresh_dl()

    def _choose_model_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Choose a Demucs Model Folder")
        if d:
            self.custom_model.setText(d)
            self._custom_model_changed()

    def _refresh_dl(self):
        ref = self._current_model_ref()
        mdir = self.s.models_dir
        size = models.downloaded_size(mdir)
        self.del_btn.setEnabled(size > 0)
        self.del_btn.setText(f"Delete Downloads ({size / 1e6:.0f} MB)…" if size
                             else "Delete Downloads…")
        if Path(ref).is_dir():
            self.dl_status.setText("Using a local model folder.")
            self.dl_btn.hide()
        elif models.is_downloaded(mdir, ref):
            self.dl_status.setText("Downloaded and ready.")
            self.dl_btn.hide()
        else:
            self.dl_status.setText("Downloads automatically the first time it is used.")
            self.dl_btn.show()

    def _download(self):
        if self._dl is not None:
            self._dl.cancel.set()
            return
        self._dl = _Downloader(self.s.models_dir, self._current_model_ref())
        self._dl.progress.connect(self._dl_progress)
        self._dl.done.connect(self._dl_done)
        self.dl_btn.setText("Cancel")
        self.dl_bar.setRange(0, 0)
        self.dl_bar.show()
        self.dl_status.setText("Downloading…")
        self._dl.start()

    def _dl_progress(self, done: int, total: int):
        if total:
            self.dl_bar.setRange(0, 1000)
            self.dl_bar.setValue(int(done / total * 1000))
            self.dl_status.setText(f"Downloading · {done / 1e6:.0f} of {total / 1e6:.0f} MB")

    def _dl_done(self, err: str):
        self._dl.wait()
        self._dl = None
        self.dl_bar.hide()
        self.dl_btn.setText("Download Now")
        self._refresh_dl()
        if err:
            self.dl_status.setText(f"Download failed: {err}")

    def _delete_models(self):
        mdir = self.s.models_dir
        size = models.downloaded_size(mdir)
        ans = QMessageBox.question(
            self, "Delete downloaded models?",
            f"This frees {size / 1e6:.0f} MB. Models download again when needed.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel)
        if ans == QMessageBox.StandardButton.Yes:
            models.delete_downloads(mdir)
            self.models_deleted = True
            self._refresh_dl()

    def _choose_models_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Choose Where to Keep Models",
                                             str(self.s.models_dir))
        if d:
            self.s.set("models_dir", d)
            self.models_dir.setText(d)
            self._refresh_dl()

    def _refresh_cache_info(self):
        used = self.s.stem_cache().size()
        self.cache_clear.setEnabled(used > 0)
        self.cache_info.setText(
            "Songs you've separated are kept (every part, as lossless FLAC), so changing "
            "the format or vocal setting, or saving a song again, takes seconds. "
            f"Using {used / 1e6:.0f} MB. The oldest songs are removed when it's full.")

    def _cache_size_changed(self, *_):
        self.s.set("cache_gb", self.cache_size.currentData())
        self.s.stem_cache().evict()
        self._refresh_cache_info()

    def _clear_cache(self):
        ans = QMessageBox.question(
            self, "Forget remembered songs?",
            "Songs will need to be separated again next time. Your saved backing tracks "
            "are not affected.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel)
        if ans == QMessageBox.StandardButton.Yes:
            self.s.stem_cache().clear()
            self._refresh_cache_info()

    def _reset(self):
        ans = QMessageBox.question(
            self, "Restore defaults?", "Reset every setting to how it was when you "
            "first opened the app?",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel)
        if ans == QMessageBox.StandardButton.Yes:
            self.s.reset()
            self._load()

    def done(self, r):
        if self._dl is not None:
            self._dl.cancel.set()
            self._dl.wait(3000)
        super().done(r)

