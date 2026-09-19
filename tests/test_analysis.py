"""Tempo and beat detection on synthetic drum patterns with a known tempo."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from guitar_remover.analysis import Tempo, analyse, click_track  # noqa: E402

SR = 44100


def drums(bpm: float, seconds: float = 30, start: float = 0.37, seed: int = 0):
    """Kick on beats 1 and 3, snare on 2 and 4, hi-hat on eighths, plus noise."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    x = rng.normal(0, 0.01, n).astype(np.float32)
    beat = 60 / bpm
    t = np.arange(int(0.12 * SR)) / SR
    kick = np.sin(2 * np.pi * 55 * t) * np.exp(-t * 25)
    snare = rng.normal(0, 1, len(t)) * np.exp(-t * 35) * 0.5
    hat = rng.normal(0, 1, len(t)) * np.exp(-t * 120) * 0.15
    k = 0
    while start + k * beat / 2 < seconds - 0.2:
        i = int((start + k * beat / 2) * SR)
        m = min(len(t), n - i)
        x[i:i + m] += hat[:m]
        if k % 2 == 0:
            b = (k // 2) % 4
            x[i:i + m] += (kick if b in (0, 2) else snare)[:m]
        k += 1
    return np.stack([x, x])


@pytest.mark.parametrize("bpm", [70, 84, 100, 120, 128, 150, 174])
def test_tempo_and_beats(bpm):
    tempo = analyse(drums(bpm, seed=bpm), SR)
    # Half or double time is an acceptable reading (the player has x2 and /2
    # buttons); anything else, such as 3:2, is a real error.
    ratio = tempo.bpm / bpm
    assert min(abs(ratio - r) for r in (0.5, 1.0, 2.0)) < 0.03, tempo.bpm
    assert ratio < 1.5, "double time would put beats between the real ones"
    expected = 0.37 + np.arange(0, 80) * 60 / bpm
    expected = expected[expected < 29.5]
    # Most detected beats should be within 30 ms of a real beat.
    err = np.min(np.abs(tempo.beats[:, None] - expected[None, :]), axis=1)
    assert np.mean(err < 0.03) > 0.9


def test_bars_start_on_the_kick():
    tempo = analyse(drums(120), SR)
    first_bar = tempo.bar_starts[0]
    beat = 0.5
    # The pattern's bar starts at 0.37 s + n * 2 s; kick on beats 1 and 3 means
    # a bar start is either there or two beats later, so accept both.
    phase = ((first_bar - 0.37) / beat) % 4
    assert min(abs(phase - 0), abs(phase - 2), abs(phase - 4)) < 0.1


def test_scaling_and_snap():
    t = Tempo(100, np.arange(0, 20, 0.6), 4, 1)
    d = t.scaled(2.0)
    assert d.bpm == 200 and len(d.beats) == 2 * len(t.beats) - 1
    h = d.scaled(0.5)
    assert h.bpm == 100
    assert t.snap(3.1) == pytest.approx(t.bar_starts[np.argmin(abs(t.bar_starts - 3.1))])
    assert Tempo.from_dict(t.to_dict()).bar_offset == 1


def test_click_track():
    c = click_track(np.array([0.0, 0.5]), np.array([True, False]), SR, SR)
    assert c[:100].max() > c[SR // 2:SR // 2 + 100].max() > 0
