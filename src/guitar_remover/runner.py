"""Background worker: runs jobs one at a time off the UI thread."""
from __future__ import annotations

import itertools
import queue
import threading
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .models import Cancelled
from .separator import JobConfig, JobResult, preset_cost


@dataclass
class Job:
    id: int
    src: Path
    cfg: JobConfig
    cancel: threading.Event = field(default_factory=threading.Event)


class Runner(QThread):
    """Owns the Separator (and so the loaded model). Signals arrive on the UI thread."""

    ready = Signal(object)  # Hardware
    started_job = Signal(int)
    progress = Signal(int, str, float)
    finished_job = Signal(int, object)  # JobResult
    failed_job = Signal(int, str, str)  # message, details
    cancelled_job = Signal(int)
    idle = Signal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._queue: queue.Queue[Job | None] = queue.Queue()
        self._ids = itertools.count(1)
        self._jobs: dict[int, Job] = {}
        self.hardware = None
        self.separator = None
        self.current: int | None = None

    # -- called from the UI thread -----------------------------------------
    def submit(self, src: Path, cfg: JobConfig) -> int:
        job = Job(next(self._ids), src, cfg)
        self._jobs[job.id] = job
        self._queue.put(job)
        return job.id

    def cancel(self, job_id: int) -> None:
        job = self._jobs.get(job_id)
        if job:
            job.cancel.set()

    def cancel_all(self) -> None:
        for job in self._jobs.values():
            job.cancel.set()

    def stop(self) -> None:
        self.cancel_all()
        self._queue.put(None)
        self.wait(5000)

    def pending(self) -> int:
        return sum(1 for j in self._jobs.values() if not j.cancel.is_set())

    # -- worker thread -------------------------------------------------------
    def run(self):
        # Importing torch takes a few seconds; do it here, not on the UI thread.
        from . import hardware
        from .separator import Separator

        self.hardware = hardware.detect()
        self.separator = Separator(self.settings.models_dir)
        self.ready.emit(self.hardware)

        while True:
            job = self._queue.get()
            if job is None:
                break
            if job.cancel.is_set():
                self._jobs.pop(job.id, None)
                self.cancelled_job.emit(job.id)
                if self._queue.empty():
                    self.idle.emit()
                continue
            self.current = job.id
            self.started_job.emit(job.id)
            try:
                result = self._run_job(job)
            except Cancelled:
                self.cancelled_job.emit(job.id)
            except MemoryError:
                self.failed_job.emit(job.id, "Ran out of memory. Try the Light power "
                                     "setting or close other apps.", traceback.format_exc())
            except Exception as exc:
                self.failed_job.emit(job.id, _friendly(exc), traceback.format_exc())
            else:
                self.finished_job.emit(job.id, result)
            finally:
                self._jobs.pop(job.id, None)
                self.current = None
            if self._queue.empty():
                self.idle.emit()

    def _run_job(self, job: Job) -> JobResult:
        from . import hardware

        s = self.settings
        device = s.get("device")
        if device == "auto" and not s.get("use_gpu"):
            device = "cpu"
        plan = hardware.plan_resources(self.hardware, s.get("power"), device,
                                       s.get("threads"), s.get("segment"))
        self.separator.models_dir = s.models_dir

        def progress(stage: str, frac: float):
            self.progress.emit(job.id, stage, frac)

        result = self.separator.run(job.src, job.cfg, plan, progress, job.cancel,
                                    s.stem_cache())
        # Learn how fast this machine is, for future time estimates.
        cost = preset_cost(job.cfg.shifts, job.cfg.overlap)
        if result.audio_seconds > 20 and not result.cached:
            s.record_speed(result.device, job.cfg.model_ref,
                           result.separate_seconds / result.audio_seconds / cost)
        return result


def _friendly(exc: Exception) -> str:
    msg = str(exc).strip() or type(exc).__name__
    low = (type(exc).__name__ + " " + msg).lower()
    if "out of memory" in low:
        return "Ran out of memory. Try the Light power setting in Settings."
    if any(k in low for k in ("connecterror", "name resolution", "network", "timed out",
                              "connection")):
        return "Couldn't download the model. Check your internet connection."
    if "no space left" in low:
        return "The disk is full."
    return msg.splitlines()[0][:300]
