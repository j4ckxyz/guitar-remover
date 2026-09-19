"""Transpose audio by semitones without changing its speed (Signalsmith Stretch)."""
from __future__ import annotations

import numpy as np


def transpose(audio: np.ndarray, samplerate: int, semitones: float) -> np.ndarray:
    """(frames, channels) float32 -> same shape, pitch-shifted. Length and timing
    are preserved (the library compensates for its own latency)."""
    if semitones == 0:
        return audio
    import python_stretch as ps

    s = ps.Signalsmith.Stretch()
    s.preset(audio.shape[1], float(samplerate))
    s.setTransposeSemitones(float(semitones))
    out = s.process(np.ascontiguousarray(audio.T, dtype=np.float32))
    return np.ascontiguousarray(out.T[: audio.shape[0]], dtype=np.float32)


def describe(semitones: int) -> str:
    """Friendly label, e.g. '+1 semitone (E♭ tuning → standard)'."""
    if semitones == 0:
        return "Original key"
    sign = "+" if semitones > 0 else "−"
    n = abs(semitones)
    text = f"{sign}{n} semitone{'s' if n != 1 else ''}"
    hint = {1: "for songs in E♭ tuning", 2: "for songs in D tuning",
            -1: "down a half step", 12: "up an octave", -12: "down an octave"}.get(semitones)
    return f"{text} ({hint})" if hint else text
