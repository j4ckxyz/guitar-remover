"""Hardware detection and resource planning.

The app never asks people for thread counts or segment sizes. Instead we look at
the machine (CPU cores, RAM, GPU) and a single "power" level the user picks
(Light / Balanced / Maximum) and derive a concrete plan from it.
"""
from __future__ import annotations

import os
import platform
from dataclasses import dataclass, field

import psutil

GB = 1024 ** 3

# Power levels exposed in the UI.
POWER_LIGHT, POWER_BALANCED, POWER_MAX = 0, 1, 2
POWER_LABELS = {
    POWER_LIGHT: "Light",
    POWER_BALANCED: "Balanced",
    POWER_MAX: "Maximum",
}


@dataclass
class Hardware:
    os_name: str
    arch: str
    cpu_name: str
    physical_cores: int
    logical_cores: int
    total_ram: int
    gpu_kind: str | None = None  # "cuda" | "mps" | None
    gpu_name: str | None = None
    gpu_memory: int | None = None  # dedicated VRAM (None for unified memory)
    gpu_vendor: str = "NVIDIA"  # "AMD" for ROCm builds of PyTorch

    @property
    def summary(self) -> str:
        ram = f"{self.total_ram / GB:.0f} GB memory"
        if self.gpu_kind == "cuda":
            vram = f", {self.gpu_memory / GB:.0f} GB" if self.gpu_memory else ""
            return f"{self.gpu_name}{vram} · {self.physical_cores} CPU cores · {ram}"
        if self.gpu_kind == "mps":
            return f"{self.gpu_name} GPU · {self.physical_cores} CPU cores · {ram}"
        return f"{self.cpu_name} · {self.physical_cores} CPU cores · {ram}"


@dataclass
class ResourcePlan:
    device: str  # "cuda", "mps" or "cpu"
    threads: int  # torch intra-op threads
    workers: int  # parallel chunks (CPU only)
    segment: float | None  # seconds per model window, None = model default
    block_seconds: float  # how much audio we hold in flight at once
    low_priority: bool
    notes: list[str] = field(default_factory=list)
    gpu_vendor: str = "NVIDIA"

    @property
    def device_label(self) -> str:
        return {"cuda": f"{self.gpu_vendor} GPU", "mps": "Apple GPU", "cpu": "CPU"}[self.device]


def _cpu_name() -> str:
    system = platform.system()
    try:
        if system == "Darwin":
            import subprocess
            out = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                                 capture_output=True, text=True, timeout=2)
            if out.stdout.strip():
                return out.stdout.strip()
        elif system == "Windows":
            import winreg  # type: ignore[import-not-found]
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                 r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            return winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
        elif system == "Linux":
            with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.lower().startswith(("model name", "hardware", "cpu model")):
                        return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return platform.processor() or "CPU"


_HW: Hardware | None = None


def detect() -> Hardware:
    """Detect hardware once (importing torch is the slow part)."""
    global _HW
    if _HW is not None:
        return _HW
    import torch

    hw = Hardware(
        os_name=platform.system(),
        arch=platform.machine(),
        cpu_name=_cpu_name(),
        physical_cores=psutil.cpu_count(logical=False) or os.cpu_count() or 1,
        logical_cores=psutil.cpu_count(logical=True) or os.cpu_count() or 1,
        total_ram=psutil.virtual_memory().total,
    )
    try:
        if torch.cuda.is_available():
            props = torch.cuda.get_device_properties(0)
            hw.gpu_kind, hw.gpu_name, hw.gpu_memory = "cuda", props.name, props.total_memory
            if getattr(torch.version, "hip", None):
                hw.gpu_vendor = "AMD"
        elif torch.backends.mps.is_available():
            hw.gpu_kind, hw.gpu_name = "mps", hw.cpu_name.replace("Apple ", "Apple ")
    except Exception:
        pass
    _HW = hw
    return hw


def available_devices(hw: Hardware) -> list[str]:
    devs = ["cpu"]
    if hw.gpu_kind:
        devs.insert(0, hw.gpu_kind)
    return devs


def plan_resources(hw: Hardware, power: int = POWER_BALANCED,
                   device_override: str = "auto",
                   threads_override: int = 0,
                   segment_override: float = 0.0) -> ResourcePlan:
    """Turn the hardware + a power level into concrete settings."""
    notes: list[str] = []
    avail_ram = psutil.virtual_memory().available

    # --- device -----------------------------------------------------------
    device = "cpu"
    if device_override != "auto":
        device = device_override if device_override in available_devices(hw) else "cpu"
    elif hw.gpu_kind == "cuda":
        if (hw.gpu_memory or 0) >= 2 * GB:
            device = "cuda"
        else:
            notes.append("GPU has under 2 GB of memory, using the CPU instead.")
    elif hw.gpu_kind == "mps":
        device = "mps"

    # --- CPU threads ------------------------------------------------------
    share = {POWER_LIGHT: 0.4, POWER_BALANCED: 0.75, POWER_MAX: 1.0}[power]
    cores = hw.physical_cores
    if device != "cpu":
        # The GPU does the heavy lifting; CPU only feeds it.
        threads = max(1, min(4, round(cores * share)))
    else:
        threads = max(1, round(cores * share))
        if power == POWER_MAX and hw.logical_cores > cores:
            threads = hw.logical_cores
    if threads_override > 0:
        threads = threads_override

    # --- parallel chunks on CPU (costs memory, only on big machines) ------
    workers = 0
    if device == "cpu" and power == POWER_MAX and cores >= 8 and hw.total_ram >= 16 * GB:
        workers = 2

    # --- model window size ------------------------------------------------
    # Smaller windows need less memory but give the model less context.
    segment: float | None = None
    if device == "cuda" and (hw.gpu_memory or 0) < 4 * GB:
        segment = 5.0
        notes.append("Using shorter windows to fit in GPU memory.")
    if segment_override > 0:
        segment = segment_override

    # --- how much of the song to process at once --------------------------
    # Holding every stem of a long song in memory is what blows up small
    # machines, so we work through the song in blocks sized to free RAM.
    # Each second of audio in flight costs ~2 MB per 6-stem model output.
    free_gb = avail_ram / GB
    if hw.total_ram <= 8 * GB or free_gb < 3:
        block = 120.0
    elif hw.total_ram <= 16 * GB:
        block = 300.0
    else:
        block = 600.0
    if power == POWER_LIGHT:
        block = min(block, 90.0)

    return ResourcePlan(device=device, threads=threads, workers=workers,
                        segment=segment, block_seconds=block,
                        low_priority=(power == POWER_LIGHT), notes=notes,
                        gpu_vendor=hw.gpu_vendor)


def set_low_priority(enabled: bool) -> None:
    """Lower (or restore) this process's scheduling priority."""
    try:
        proc = psutil.Process()
        if platform.system() == "Windows":
            proc.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if enabled
                      else psutil.NORMAL_PRIORITY_CLASS)
        elif enabled:
            # POSIX can only lower priority without privileges.
            if proc.nice() < 10:
                proc.nice(10)
    except Exception:
        pass
