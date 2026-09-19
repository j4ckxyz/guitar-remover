"""Real-time multitrack playback: per-track volume, mute and solo, seeking and
looping, all mixed live in the audio callback so changes are heard instantly."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import audio_io

SAMPLERATE = 44100
PEAK_BLOCK = 256  # samples per stored waveform peak


@dataclass
class Track:
    name: str
    path: Path | None
    data: np.ndarray  # (frames, 2) float32, C-contiguous
    gain: float = 1.0
    muted: bool = False
    solo: bool = False
    peaks: np.ndarray | None = None  # max |x| per PEAK_BLOCK
    rms: np.ndarray | None = None  # RMS per PEAK_BLOCK

    @property
    def frames(self) -> int:
        return self.data.shape[0]


def load_track(path: Path, name: str | None = None, samplerate: int = SAMPLERATE) -> Track:
    data = np.ascontiguousarray(audio_io.decode(path, samplerate, 2).T)
    t = Track(name or path.stem, path, data)
    compute_peaks(t)
    return t


def compute_peaks(t: Track) -> None:
    n = t.frames // PEAK_BLOCK
    mono = np.abs(t.data[: n * PEAK_BLOCK]).max(axis=1).reshape(n, PEAK_BLOCK)
    t.peaks = mono.max(axis=1)
    sq = (t.data[: n * PEAK_BLOCK] ** 2).mean(axis=1).reshape(n, PEAK_BLOCK)
    t.rms = np.sqrt(sq.mean(axis=1))


def audible(tracks: list[Track]) -> list[Track]:
    any_solo = any(t.solo for t in tracks)
    return [t for t in tracks if not t.muted and (t.solo or not any_solo)]


def mix(tracks: list[Track], start: int, frames: int, master: float = 1.0,
        loop: tuple[int, int] | None = None, length: int | None = None
        ) -> tuple[np.ndarray, int, bool]:
    """Mix ``frames`` frames starting at ``start``.

    Returns (audio (frames, 2), next position, reached_end). With a loop the
    position wraps from loop end back to loop start, as often as needed."""
    if length is None:
        length = max((t.frames for t in tracks), default=0)
    out = np.zeros((frames, 2), np.float32)
    live = audible(tracks)
    pos, filled, ended = start, 0, False
    looping = loop is not None and loop[0] <= pos < loop[1]
    while filled < frames:
        stop = loop[1] if looping else length
        n = min(frames - filled, stop - pos)
        if n <= 0:
            if looping:
                pos = loop[0]
                continue
            ended = True
            break
        for t in live:
            seg = t.data[pos:pos + n]
            if seg.shape[0]:
                out[filled:filled + seg.shape[0]] += seg * t.gain
        pos += n
        filled += n
    if looping and pos >= loop[1]:
        pos = loop[0]  # so the next block starts inside the loop again
    if master != 1.0:
        out *= master
    np.clip(out, -1.0, 1.0, out=out)
    return out, pos, ended


class Player:
    """Owns the output stream. Thread-safe: UI thread calls the setters, the
    PortAudio thread calls the callback."""

    def __init__(self, tracks: list[Track], samplerate: int = SAMPLERATE):
        self.tracks = tracks
        self.samplerate = samplerate
        self.length = max(t.frames for t in tracks)
        self.position = 0
        self.playing = False
        self.master = 1.0
        self.loop: tuple[int, int] | None = None
        self._lock = threading.Lock()
        self._stream = None
        self.error: str | None = None
        self._pre: np.ndarray | None = None  # count-in clicks played before the music
        self._pre_pos = 0

    # -- stream -------------------------------------------------------------
    def _ensure_stream(self) -> bool:
        if self._stream is not None:
            return True
        try:
            import sounddevice as sd
        except OSError:
            self.error = ("Playback needs the PortAudio library. On Linux install it with "
                          "your package manager, e.g. sudo apt install libportaudio2")
            return False
        except Exception as exc:  # noqa: BLE001
            self.error = f"Audio output is unavailable: {exc}"
            return False
        try:
            self._stream = sd.OutputStream(samplerate=self.samplerate, channels=2,
                                           dtype="float32", callback=self._callback)
        except Exception:
            # Device can't do 44.1 kHz: resample the tracks to what it can do.
            try:
                rate = int(sd.query_devices(kind="output")["default_samplerate"])
                self._resample(rate)
                self._stream = sd.OutputStream(samplerate=rate, channels=2,
                                               dtype="float32", callback=self._callback)
            except Exception as exc:  # noqa: BLE001
                self.error = f"Couldn't open the audio output: {exc}"
                return False
        self._stream.start()
        return True

    def replace_audio(self, datas: list[np.ndarray]) -> None:
        """Swap every track's audio (e.g. after transposing) without a glitch."""
        with self._lock:
            for t, d in zip(self.tracks, datas):
                t.data = d

    def _resample(self, rate: int) -> None:
        ratio = rate / self.samplerate
        for t in self.tracks:
            n = int(t.frames * ratio)
            x = np.linspace(0, t.frames - 1, n)
            t.data = np.ascontiguousarray(np.stack(
                [np.interp(x, np.arange(t.frames), t.data[:, c]) for c in range(2)],
                axis=1).astype(np.float32))
        self.position = int(self.position * ratio)
        if self.loop:
            self.loop = (int(self.loop[0] * ratio), int(self.loop[1] * ratio))
        self.samplerate = rate
        self.length = max(t.frames for t in self.tracks)

    def _callback(self, outdata, frames, _time, _status):
        with self._lock:
            if not self.playing:
                outdata.fill(0)
                return
            done = 0
            if self._pre is not None:
                m = min(frames, len(self._pre) - self._pre_pos)
                outdata[:m] = self._pre[self._pre_pos:self._pre_pos + m] * self.master
                self._pre_pos += m
                done = m
                if self._pre_pos >= len(self._pre):
                    self._pre = None
            if done < frames:
                out, pos, ended = mix(self.tracks, self.position, frames - done, self.master,
                                      self.loop, self.length)
                self.position = pos
                outdata[done:] = out
                if ended:
                    self.playing = False
                    self.position = self.length

    @property
    def counting_in(self) -> bool:
        return self._pre is not None

    def close(self) -> None:
        with self._lock:
            self.playing = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    # -- controls -------------------------------------------------------------
    def play(self, count_in: np.ndarray | None = None) -> bool:
        """Start playing; count_in is optional (frames,) click audio to play first."""
        if not self._ensure_stream():
            return False
        with self._lock:
            if count_in is not None and len(count_in):
                self._pre = np.repeat(count_in[:, None], 2, axis=1).astype(np.float32)
                self._pre_pos = 0
            if self.position >= self.length:
                self.position = self.loop[0] if self.loop else 0
            elif self.loop and not (self.loop[0] <= self.position < self.loop[1]):
                self.position = self.loop[0]
            self.playing = True
        return True

    def pause(self) -> None:
        with self._lock:
            self.playing = False
            self._pre = None

    def seek(self, frame: int) -> None:
        with self._lock:
            self.position = int(min(max(0, frame), self.length))

    def skip(self, seconds: float) -> None:
        self.seek(self.position + int(seconds * self.samplerate))

    def set_loop(self, loop: tuple[int, int] | None) -> None:
        with self._lock:
            if loop and loop[1] - loop[0] < self.samplerate // 20:
                loop = None
            self.loop = loop

    def render(self, start: int = 0, end: int | None = None) -> np.ndarray:
        """The current mix (volumes, mutes, solos) as (2, frames) for export."""
        end = self.length if end is None else end
        out, _, _ = mix(self.tracks, start, end - start, self.master, None, self.length)
        return out.T.copy()
