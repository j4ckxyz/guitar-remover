"""Remembers separated parts, so changing settings or exporting again is instant.

Each entry holds every part the model produced, as 24-bit FLAC (peak-normalised,
with the gain stored alongside), keyed by the song's content and the settings
that affect separation. Oldest-used entries are removed past the size limit.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path

import numpy as np

CACHE_VERSION = 1
GB = 1024 ** 3


def default_cache_dir() -> Path:
    from PySide6.QtCore import QStandardPaths
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
    return Path(base or Path(tempfile.gettempdir()) / "guitar-remover") / "separations"


def fingerprint(path: Path) -> str:
    """Content fingerprint: size plus the first and last 4 MB. Survives renames
    and moves, and is quick even for huge files."""
    h = hashlib.sha1()
    size = path.stat().st_size
    h.update(str(size).encode())
    with open(path, "rb") as fh:
        h.update(fh.read(4 << 20))
        if size > 8 << 20:
            fh.seek(-(4 << 20), os.SEEK_END)
            h.update(fh.read())
    return h.hexdigest()


def make_key(src: Path, model_ref: str, shifts: int, overlap: float,
             segment: float | None) -> str:
    parts = [CACHE_VERSION, fingerprint(src), model_ref, shifts, round(overlap, 4),
             round(segment or 0, 3)]
    return hashlib.sha1(json.dumps(parts).encode()).hexdigest()[:24]


class LazyStems(Mapping):
    """Cached parts, each decoded only when used, so only one or two are in
    memory at a time."""

    def __init__(self, folder: Path, meta: dict):
        self.folder, self.meta = folder, meta

    def __getitem__(self, name: str) -> np.ndarray:
        import soundfile as sf
        if name not in self.meta["sources"]:
            raise KeyError(name)
        data, _ = sf.read(str(self.folder / f"{name}.flac"), dtype="float32", always_2d=True)
        out = np.ascontiguousarray(data.T)
        out *= np.float32(self.meta["gains"][name])
        return out

    def __iter__(self):
        return iter(self.meta["sources"])

    def __len__(self) -> int:
        return len(self.meta["sources"])


class StemCache:
    def __init__(self, root: Path, limit_bytes: int):
        self.root = Path(root)
        self.limit = limit_bytes

    @property
    def enabled(self) -> bool:
        return self.limit > 0

    def _dir(self, key: str) -> Path:
        return self.root / key

    def get(self, key: str) -> dict | None:
        """{'samplerate', 'sources', 'stems': {name: (C, T) float32}} or None."""
        d = self._dir(key)
        meta_path = d / "meta.json"
        if not self.enabled or not meta_path.exists():
            return None
        try:
            meta = json.loads(meta_path.read_text())
            if not all((d / f"{n}.flac").exists() for n in meta["sources"]):
                raise ValueError("incomplete")
        except Exception:
            shutil.rmtree(d, ignore_errors=True)  # corrupt entry: forget it
            return None
        os.utime(meta_path)  # mark as recently used
        return {"samplerate": meta["samplerate"], "sources": meta["sources"],
                "stems": LazyStems(d, meta)}

    def put(self, key: str, samplerate: int, stems: dict[str, np.ndarray],
            info: dict | None = None) -> None:
        if not self.enabled:
            return
        import soundfile as sf
        d = self._dir(key)
        tmp = d.with_name(d.name + ".part")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        gains = {}
        for name, audio in stems.items():
            peak = max(float(np.abs(audio[:, i:i + samplerate * 30]).max())
                       for i in range(0, audio.shape[1], samplerate * 30))
            gain = max(peak, 1e-6) / 0.999
            gains[name] = gain
            with sf.SoundFile(str(tmp / f"{name}.flac"), "w", samplerate=samplerate,
                              channels=audio.shape[0], subtype="PCM_24", format="FLAC") as f:
                step = samplerate * 30  # write in chunks to keep memory flat
                for i in range(0, audio.shape[1], step):
                    f.write((audio[:, i:i + step] / gain).T.astype(np.float32))
        meta = {"samplerate": samplerate, "sources": list(stems), "gains": gains,
                "created": time.time(), **(info or {})}
        (tmp / "meta.json").write_text(json.dumps(meta, indent=1))
        shutil.rmtree(d, ignore_errors=True)
        os.replace(tmp, d)
        self.evict()

    def entries(self) -> list[tuple[Path, float, int]]:
        out = []
        if self.root.exists():
            for d in self.root.iterdir():
                meta = d / "meta.json"
                if d.is_dir() and meta.exists():
                    size = sum(f.stat().st_size for f in d.iterdir() if f.is_file())
                    out.append((d, meta.stat().st_mtime, size))
        return out

    def size(self) -> int:
        return sum(s for _, _, s in self.entries())

    def evict(self) -> None:
        entries = sorted(self.entries(), key=lambda e: e[1])  # oldest first
        total = sum(s for _, _, s in entries)
        while entries and total > self.limit:
            d, _, s = entries.pop(0)
            shutil.rmtree(d, ignore_errors=True)
            total -= s
        # Leftovers from interrupted writes.
        if self.root.exists():
            for d in self.root.glob("*.part"):
                if time.time() - d.stat().st_mtime > 3600:
                    shutil.rmtree(d, ignore_errors=True)

    def clear(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
