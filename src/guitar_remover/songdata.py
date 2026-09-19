"""Per-song memory: saved loops, tempo corrections and transpose, keyed by the
audio file's content so it survives renames and moves."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .stem_cache import fingerprint


def default_dir() -> Path:
    from PySide6.QtCore import QStandardPaths
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    return Path(base or Path.home() / ".guitar-remover") / "songs"


class SongData:
    def __init__(self, audio_path: Path, folder: Path | None = None):
        self.folder = folder or default_dir()
        try:
            self.id = fingerprint(Path(audio_path))
        except OSError:
            self.id = None
        self.data: dict = {"loops": [], "tempo": None, "transpose": 0, "volumes": {}}
        if self.id and self.path.exists():
            try:
                self.data.update(json.loads(self.path.read_text()))
            except (OSError, ValueError):
                pass

    @property
    def path(self) -> Path:
        return self.folder / f"{self.id}.json"

    def save(self) -> None:
        if not self.id:
            return
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1))
        os.replace(tmp, self.path)

    # -- loops ------------------------------------------------------------------
    @property
    def loops(self) -> list[dict]:
        return self.data["loops"]

    def add_loop(self, name: str, start: float, end: float) -> dict:
        loop = {"name": name, "start": round(start, 4), "end": round(end, 4)}
        self.loops.append(loop)
        self.loops.sort(key=lambda lp: lp["start"])
        self.save()
        return loop

    def remove_loop(self, loop: dict) -> None:
        if loop in self.loops:
            self.loops.remove(loop)
            self.save()

    def rename_loop(self, loop: dict, name: str) -> None:
        loop["name"] = name
        self.save()

    def set(self, key: str, value) -> None:
        self.data[key] = value
        self.save()
