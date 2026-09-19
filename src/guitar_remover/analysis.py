"""Tempo, beat and bar detection (NumPy only).

Onset strength from spectral flux, tempo from its autocorrelation (weighted
towards common tempos), beats from dynamic programming (Ellis, 2007), and bar
starts from where the low end (kick drum and bass) hits hardest.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

SR = 22050
N_FFT = 2048
HOP = 512
FPS = SR / HOP  # ~43 frames per second


@dataclass
class Tempo:
    bpm: float
    beats: np.ndarray  # beat times in seconds
    beats_per_bar: int = 4
    bar_offset: int = 0  # index of the first beat that starts a bar
    confidence: float = 0.0
    extra: dict = field(default_factory=dict)

    @property
    def bar_starts(self) -> np.ndarray:
        return self.beats[self.bar_offset::self.beats_per_bar]

    def beat_period(self) -> float:
        return 60.0 / self.bpm

    def scaled(self, factor: float) -> "Tempo":
        """Double (2.0) or halve (0.5) the beat grid, e.g. when a song is detected
        at half or double time."""
        b = self.beats
        if factor == 2.0 and len(b) > 1:
            mids = (b[:-1] + b[1:]) / 2
            nb = np.sort(np.concatenate([b, mids]))
            return Tempo(self.bpm * 2, nb, self.beats_per_bar, self.bar_offset * 2,
                         self.confidence)
        if factor == 0.5 and len(b) > 2:
            return Tempo(self.bpm / 2, b[self.bar_offset % 2::2], self.beats_per_bar,
                         self.bar_offset // 2, self.confidence)
        return self

    def to_dict(self) -> dict:
        return {"bpm": self.bpm, "beats": [round(float(t), 4) for t in self.beats],
                "beats_per_bar": self.beats_per_bar, "bar_offset": self.bar_offset,
                "confidence": self.confidence}

    @classmethod
    def from_dict(cls, d: dict) -> "Tempo":
        return cls(d["bpm"], np.asarray(d["beats"], float), d.get("beats_per_bar", 4),
                   d.get("bar_offset", 0), d.get("confidence", 0.0))

    def snap(self, t: float, to: str = "bar") -> float:
        """Nearest bar start (or beat) to time t, in seconds."""
        grid = self.bar_starts if to == "bar" else self.beats
        if len(grid) == 0:
            return t
        return float(grid[np.argmin(np.abs(grid - t))])

    def bar_number(self, t: float) -> float:
        """Bar position of time t, 1-based (1.0 = first bar start)."""
        bars = self.bar_starts
        if len(bars) < 2:
            return 1.0
        return float(np.interp(t, bars, np.arange(1, len(bars) + 1),
                               left=1 + (t - bars[0]) / (bars[1] - bars[0]),
                               right=len(bars) + (t - bars[-1]) / (bars[-1] - bars[-2])))


def to_mono_22k(audio: np.ndarray, samplerate: int) -> np.ndarray:
    """(frames, channels) or (channels, frames) at 44.1/48 kHz -> mono 22.05 kHz."""
    x = audio.mean(axis=1 if audio.shape[0] > audio.shape[-1] else 0).astype(np.float32)
    if samplerate == 2 * SR:
        n = len(x) // 2 * 2
        return x[:n].reshape(-1, 2).mean(axis=1)
    idx = np.arange(0, len(x), samplerate / SR)
    return np.interp(idx, np.arange(len(x)), x).astype(np.float32)


def _stft_mag(x: np.ndarray) -> np.ndarray:
    x = np.pad(x, (N_FFT // 2, N_FFT // 2))
    n = 1 + (len(x) - N_FFT) // HOP
    frames = np.lib.stride_tricks.as_strided(
        x, shape=(n, N_FFT), strides=(x.strides[0] * HOP, x.strides[0]))
    win = np.hanning(N_FFT).astype(np.float32)
    out = np.empty((n, N_FFT // 2 + 1), np.float32)
    for i in range(0, n, 2048):  # chunked to keep memory low
        out[i:i + 2048] = np.abs(np.fft.rfft(frames[i:i + 2048] * win, axis=1))
    return out


def onset_strength(mag: np.ndarray, lo_hz: float = 0, hi_hz: float = SR / 2) -> np.ndarray:
    freqs = np.fft.rfftfreq(N_FFT, 1 / SR)
    band = (freqs >= lo_hz) & (freqs < hi_hz)
    logm = np.log1p(100 * mag[:, band])
    flux = np.maximum(0, np.diff(logm, axis=0, prepend=logm[:1])).mean(axis=1)
    flux -= _moving_average(flux, int(FPS))  # remove slow loudness changes
    flux = np.maximum(flux, 0)
    return flux / (flux.std() + 1e-9)


def _moving_average(x: np.ndarray, n: int) -> np.ndarray:
    if n <= 1:
        return x
    c = np.cumsum(np.pad(x, (n // 2, n - n // 2)), dtype=np.float64)
    return ((c[n:] - c[:-n]) / n)[: len(x)].astype(np.float32)


def estimate_bpm(env: np.ndarray, lo: float = 60, hi: float = 200,
                 prefer: float = 120) -> tuple[float, float]:
    """(bpm, confidence) from the onset envelope's autocorrelation."""
    e = env - env.mean()
    n = len(e)
    spec = np.fft.rfft(e, 2 * n)
    ac = np.fft.irfft(np.abs(spec) ** 2)[:n]
    ac /= ac[0] + 1e-9
    lags = np.arange(1, n // 4)
    bpms = 60 * FPS / lags
    ok = (bpms >= lo) & (bpms <= hi)
    # Reward periods whose half-period is also strong (the in-between eighth
    # notes), which rules out 3:2 mistakes such as reading 150 BPM as 100.
    score_all = ac[lags] + 0.5 * ac[lags // 2]
    # Log-normal preference around a typical tempo.
    weight = np.exp(-0.5 * (np.log2(bpms[ok] / prefer) / 1.0) ** 2)
    score = score_all[ok] * weight
    i = int(np.argmax(score))
    lag = lags[ok][i]
    # Parabolic interpolation for a sub-frame lag.
    if 1 < lag < n - 1:
        y0, y1, y2 = ac[lag - 1], ac[lag], ac[lag + 1]
        denom = y0 - 2 * y1 + y2
        if denom != 0:
            lag = lag + 0.5 * (y0 - y2) / denom
    return float(60 * FPS / lag), float(max(0.0, ac[int(round(lag))]))


def track_beats(env: np.ndarray, bpm: float, tightness: float = 100.0) -> np.ndarray:
    """Beat frames by dynamic programming: beats land on strong onsets while
    staying close to the tempo."""
    period = 60 * FPS / bpm
    local = np.convolve(env, np.exp(-0.5 * (np.arange(-int(period), int(period) + 1)
                                            * 32 / period) ** 2), "same")
    n = len(local)
    score = local.copy()
    back = np.full(n, -1)
    lo, hi = int(round(period / 2)), int(round(2 * period))
    offsets = np.arange(lo, hi + 1)
    penalty = -tightness * np.log(offsets / period) ** 2
    for t in range(lo, n):
        prev = t - offsets
        valid = prev >= 0
        if not valid.any():
            continue
        cand = score[prev[valid]] + penalty[valid]
        j = int(np.argmax(cand))
        if cand[j] > 0:
            score[t] = local[t] + cand[j]
            back[t] = prev[valid][j]
    # Start from the best-scoring frame near the end and walk back.
    tail = max(0, n - int(period) - 1)
    t = tail + int(np.argmax(score[tail:]))
    beats = []
    while t >= 0:
        beats.append(t)
        t = back[t]
    beats = np.array(beats[::-1])
    # Trim beats in silent lead-in / tail.
    if len(beats):
        strong = local[beats] > 0.1 * np.median(local[beats]) if len(beats) > 4 else \
            np.ones(len(beats), bool)
        nz = np.flatnonzero(strong)
        if len(nz):
            beats = beats[nz[0]:nz[-1] + 1]
    return beats


def low_energy(mag: np.ndarray, hi_hz: float = 150) -> np.ndarray:
    """Energy below hi_hz per frame: kick drum and bass notes."""
    freqs = np.fft.rfftfreq(N_FFT, 1 / SR)
    band = (freqs >= 30) & (freqs < hi_hz)
    e = (mag[:, band] ** 2).sum(axis=1)
    return e / (e.max() + 1e-9)


def bar_phase(low: np.ndarray, beats: np.ndarray, beats_per_bar: int) -> int:
    """Which beat starts the bars: the phase where kick and bass are strongest."""
    if len(beats) < beats_per_bar * 2:
        return 0
    strength = np.array([low[b:b + 4].max() - low[max(0, b - 3):b + 1].min()
                         for b in beats])
    scores = [strength[p::beats_per_bar].mean() for p in range(beats_per_bar)]
    return int(np.argmax(scores))


def analyse(audio: np.ndarray, samplerate: int, beats_per_bar: int = 4) -> Tempo:
    """Detect tempo, beats and bars for (channels, frames) or (frames, channels) audio."""
    x = to_mono_22k(audio, samplerate)
    if len(x) < SR * 4:
        return Tempo(120.0, np.zeros(0), beats_per_bar, 0, 0.0)
    mag = _stft_mag(x)
    env = onset_strength(mag)
    low = low_energy(mag)
    bpm, conf = estimate_bpm(env)
    beat_frames = track_beats(env, bpm)
    times = beat_frames / FPS
    if len(times) > 8:
        # Report the tempo the beat grid actually follows.
        bpm = float(60 / np.median(np.diff(times)))
    phase = bar_phase(low, beat_frames, beats_per_bar)
    return Tempo(bpm, times, beats_per_bar, phase, conf)


def click_track(times: np.ndarray, accents: np.ndarray, length: int,
                samplerate: int) -> np.ndarray:
    """Mono click audio: short sine blips, higher and louder on accented beats."""
    out = np.zeros(length, np.float32)
    n = int(0.035 * samplerate)
    t = np.arange(n) / samplerate
    env = np.exp(-t * 90).astype(np.float32)
    hi = (0.9 * np.sin(2 * np.pi * 1760 * t) * env).astype(np.float32)
    lo = (0.6 * np.sin(2 * np.pi * 1175 * t) * env).astype(np.float32)
    for when, accent in zip(times, accents):
        i = int(round(when * samplerate))
        if 0 <= i < length:
            blip = hi if accent else lo
            m = min(n, length - i)
            out[i:i + m] += blip[:m]
    return out
