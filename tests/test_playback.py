"""Mixer logic for the player (no audio device needed)."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from guitar_remover.playback import Track, compute_peaks, mix  # noqa: E402
from guitar_remover.ui.player import _reduce_max, _reduce_mean  # noqa: E402


def ramp(n, value):
    return Track("t", None, np.full((n, 2), value, np.float32))


def test_mix_sums_with_gain():
    a, b = ramp(100, 0.1), ramp(100, 0.2)
    b.gain = 0.5
    out, pos, ended = mix([a, b], 0, 10)
    assert np.allclose(out, 0.2) and pos == 10 and not ended


def test_mute_and_solo():
    a, b = ramp(100, 0.1), ramp(100, 0.2)
    a.muted = True
    assert np.allclose(mix([a, b], 0, 5)[0], 0.2)
    a.muted, a.solo = False, True
    assert np.allclose(mix([a, b], 0, 5)[0], 0.1)
    a.muted = True  # muted beats solo
    assert np.allclose(mix([a, b], 0, 5)[0], 0.0)


def test_end_of_song():
    out, pos, ended = mix([ramp(100, 0.1)], 95, 10)
    assert ended and pos == 100
    assert np.allclose(out[:5], 0.1) and np.allclose(out[5:], 0)


def test_loop_wraps_including_exact_boundary():
    t = Track("t", None, np.arange(100, dtype=np.float32).repeat(2).reshape(100, 2) / 1000)
    out, pos, ended = mix([t], 10, 25, loop=(10, 20))
    assert not ended
    assert np.allclose(out[:, 0] * 1000, [*range(10, 20), *range(10, 20), *range(10, 15)])
    assert pos == 15
    # A block that ends exactly on the loop end must wrap on the next block.
    _, pos, _ = mix([t], 10, 10, loop=(10, 20))
    out, pos, _ = mix([t], pos, 3, loop=(10, 20))
    assert np.allclose(out[:, 0] * 1000, [10, 11, 12])


def test_outside_loop_plays_normally():
    t = ramp(100, 0.1)
    _, pos, _ = mix([t], 50, 10, loop=(10, 20))
    assert pos == 60


def test_clipping_guard():
    out, _, _ = mix([ramp(10, 0.8), ramp(10, 0.8)], 0, 10)
    assert out.max() <= 1.0


def test_waveform_reductions():
    t = Track("t", None, np.random.default_rng(0).uniform(-1, 1, (256 * 50, 2)).astype(np.float32))
    compute_peaks(t)
    lo = np.array([0, 3, 3, 10])
    hi = np.array([3, 3 + 1, 10, 50])
    m = _reduce_max(t.peaks, lo, hi)
    assert np.allclose(m, [t.peaks[a:b].max() for a, b in zip(lo, hi)])
    r = _reduce_mean(t.rms, lo, hi)
    assert np.allclose(r, [t.rms[a:b].mean() for a, b in zip(lo, hi)], atol=1e-6)
