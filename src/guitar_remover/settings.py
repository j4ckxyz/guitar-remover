"""User settings, stored natively (plist on macOS, registry on Windows)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths

from . import APP_NAME, models
from .hardware import POWER_BALANCED
from .separator import DEFAULT_QUALITY, QUALITY_PRESETS, JobConfig


def default_output_dir() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
    return Path(base or Path.home() / "Downloads") / APP_NAME


# key -> default. Types are taken from the defaults.
DEFAULTS: dict[str, object] = {
    "quality": DEFAULT_QUALITY,
    "output_dir": "",  # empty -> default_output_dir()
    "save_next_to_original": False,
    "folder_per_song": True,
    "format": "wav24",
    "remove_vocals": False,
    "auto_start": True,
    "reveal_when_done": False,
    "open_player_when_done": True,
    "power": POWER_BALANCED,
    "use_gpu": True,
    # advanced
    "model": models.DEFAULT_MODEL,
    "target_stem": "auto",
    "custom_quality": False,
    "shifts": 1,
    "overlap": 0.25,
    "segment": 0.0,
    "backing_method": "stems",
    "clip_mode": "shared_gain",
    "export_stems": False,
    "device": "auto",
    "threads": 0,
    "models_dir": "",
    "keep_tags": True,
    "cache_gb": 3,  # space for remembered separations; 0 turns it off
    "cache_dir": "",
    "update_mode": "ask",  # "ask" | "auto" | "off"
    "update_last_check": 0.0,
    "update_skip": "",
}


class Settings:
    def __init__(self):
        self._q = QSettings()

    def get(self, key: str):
        default = DEFAULTS[key]
        value = self._q.value(key, default)
        # QSettings (plist/registry/ini) may hand back strings.
        if isinstance(default, bool):
            return value if isinstance(value, bool) else str(value).lower() in ("1", "true")
        if isinstance(default, int):
            try:
                return int(value)
            except (TypeError, ValueError):
                return default
        if isinstance(default, float):
            try:
                return float(value)
            except (TypeError, ValueError):
                return default
        return value if value is not None else default

    def set(self, key: str, value) -> None:
        self._q.setValue(key, value)

    def reset(self) -> None:
        perf = {k: self._q.value(k) for k in self._q.allKeys() if k.startswith("perf/")}
        self._q.clear()
        for k, v in perf.items():
            self._q.setValue(k, v)

    # -- derived values ----------------------------------------------------
    @property
    def output_dir(self) -> Path:
        return Path(self.get("output_dir") or default_output_dir())

    @property
    def cache_dir(self) -> Path:
        from .stem_cache import default_cache_dir
        return Path(self.get("cache_dir") or default_cache_dir())

    def stem_cache(self):
        from .stem_cache import GB, StemCache
        return StemCache(self.cache_dir, int(self.get("cache_gb") * GB))

    @property
    def models_dir(self) -> Path:
        return Path(self.get("models_dir") or models.default_models_dir())

    def quality_params(self) -> tuple[int, float]:
        if self.get("custom_quality"):
            return self.get("shifts"), self.get("overlap")
        shifts, overlap, *_ = QUALITY_PRESETS[self.get("quality")]
        return shifts, overlap

    def job_config(self, src: Path) -> JobConfig:
        shifts, overlap = self.quality_params()
        out = src.parent if self.get("save_next_to_original") else self.output_dir
        return JobConfig(
            model_ref=self.get("model"),
            target_stem=self.get("target_stem"),
            shifts=shifts,
            overlap=overlap,
            backing_method=self.get("backing_method"),
            also_remove=("vocals",) if self.get("remove_vocals") else (),
            export_stems=self.get("export_stems"),
            fmt=self.get("format"),
            clip_mode=self.get("clip_mode"),
            output_dir=out,
            folder_per_song=self.get("folder_per_song"),
            keep_tags=self.get("keep_tags"),
        )

    # -- learned speed, used for time estimates ---------------------------
    def seconds_per_audio_second(self, device: str, model: str) -> float:
        """Processing seconds per second of audio at cost 1.0."""
        guess = {"cuda": 0.04, "mps": 0.15, "cpu": 0.30}.get(device, 0.3)
        passes = models.CATALOG[model].passes if model in models.CATALOG else 1
        v = self._q.value(f"perf/{device}/{model}")
        try:
            return float(v)
        except (TypeError, ValueError):
            return guess * passes

    def record_speed(self, device: str, model: str, value: float) -> None:
        old = self._q.value(f"perf/{device}/{model}")
        try:
            value = 0.6 * float(old) + 0.4 * value
        except (TypeError, ValueError):
            pass
        self._q.setValue(f"perf/{device}/{model}", value)
