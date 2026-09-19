"""Guitar to notes, tab and MIDI.

Notes come from Spotify's Basic Pitch model (Apache 2.0, bundled as ONNX in
data/). The pre- and post-processing below is a NumPy port of basic_pitch's
inference.py and note_creation.py, so the heavy TensorFlow/librosa stack isn't
needed. Tab fingering is our own: a Viterbi search that keeps chord shapes
playable and the hand moving as little as possible.
"""
from __future__ import annotations

import itertools
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# Basic Pitch constants
SR = 22050
FFT_HOP = 256
AUDIO_N_SAMPLES = SR * 2 - FFT_HOP  # 43844
ANNOT_FPS = SR // FFT_HOP  # 86
ANNOT_N_FRAMES = ANNOT_FPS * 2  # 172
N_OVERLAP_FRAMES = 30
OVERLAP_LEN = N_OVERLAP_FRAMES * FFT_HOP
HOP_SIZE = AUDIO_N_SAMPLES - OVERLAP_LEN
MIDI_OFFSET = 21
MAX_FREQ_IDX = 87

MODEL_PATH = Path(__file__).parent / "data" / "basic_pitch_nmp.onnx"

TUNINGS = {  # low string to high string, MIDI note numbers
    "Standard (E A D G B E)": [40, 45, 50, 55, 59, 64],
    "E♭ standard (half step down)": [39, 44, 49, 54, 58, 63],
    "Drop D (D A D G B E)": [38, 45, 50, 55, 59, 64],
    "D standard (whole step down)": [38, 43, 48, 53, 57, 62],
    "Drop C♯ (C♯ G♯ C♯ F♯ A♯ D♯)": [37, 44, 49, 54, 58, 63],
    "Drop C (C G C F A D)": [36, 43, 48, 53, 57, 62],
    "Open G (D G D G B D)": [38, 43, 50, 55, 59, 62],
    "DADGAD": [38, 45, 50, 55, 57, 62],
}
NOTE_NAMES = ["C", "C♯", "D", "E♭", "E", "F", "F♯", "G", "A♭", "A", "B♭", "B"]
MAX_FRET = 22


@dataclass
class Note:
    start: float  # seconds
    end: float
    pitch: int  # MIDI
    velocity: float  # 0..1


# ---------------------------------------------------------------- model
def model_output(audio: np.ndarray, samplerate: int, threads: int = 0,
                 progress=None) -> dict[str, np.ndarray]:
    """Run Basic Pitch on audio ((frames, channels) or mono). Returns frame,
    onset and contour activations, each (n_frames, bins)."""
    import onnxruntime as ort

    from .analysis import to_mono_22k
    x = audio if audio.ndim == 1 else None
    if x is None:
        x = to_mono_22k(audio, samplerate)
    elif samplerate != SR:
        x = to_mono_22k(np.stack([x, x]), samplerate)
    original_length = len(x)
    x = np.concatenate([np.zeros(OVERLAP_LEN // 2, np.float32), x.astype(np.float32)])
    windows = []
    for i in range(0, len(x), HOP_SIZE):
        w = x[i:i + AUDIO_N_SAMPLES]
        if len(w) < AUDIO_N_SAMPLES:
            w = np.pad(w, (0, AUDIO_N_SAMPLES - len(w)))
        windows.append(w)
    opts = ort.SessionOptions()
    if threads:
        opts.intra_op_num_threads = threads
    sess = ort.InferenceSession(str(MODEL_PATH), opts, providers=["CPUExecutionProvider"])
    outputs = {"note": [], "onset": [], "contour": []}
    names = ["StatefulPartitionedCall:1", "StatefulPartitionedCall:2",
             "StatefulPartitionedCall:0"]
    batch = 8
    for b in range(0, len(windows), batch):
        inp = np.stack(windows[b:b + batch])[..., None]
        note, onset, contour = sess.run(names, {"serving_default_input_2:0": inp})
        outputs["note"].append(note)
        outputs["onset"].append(onset)
        outputs["contour"].append(contour)
        if progress:
            progress(min(1.0, (b + batch) / len(windows)))
    n_frames = int(np.floor(original_length * (ANNOT_FPS / SR)))
    half = N_OVERLAP_FRAMES // 2
    result = {"rms": _frame_rms(x[OVERLAP_LEN // 2:], n_frames)}
    for k, parts in outputs.items():
        arr = np.concatenate(parts)[:, half:-half, :]
        result[k] = arr.reshape(-1, arr.shape[-1])[:n_frames]
    return result


def _frame_rms(x: np.ndarray, n_frames: int) -> np.ndarray:
    """Loudness per model frame, used to ignore faint bleed from other instruments."""
    n = n_frames * FFT_HOP
    x = np.pad(x[:n], (0, max(0, n - len(x))))
    return np.sqrt((x.reshape(n_frames, FFT_HOP) ** 2).mean(axis=1) + 1e-12)


def _frames_to_time(n_frames: int) -> np.ndarray:
    original = np.arange(n_frames) * FFT_HOP / SR
    window_numbers = np.floor(np.arange(n_frames) / ANNOT_N_FRAMES)
    window_offset = (FFT_HOP / SR) * (ANNOT_N_FRAMES - (AUDIO_N_SAMPLES / FFT_HOP)) + 0.0018
    return original - window_offset * window_numbers


def _hz_to_idx(hz: float) -> int:
    return int(np.round(12 * np.log2(hz / 440.0) + 69 - MIDI_OFFSET))


def _inferred_onsets(onsets: np.ndarray, frames: np.ndarray, n_diff: int = 2) -> np.ndarray:
    diffs = []
    for n in range(1, n_diff + 1):
        padded = np.concatenate([np.zeros((n, frames.shape[1])), frames])
        diffs.append(padded[n:, :] - padded[:-n, :])
    frame_diff = np.min(diffs, axis=0)
    frame_diff[frame_diff < 0] = 0
    frame_diff[:n_diff, :] = 0
    if frame_diff.max() > 0:
        frame_diff = onsets.max() * frame_diff / frame_diff.max()
    return np.maximum(onsets, frame_diff)


def notes_from_output(out: dict[str, np.ndarray], onset_thresh: float = 0.5,
                      frame_thresh: float = 0.3, min_note_ms: float = 80.0,
                      min_hz: float = 70.0, max_hz: float = 1400.0,
                      melodia_trick: bool = True, energy_tol: int = 11,
                      gate_db: float = -35.0) -> list[Note]:
    """Decode activations to notes (port of basic_pitch output_to_notes_polyphonic).
    Notes quieter than gate_db below the track's loud passages are dropped: on a
    separated guitar track those are bleed from other instruments."""
    frames = out["note"].astype(np.float64).copy()
    onsets = out["onset"].astype(np.float64).copy()
    n_frames = frames.shape[0]
    if n_frames < 3:
        return []
    min_len = int(np.round(min_note_ms / 1000 * (SR / FFT_HOP)))
    hi, lo = _hz_to_idx(max_hz), _hz_to_idx(min_hz)
    onsets[:, hi:] = 0
    frames[:, hi:] = 0
    onsets[:, :lo] = 0
    frames[:, :lo] = 0
    onsets = _inferred_onsets(onsets, frames)

    # Local maxima in time (scipy.signal.argrelmax along axis 0).
    peaks = np.zeros_like(onsets, dtype=bool)
    peaks[1:-1] = (onsets[1:-1] > onsets[:-2]) & (onsets[1:-1] > onsets[2:])
    t_idx, f_idx = np.where(peaks & (onsets >= onset_thresh))
    order = np.argsort(t_idx, kind="stable")[::-1]  # backwards in time
    t_idx, f_idx = t_idx[order], f_idx[order]

    energy = frames.copy()
    events = []
    for start, f in zip(t_idx, f_idx):
        if start >= n_frames - 1:
            continue
        i, k = start + 1, 0
        while i < n_frames - 1 and k < energy_tol:
            k = k + 1 if energy[i, f] < frame_thresh else 0
            i += 1
        i -= k
        if i - start <= min_len:
            continue
        energy[start:i, f] = 0
        if f < MAX_FREQ_IDX:
            energy[start:i, f + 1] = 0
        if f > 0:
            energy[start:i, f - 1] = 0
        events.append((start, i, f + MIDI_OFFSET, float(frames[start:i, f].mean())))

    if melodia_trick:
        while energy.max() > frame_thresh:
            mid, f = np.unravel_index(int(np.argmax(energy)), energy.shape)
            energy[mid, f] = 0
            i, k = mid + 1, 0
            while i < n_frames - 1 and k < energy_tol:
                k = k + 1 if energy[i, f] < frame_thresh else 0
                energy[i, f] = 0
                if f < MAX_FREQ_IDX:
                    energy[i, f + 1] = 0
                if f > 0:
                    energy[i, f - 1] = 0
                i += 1
            end = i - 1 - k
            i, k = mid - 1, 0
            while i > 0 and k < energy_tol:
                k = k + 1 if energy[i, f] < frame_thresh else 0
                energy[i, f] = 0
                if f < MAX_FREQ_IDX:
                    energy[i, f + 1] = 0
                if f > 0:
                    energy[i, f - 1] = 0
                i -= 1
            start = i + 1 + k
            if end - start <= min_len:
                continue
            events.append((start, end, f + MIDI_OFFSET, float(frames[start:end, f].mean())))

    if "rms" in out and len(out["rms"]):
        rms = out["rms"]
        floor = np.percentile(rms, 99) * 10 ** (gate_db / 20)
        events = [ev for ev in events if rms[ev[0]:max(ev[1], ev[0] + 1)].max() >= floor]
    times = _frames_to_time(n_frames + 1)
    notes = [Note(float(times[s]), float(times[min(e, n_frames)]), int(p), a)
             for s, e, p, a in events]
    notes.sort(key=lambda n: (n.start, n.pitch))
    return notes


def detail_to_thresholds(detail: float) -> tuple[float, float, float]:
    """Map the 'Fewer notes ↔ More notes' slider (0..1) to
    (onset threshold, frame threshold, minimum note length in ms)."""
    d = float(np.clip(detail, 0, 1))
    return 0.7 - 0.4 * d, 0.4 - 0.2 * d, 140 - 90 * d


# ------------------------------------------------------------------ tab
Position = tuple[int, int]  # (string index 0 = lowest, fret)


def _positions(pitch: int, tuning: list[int]) -> list[Position]:
    return [(s, pitch - open_) for s, open_ in enumerate(tuning)
            if 0 <= pitch - open_ <= MAX_FRET]


def _chord_candidates(pitches: list[int], tuning: list[int], limit: int = 24):
    """Playable fingerings of a chord: one string per note, stretch ≤ 5 frets."""
    options = [_positions(p, tuning) for p in pitches]
    if any(not o for o in options):
        return []
    found = []
    for combo in itertools.product(*options):
        strings = [s for s, _ in combo]
        if len(set(strings)) != len(strings):
            continue
        fretted = [f for _, f in combo if f > 0]
        span = (max(fretted) - min(fretted)) if fretted else 0
        if span > 5:
            continue
        found.append(combo)
        if len(found) > 400:
            break
    found.sort(key=lambda c: _shape_cost(c))
    return found[:limit]


def _hand(combo) -> float:
    fretted = [f for _, f in combo if f > 0]
    return float(np.mean(fretted)) if fretted else -1.0  # -1: open strings only


def _shape_cost(combo) -> float:
    fretted = [f for _, f in combo if f > 0]
    span = (max(fretted) - min(fretted)) if fretted else 0
    height = np.mean(fretted) if fretted else 0
    return span * 1.5 + height * 0.15


def group_chords(notes: list[Note], window: float = 0.035) -> list[list[Note]]:
    groups: list[list[Note]] = []
    for n in notes:
        if groups and n.start - groups[-1][0].start <= window:
            if all(n.pitch != m.pitch for m in groups[-1]):
                groups[-1].append(n)
        else:
            groups.append([n])
    return groups


def fingering(notes: list[Note], tuning: list[int]) -> list[tuple[list[Note], tuple]]:
    """Choose a string and fret for every note (Viterbi over note groups).
    Notes outside the guitar's range are dropped."""
    groups = []
    for g in group_chords(notes):
        g = sorted(g, key=lambda n: n.pitch)[:6]
        g = [n for n in g if _positions(n.pitch, tuning)]
        cands = _chord_candidates([n.pitch for n in g], tuning) if g else []
        while g and not cands:  # unplayable chord: drop the least confident note
            g.remove(min(g, key=lambda n: n.velocity))
            cands = _chord_candidates([n.pitch for n in g], tuning) if g else []
        if g:
            groups.append((g, cands))
    if not groups:
        return []

    def move(a, b) -> float:
        ha, hb = _hand(a), _hand(b)
        if ha < 0 or hb < 0:  # open strings: free to move
            return 0.0
        return abs(ha - hb) * 0.6 + (2.0 if abs(ha - hb) > 4 else 0.0)

    cost = [np.array([_shape_cost(c) for c in groups[0][1]])]
    back = []
    for i in range(1, len(groups)):
        prev, cur = groups[i - 1][1], groups[i][1]
        m = np.array([[cost[-1][a] + move(prev[a], c) for a in range(len(prev))]
                      for c in cur])
        back.append(m.argmin(axis=1))
        cost.append(m.min(axis=1) + np.array([_shape_cost(c) for c in cur]))
    j = int(np.argmin(cost[-1]))
    picks = [j]
    for b in reversed(back):
        j = int(b[j])
        picks.append(j)
    picks.reverse()
    return [(g, cands[k]) for (g, cands), k in zip(groups, picks)]


def note_name(pitch: int) -> str:
    return NOTE_NAMES[pitch % 12]


def render_tab(fingered, tuning: list[int], beats: np.ndarray | None,
               beats_per_bar: int = 4, bar_offset: int = 0, title: str = "",
               tuning_name: str = "", bpm: float | None = None,
               bars_per_line: int = 2, subdivisions: int = 4) -> str:
    """ASCII tab laid out in bars, with each beat split into `subdivisions` slots."""
    if beats is None or len(beats) < 2:
        period = 0.5
        last = max((g[0][-1].end for g in fingered), default=0) + 2
        beats = np.arange(0, last, period)
        bar_offset = 0
    beats = np.asarray(beats, float)
    # Extend the grid backwards/forwards so every note has a slot.
    period = float(np.median(np.diff(beats)))
    first_note = min((g[0][0].start for g in fingered), default=beats[0])
    last_note = max((g[0][0].start for g in fingered), default=beats[-1])
    pre = []
    t = beats[0] - period
    while t > first_note - period:
        pre.insert(0, t)
        t -= period
    post = []
    t = beats[-1] + period
    while t < last_note + period * beats_per_bar:
        post.append(t)
        t += period
    bar_offset = (bar_offset + len(pre)) % beats_per_bar
    grid = np.concatenate([pre, beats, post])
    beat_pos = np.arange(len(grid), dtype=float)

    n_strings = len(tuning)
    total_slots = int(np.ceil((len(grid) - bar_offset) / beats_per_bar)) * beats_per_bar \
        * subdivisions
    cells = [["" for _ in range(total_slots)] for _ in range(n_strings)]
    for group, combo in fingered:
        b = float(np.interp(group[0].start, grid, beat_pos))
        slot = int(round((b - bar_offset) * subdivisions))
        if 0 <= slot < total_slots:
            for s, f in combo:
                cells[s][slot] = str(f)

    slots_per_bar = beats_per_bar * subdivisions
    n_bars = total_slots // slots_per_bar
    # Skip empty bars at the start and end.
    used = [any(cells[s][i] for s in range(n_strings)
                for i in range(b * slots_per_bar, (b + 1) * slots_per_bar))
            for b in range(n_bars)]
    if not any(used):
        return "No guitar notes were found."
    first_bar = used.index(True)
    last_bar = n_bars - 1 - used[::-1].index(True)

    names = [note_name(p) for p in tuning]
    names = [n.lower() if i == n_strings - 1 and n == "E" else n for i, n in enumerate(names)]
    width = max(len(n) for n in names)
    lines = []
    if title:
        lines.append(title)
    meta = [f"Tuning: {tuning_name or ' '.join(names)}"]
    if bpm:
        meta.append(f"Tempo: about {bpm:.0f} BPM, {beats_per_bar}/4")
    lines.append(" · ".join(meta))
    lines.append("Transcribed automatically by Guitar Remover (Basic Pitch). "
                 "Check it by ear: fast passages and bends are approximate.")
    lines.append("")
    for line_start in range(first_bar, last_bar + 1, bars_per_line):
        bars = range(line_start, min(line_start + bars_per_line, last_bar + 1))
        lines.append(" " * (width + 1) + "".join(
            f"{b - first_bar + 1:<{slots_per_bar * 3 + 1}}" for b in bars).rstrip())
        for s in reversed(range(n_strings)):  # high string on top
            row = names[s].rjust(width) + "|"
            for b in bars:
                for i in range(b * slots_per_bar, (b + 1) * slots_per_bar):
                    cell = cells[s][i]
                    row += cell.rjust(2, "-") + "-" if cell else "---"
                row += "|"
            lines.append(row)
        lines.append("")
    return "\n".join(lines)


# ----------------------------------------------------------------- MIDI
def _varlen(n: int) -> bytes:
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.insert(0, (n & 0x7F) | 0x80)
        n >>= 7
    return bytes(out)


def write_midi(path: Path, notes: list[Note], bpm: float = 120.0,
               program: int = 27, name: str = "Guitar") -> Path:
    """Standard MIDI file (type 0). program 27 = Electric Guitar (clean)."""
    ppq = 480
    tick = lambda s: int(round(s * bpm / 60 * ppq))  # noqa: E731
    events = []
    for n in notes:
        vel = int(np.clip(40 + n.velocity * 87, 1, 127))
        events.append((tick(n.start), 1, n.pitch, vel))
        events.append((tick(max(n.end, n.start + 0.03)), 0, n.pitch, 0))
    events.sort(key=lambda e: (e[0], e[1]))  # note-offs before note-ons at a tick
    track = bytearray()
    track += b"\x00\xff\x03" + _varlen(len(name.encode())) + name.encode()
    tempo = int(round(60_000_000 / bpm))
    track += b"\x00\xff\x51\x03" + tempo.to_bytes(3, "big")
    track += b"\x00\xc0" + bytes([program])
    now = 0
    for t, on, pitch, vel in events:
        track += _varlen(t - now) + bytes([0x90 if on else 0x80, pitch, vel])
        now = t
    track += b"\x00\xff\x2f\x00"
    data = b"MThd" + struct.pack(">IHHH", 6, 0, 1, ppq) + \
        b"MTrk" + struct.pack(">I", len(track)) + bytes(track)
    path = Path(path).with_suffix(".mid")
    path.write_bytes(data)
    return path
