"""Transcription: model on synthetic guitar-like notes, fingering and MIDI."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from guitar_remover import transcribe as T  # noqa: E402

SR = 44100


def pluck(pitch: int, seconds: float) -> np.ndarray:
    """Plucked-string tone (a few harmonics, decaying)."""
    t = np.arange(int(seconds * SR)) / SR
    f = 440 * 2 ** ((pitch - 69) / 12)
    x = sum(np.sin(2 * np.pi * f * k * t) / k ** 1.3 for k in range(1, 6))
    return (x * np.exp(-t * 3) * 0.3).astype(np.float32)


def test_detects_a_melody():
    melody = [(52, 0.5), (55, 1.0), (57, 1.5), (59, 2.0), (62, 2.5), (64, 3.0)]
    audio = np.zeros(int(4 * SR), np.float32)
    for p, t in melody:
        tone = pluck(p, 0.45)
        i = int(t * SR)
        audio[i:i + len(tone)] += tone
    notes = T.notes_from_output(T.model_output(np.stack([audio, audio], 1), SR))
    found = {(n.pitch, round(n.start * 2) / 2) for n in notes}
    hits = sum((p, t) in found for p, t in melody)
    assert hits >= 5, [(n.pitch, round(n.start, 2)) for n in notes]


def test_fingering_is_playable():
    tuning = T.TUNINGS["Standard (E A D G B E)"]
    # E minor chord then a G major chord, then a small riff.
    notes = [T.Note(0, 1, p, 0.8) for p in (40, 47, 52, 55, 59, 64)] + \
            [T.Note(1, 2, p, 0.8) for p in (43, 47, 50, 55, 59, 67)] + \
            [T.Note(2 + i * 0.25, 2.2 + i * 0.25, p, 0.8) for i, p in enumerate((52, 55, 57))]
    fingered = T.fingering(notes, tuning)
    assert len(fingered) == 5
    for group, combo in fingered:
        strings = [s for s, _ in combo]
        assert len(set(strings)) == len(strings)
        for n, (s, f) in zip(sorted(group, key=lambda n: n.pitch), combo):
            assert tuning[s] + f == n.pitch
        fretted = [f for _, f in combo if f > 0]
        assert not fretted or max(fretted) - min(fretted) <= 5
    # The E minor chord should be the open shape.
    assert max(f for _, f in fingered[0][1]) <= 2
    tab = T.render_tab(fingered, tuning, np.arange(0, 4, 0.5), 4, 0, bpm=120)
    assert "e|" in tab and "E|" in tab


def test_midi_file(tmp_path):
    path = T.write_midi(tmp_path / "x", [T.Note(0, 0.5, 60, 0.5), T.Note(0.5, 1, 64, 0.9)])
    data = path.read_bytes()
    assert data[:4] == b"MThd" and b"MTrk" in data and data.endswith(b"\xff\x2f\x00")
