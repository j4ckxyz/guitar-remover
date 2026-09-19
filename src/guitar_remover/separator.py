"""The separation engine: song in, guitar track + backing track out."""
from __future__ import annotations

import gc
import math
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from . import audio_io, models
from .hardware import ResourcePlan, set_low_priority
from .models import Cancelled

# Speed-vs-quality slider positions -> (shifts, overlap, label, blurb)
QUALITY_PRESETS = [
    (0, 0.10, "Fastest", "Quick preview quality"),
    (0, 0.25, "Fast", "Good quality, quick"),
    (1, 0.25, "Balanced", "Great quality for most songs"),
    (2, 0.25, "High", "Cleaner separation, about 2× slower"),
    (3, 0.50, "Best", "Maximum quality, about 6× slower"),
]
DEFAULT_QUALITY = 2


def preset_cost(shifts: int, overlap: float) -> float:
    return max(1, shifts) / (1.0 - overlap)


@dataclass
class JobConfig:
    model_ref: str = models.DEFAULT_MODEL
    target_stem: str = "auto"  # "auto" -> guitar, or "other" for 4-stem models
    shifts: int = 1
    overlap: float = 0.25
    backing_method: str = "stems"  # "stems": sum of other stems, "residual": original - guitar
    also_remove: tuple[str, ...] = ()  # e.g. ("vocals",) for a karaoke-style backing track
    export_stems: bool = False
    fmt: str = "wav24"
    clip_mode: str = "shared_gain"  # "shared_gain" | "clamp" | "none"
    output_dir: Path = field(default_factory=Path.home)
    folder_per_song: bool = True
    keep_tags: bool = True


@dataclass
class JobResult:
    guitar: Path
    backing: Path
    stems: list[Path]
    folder: Path
    seconds: float
    audio_seconds: float
    separate_seconds: float  # model time only, for speed estimates
    device: str
    notes: list[str]


ProgressFn = Callable[[str, float], None]  # (stage text, 0..1 or -1 for busy)


class Separator:
    """Keeps a model loaded between jobs so a queue of songs runs quickly."""

    def __init__(self, models_dir: Path):
        self.models_dir = models_dir
        self._model = None
        self._model_ref: str | None = None
        self._lock = threading.Lock()

    # -- model management ------------------------------------------------
    def ensure_model(self, ref: str, progress: ProgressFn, cancel: threading.Event):
        if self._model is not None and self._model_ref == ref:
            return self._model
        self.unload()
        if not models.is_downloaded(self.models_dir, ref):
            def dl(done: int, total: int):
                if total:
                    progress(f"Downloading model · {done / 1e6:.0f} of {total / 1e6:.0f} MB",
                             done / total)
            progress("Downloading model…", -1)
            models.download(self.models_dir, ref, dl, cancel)
        progress("Loading model…", -1)
        self._model = models.load(self.models_dir, ref)
        self._model_ref = ref
        return self._model

    def unload(self):
        self._model = None
        self._model_ref = None
        _free_memory()

    # -- the actual work -------------------------------------------------
    def run(self, src: Path, cfg: JobConfig, plan: ResourcePlan,
            progress: ProgressFn, cancel: threading.Event) -> JobResult:
        import torch

        with self._lock:
            t0 = time.monotonic()
            set_low_priority(plan.low_priority)
            torch.set_num_threads(plan.threads)
            notes = list(plan.notes)

            model = self.ensure_model(cfg.model_ref, progress, cancel)
            sources = list(model.sources)
            target = cfg.target_stem
            if target == "auto":
                target = "guitar" if "guitar" in sources else "other"
            if target not in sources:
                raise RuntimeError(f"This model has no '{target}' stem "
                                   f"(it has: {', '.join(sources)}).")
            removed = {target, *[s for s in cfg.also_remove if s in sources]}

            progress("Reading audio…", -1)
            if cancel.is_set():
                raise Cancelled()
            mix_np = audio_io.decode(src, model.samplerate, model.audio_channels)
            tags = audio_io.read_tags(src) if cfg.keep_tags else {}
            length = mix_np.shape[1]
            audio_seconds = length / model.samplerate
            if length < model.samplerate // 10:
                raise RuntimeError("This file is too short to separate.")

            mix = torch.from_numpy(mix_np)
            ref = mix.mean(0)
            mean, std = float(ref.mean()), float(ref.std()) or 1.0
            mix = ((mix - mean) / std)[None]  # (1, C, T), stays on CPU

            want_stems = sources if cfg.export_stems else []
            device = plan.device
            t_sep = time.monotonic()
            try:
                outs = self._separate(model, mix, sources, target, removed, want_stems,
                                      cfg, plan, device, progress, cancel)
            except (Cancelled, MemoryError):
                raise
            except RuntimeError as exc:
                if device == "cpu" or "out of memory" in str(exc).lower() and device == "cuda":
                    raise
                # Some GPUs/drivers lack an op: retry on the CPU instead of failing.
                notes.append(f"GPU failed ({str(exc).splitlines()[0][:80]}), used CPU instead.")
                device = "cpu"
                _free_memory()
                outs = self._separate(model, mix, sources, target, removed, want_stems,
                                      cfg, plan, device, progress, cancel)
            del mix
            t_sep = time.monotonic() - t_sep
            progress("Saving…", -1)

            # Undo normalisation.
            for k in outs:
                outs[k] = outs[k] * std + mean
            guitar = outs.pop("_guitar")
            if cfg.backing_method == "residual":
                backing = mix_np - guitar
                for s in removed - {target}:
                    backing -= outs.get(f"_rm_{s}", 0)
            else:
                backing = outs.pop("_backing")
            for k in [k for k in outs if k.startswith("_rm_")]:
                outs.pop(k)

            # Keep guitar + backing at matched levels so they still add up.
            tracks = {"guitar": guitar, "backing": backing, **outs}
            if cfg.clip_mode == "shared_gain" and cfg.fmt != "wav32f":
                peak = max(float(np.abs(a).max()) for a in tracks.values())
                if peak > 0.999:
                    gain = 0.999 / peak
                    for a in tracks.values():
                        a *= gain
                    notes.append(f"Lowered volume by {-20 * math.log10(gain):.1f} dB "
                                 "to avoid clipping.")
            elif cfg.clip_mode == "clamp":
                for a in tracks.values():
                    np.clip(a, -1, 1, out=a)

            song = audio_io.safe_filename(src.stem)
            folder = Path(cfg.output_dir)
            if cfg.folder_per_song:
                folder = folder / song
            sr = model.samplerate

            def tagged(suffix: str) -> dict[str, str]:
                if not tags:
                    return {}
                t = dict(tags)
                t["title"] = f"{tags.get('title', song)} ({suffix})"
                return t

            guitar_label = "Guitar" if target == "guitar" else target.title()
            backing_label = "Backing Track" + (" (no vocals)" if "vocals" in removed else "")
            g_path = audio_io.write(folder / f"{song} - {guitar_label}", guitar, sr, cfg.fmt,
                                    tagged(guitar_label))
            b_path = audio_io.write(folder / f"{song} - {backing_label}", backing, sr, cfg.fmt,
                                    tagged(backing_label))
            stem_paths = []
            for name, audio in outs.items():
                stem_paths.append(audio_io.write(
                    folder / "Stems" / f"{song} - {name.title()}", audio, sr, cfg.fmt,
                    tagged(name.title())))
            del tracks, guitar, backing, outs, mix_np
            _free_memory()
            set_low_priority(False)
            return JobResult(g_path, b_path, stem_paths, folder,
                             time.monotonic() - t0, audio_seconds, t_sep, device, notes)

    def _separate(self, model, mix, sources, target, removed, want_stems,
                  cfg: JobConfig, plan: ResourcePlan, device: str,
                  progress: ProgressFn, cancel: threading.Event) -> dict[str, np.ndarray]:
        """Run the model block by block, keeping only the tracks we need."""
        import torch
        from demucs.apply import BagOfModels, TensorChunk, apply_model

        sr = model.samplerate
        _, channels, length = mix.shape
        sub_models = model.models if isinstance(model, BagOfModels) else [model]
        max_seg = min(float(getattr(m, "segment", 1e9)) for m in sub_models)
        segment = min(plan.segment, max_seg) if plan.segment else None
        seg_len = int(sr * (segment or max_seg if max_seg < 1e9 else 10.0))
        stride = max(1, int((1 - cfg.overlap) * seg_len))

        # Blocks are aligned to the window stride and padded by whole windows on
        # both sides, so inside its core region each block sees exactly the same
        # windows as one full-length pass would. Only a short crossfade is needed
        # (for random time shifts, which differ between blocks).
        block = max(1, round(plan.block_seconds * sr / stride)) * stride
        pad = math.ceil(seg_len / stride) * stride
        xf = min(int(0.5 * sr), stride)
        cuts = list(range(block, length, block))
        if cuts and length - cuts[-1] < block // 4:
            cuts.pop()  # fold a short tail into the previous block
        bounds = [0, *cuts, length]
        spans = []  # (process_start, process_end, keep_start, keep_end)
        for i in range(len(bounds) - 1):
            ks = bounds[i] - (xf // 2 if i > 0 else 0)
            ke = bounds[i + 1] + (xf // 2 if i < len(bounds) - 2 else 0)
            spans.append((max(0, bounds[i] - pad), min(length, bounds[i + 1] + pad), ks, ke))

        extra = int(0.5 * sr) if cfg.shifts else 0
        passes = max(1, cfg.shifts) * len(sub_models)
        total = sum(math.ceil((e - s + extra / 2) / stride) for s, e, _, _ in spans) * passes
        done = 0
        lock = threading.Lock()

        def cb(d: dict):
            nonlocal done
            if cancel.is_set():
                raise Cancelled()
            if d.get("state") == "end":
                with lock:
                    done += 1
                    frac = min(0.99, done / total)
                progress("Separating", frac)

        idx = {s: i for i, s in enumerate(sources)}
        keep_idx = [idx[s] for s in sources if s not in removed]
        rm_extra = [s for s in removed if s != target]
        out: dict[str, np.ndarray] = {"_guitar": np.zeros((channels, length), np.float32)}
        if cfg.backing_method == "stems":
            out["_backing"] = np.zeros((channels, length), np.float32)
        else:
            for s in rm_extra:
                out[f"_rm_{s}"] = np.zeros((channels, length), np.float32)
        for s in want_stems:
            out[s] = np.zeros((channels, length), np.float32)

        progress("Separating", 0.0)
        last = len(spans) - 1
        for i, (s, e, ks, ke) in enumerate(spans):
            n = ke - ks
            w = np.ones(n, np.float32)
            if i > 0:
                w[:xf] = np.linspace(0, 1, xf, dtype=np.float32)
            if i < last:
                w[-xf:] = np.linspace(1, 0, xf, dtype=np.float32)
            # TensorChunk also lets the model pad with real audio past the edges.
            chunk = TensorChunk(mix, s, e - s)
            with torch.inference_mode():
                est = apply_model(model, chunk, shifts=cfg.shifts, split=True,
                                  overlap=cfg.overlap, device=device,
                                  num_workers=plan.workers, segment=segment,
                                  callback=cb, progress=False)[0]
            est = est[..., ks - s:ke - s].numpy()  # (sources, C, n)
            out["_guitar"][:, ks:ke] += est[idx[target]] * w
            if "_backing" in out:
                out["_backing"][:, ks:ke] += est[keep_idx].sum(0) * w
            for st in rm_extra:
                if f"_rm_{st}" in out:
                    out[f"_rm_{st}"][:, ks:ke] += est[idx[st]] * w
            for st in want_stems:
                out[st][:, ks:ke] += est[idx[st]] * w
            del est
            if device != "cpu":
                _empty_device_cache()
        return out


def _empty_device_cache():
    try:
        import torch
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _free_memory():
    gc.collect()
    _empty_device_cache()
