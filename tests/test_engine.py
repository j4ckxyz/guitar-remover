"""Engine edge cases: cancel, bad input, 4-stem model.

Needs a real song:  TEST_SONG=/path/to/song.mp3 python -m pytest tests"""
import os
import sys
import threading
from pathlib import Path

import pytest

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from guitar_remover import hardware, separator  # noqa: E402
from guitar_remover.models import Cancelled  # noqa: E402

SONG = Path(os.environ.get("TEST_SONG", ""))
pytestmark = pytest.mark.skipif(not SONG.is_file(), reason="set TEST_SONG to an audio file")
MODELS = Path(os.environ.get("MODELS_DIR", Path(__file__).parents[1] / ".models-cache"))


@pytest.fixture(scope="module")
def sep():
    return separator.Separator(MODELS)


@pytest.fixture(scope="module")
def plan():
    return hardware.plan_resources(hardware.detect())


def test_cancel_midway(sep, plan, tmp_path):
    cancel = threading.Event()
    seen = []

    def prog(stage, frac):
        seen.append(frac)
        if stage == "Separating" and frac > 0.3:
            cancel.set()

    cfg = separator.JobConfig(shifts=0, overlap=0.1, output_dir=tmp_path)
    with pytest.raises(Cancelled):
        sep.run(SONG, cfg, plan, prog, cancel)
    assert not list(tmp_path.rglob("*.wav")), "cancelled job must not write files"
    # The separator must still work after a cancel.
    res = sep.run(SONG, cfg, plan, lambda *a: None, threading.Event())
    assert res.guitar.exists() and res.backing.exists()


def test_not_audio(sep, plan, tmp_path):
    bad = tmp_path / "notes.mp3"
    bad.write_text("definitely not audio")
    cfg = separator.JobConfig(output_dir=tmp_path)
    with pytest.raises(RuntimeError, match="Couldn't read"):
        sep.run(bad, cfg, plan, lambda *a: None, threading.Event())


def test_short_clip(sep, plan, tmp_path):
    from guitar_remover import audio_io
    import numpy as np
    clip = tmp_path / "clip.wav"
    audio_io.write(clip, np.zeros((2, 44100 * 3), np.float32) + 0.01, 44100, "wav16")
    res = sep.run(clip, separator.JobConfig(output_dir=tmp_path / "o"), plan,
                  lambda *a: None, threading.Event())
    assert res.audio_seconds == pytest.approx(3, abs=0.05)


def test_four_stem_model(plan, tmp_path):
    sep4 = separator.Separator(MODELS)
    cfg = separator.JobConfig(model_ref="htdemucs", shifts=0, overlap=0.1, output_dir=tmp_path)
    res = sep4.run(SONG, cfg, plan, lambda *a: None, threading.Event())
    assert res.guitar.name.endswith("Other.wav")
    assert res.backing.exists()
