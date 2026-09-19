"""Headless end-to-end run with a memory watchdog (safe for 8 GB machines).

usage: python tests/run_headless.py SONG [quality 0-4] [model] [out_dir] [extra k=v ...]
"""
import os
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import psutil  # noqa: E402

from guitar_remover import hardware, separator  # noqa: E402

LIMIT = float(os.environ.get("RSS_LIMIT_GB", "3")) * 1024 ** 3
peak = 0


def watchdog():
    global peak
    p = psutil.Process()
    while True:
        rss = p.memory_info().rss
        peak = max(peak, rss)
        if rss > LIMIT:
            print(f"\n!! RSS {rss / 1e9:.2f} GB over limit, aborting", flush=True)
            os._exit(3)
        time.sleep(0.2)


threading.Thread(target=watchdog, daemon=True).start()

song = Path(sys.argv[1])
q = int(sys.argv[2]) if len(sys.argv) > 2 else 0
model = sys.argv[3] if len(sys.argv) > 3 else "htdemucs_6s"
out = Path(sys.argv[4]) if len(sys.argv) > 4 else Path("/tmp/gr-out")
extra = dict(a.split("=", 1) for a in sys.argv[5:])

hw = hardware.detect()
plan = hardware.plan_resources(hw, int(extra.get("power", 1)),
                               device_override=extra.get("device", "auto"))
if "block" in extra:
    plan.block_seconds = float(extra["block"])
print(hw.summary)
print(plan)
shifts, overlap, label, _ = separator.QUALITY_PRESETS[q]
cfg = separator.JobConfig(model_ref=model, shifts=shifts, overlap=overlap, output_dir=out,
                          fmt=extra.get("fmt", "wav24"),
                          backing_method=extra.get("method", "stems"),
                          export_stems=extra.get("stems") == "1",
                          also_remove=tuple(filter(None, extra.get("remove", "").split(","))))
sep = separator.Separator(Path(os.environ.get("MODELS_DIR", Path(__file__).parents[1] / ".models-cache")))
last = [0.0, ""]


def prog(stage, frac):
    now = time.monotonic()
    if stage != last[1] or now - last[0] > 2 or frac >= 0.99:
        print(f"  {stage:50s} {frac * 100 if frac >= 0 else -1:6.1f}%  "
              f"rss={psutil.Process().memory_info().rss / 1e9:.2f}GB", flush=True)
        last[:] = [now, stage]


cache = None
if "cache" in extra:
    from guitar_remover.stem_cache import StemCache
    cache = StemCache(Path(extra["cache"]), 2 * 1024 ** 3)
res = sep.run(song, cfg, plan, prog, threading.Event(), cache)
print(res)
print(f"speed: {res.audio_seconds / res.seconds:.2f}x realtime, peak RSS {peak / 1e9:.2f} GB")
