"""Model catalog, downloading and loading.

Built-in models come from the official Demucs repositories on the Hugging Face
hub. We download them ourselves (instead of letting demucs do it) so the UI can
show real download progress and so the files live in a folder the user controls.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

HF_NAMESPACE = "adefossez"


@dataclass(frozen=True)
class ModelInfo:
    name: str
    title: str
    description: str
    size_mb: int
    has_guitar: bool
    passes: int = 1  # models in the bag -> relative cost

    @property
    def guitar_stem(self) -> str:
        return "guitar" if self.has_guitar else "other"


CATALOG: dict[str, ModelInfo] = {m.name: m for m in [
    ModelInfo("htdemucs_6s", "6-stem (recommended)",
              "Separates guitar and piano individually. The best choice for guitar.",
              55, True),
    ModelInfo("htdemucs_ft", "4-stem fine-tuned",
              "Highest quality 4-stem model, 4× slower. Guitar comes from the "
              "'other' stem, so keys and synths are removed too.",
              336, False, passes=4),
    ModelInfo("htdemucs", "4-stem",
              "Fast 4-stem model. Guitar comes from the 'other' stem, so keys "
              "and synths are removed too.",
              84, False),
    ModelInfo("hdemucs_mmi", "Hybrid Demucs v3",
              "Older hybrid model (4 stems, 'other' used as guitar).",
              167, False),
    ModelInfo("mdx_extra", "MDX extra",
              "Older 4-model bag from the MDX challenge (4 stems, 'other' used "
              "as guitar).",
              669, False, passes=4),
]}
DEFAULT_MODEL = "htdemucs_6s"


def hf_repo_name(name: str) -> str:
    if name == "htdemucs":
        return "HTDemucs"
    if name.startswith("htdemucs_"):
        return "HTDemucs-" + name[len("htdemucs_"):]
    return "Demucs-" + name


class Cancelled(Exception):
    pass


def default_models_dir() -> Path:
    from PySide6.QtCore import QStandardPaths
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    if not base:
        base = str(Path.home() / ".guitar-remover")
    return Path(base) / "models"


def _parse_ref(ref: str) -> tuple[str, str]:
    """'htdemucs_6s' or 'someone/htdemucs_6s' or 'hf://someone/name' -> (repo_id, name)."""
    ref = ref.removeprefix("hf://").strip()
    namespace = HF_NAMESPACE
    if "/" in ref:
        namespace, ref = ref.split("/", 1)
    return f"{namespace}/{hf_repo_name(ref)}", ref


def model_dir(models_dir: Path, ref: str) -> Path:
    repo_id, _ = _parse_ref(ref)
    return models_dir / repo_id.replace("/", "--")


def is_downloaded(models_dir: Path, ref: str) -> bool:
    if Path(ref).is_dir():
        return True
    d = model_dir(models_dir, ref)
    return (d / "complete.json").exists()


def downloaded_size(models_dir: Path) -> int:
    if not models_dir.exists():
        return 0
    return sum(f.stat().st_size for f in models_dir.rglob("*") if f.is_file())


def delete_downloads(models_dir: Path) -> None:
    if models_dir.exists():
        shutil.rmtree(models_dir, ignore_errors=True)


def download(models_dir: Path, ref: str,
             progress: Callable[[int, int], None] | None = None,
             cancel: threading.Event | None = None) -> Path:
    """Download a model bag (yaml + safetensors) into ``models_dir``."""
    import httpx
    import yaml
    from huggingface_hub import HfApi, hf_hub_url

    repo_id, name = _parse_ref(ref)
    target = model_dir(models_dir, ref)
    if (target / "complete.json").exists():
        return target
    target.mkdir(parents=True, exist_ok=True)

    info = HfApi().model_info(repo_id, files_metadata=True)
    sizes = {s.rfilename: (s.size or 0) for s in info.siblings}
    yaml_name = f"{name}.yaml"
    if yaml_name not in sizes:
        raise RuntimeError(f"{repo_id} does not look like a Demucs model (no {yaml_name}).")

    def fetch(fname: str, done_before: int, total: int) -> int:
        dest = target / fname
        if dest.exists() and dest.stat().st_size == sizes.get(fname, -1):
            return sizes[fname]
        tmp = dest.with_suffix(dest.suffix + ".part")
        got = 0
        with httpx.stream("GET", hf_hub_url(repo_id, fname), follow_redirects=True,
                          timeout=httpx.Timeout(30, read=120)) as r:
            r.raise_for_status()
            with open(tmp, "wb") as fh:
                for chunk in r.iter_bytes(1 << 20):
                    if cancel is not None and cancel.is_set():
                        raise Cancelled()
                    fh.write(chunk)
                    got += len(chunk)
                    if progress:
                        progress(done_before + got, total)
        os.replace(tmp, dest)
        return got

    fetch(yaml_name, 0, 0)
    bag = yaml.safe_load((target / yaml_name).read_text())
    files = [f"{sig}.safetensors" for sig in bag["models"]]
    total = sum(sizes.get(f, 0) for f in files)
    done = 0
    for f in files:
        done += fetch(f, done, total)
    (target / "complete.json").write_text(json.dumps({"repo": repo_id, "name": name}))
    return target


def load(models_dir: Path, ref: str):
    """Load a model bag. ``ref`` is a catalog name, an HF 'namespace/name', or a
    local folder containing Demucs .th/.yaml files (like `demucs --repo`)."""
    from demucs.apply import BagOfModels

    local = Path(ref)
    if local.is_dir():
        from demucs.pretrained import get_model
        yamls = sorted(local.glob("*.yaml"))
        if not yamls:
            raise RuntimeError(f"No model .yaml file found in {local}")
        return get_model(yamls[0].stem, repo=local)

    import yaml
    from demucs.hf import load_safetensors_model

    _, name = _parse_ref(ref)
    target = model_dir(models_dir, ref)
    bag = yaml.safe_load((target / f"{name}.yaml").read_text())
    models = [load_safetensors_model(target / f"{sig}.safetensors") for sig in bag["models"]]
    model = BagOfModels(models, bag.get("weights"), bag.get("segment"))
    model.eval()
    return model
