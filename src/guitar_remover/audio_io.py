"""Decoding and encoding audio with a bundled ffmpeg (via imageio-ffmpeg)."""
from __future__ import annotations


import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

AUDIO_EXTENSIONS = {
    ".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".aif",
    ".aiff", ".wma", ".alac", ".mp4", ".m4v", ".mov", ".mkv", ".webm",
}

FORMATS = {
    # key: (label, extension)
    "wav16": ("WAV · 16-bit (CD quality)", ".wav"),
    "wav24": ("WAV · 24-bit (studio)", ".wav"),
    "wav32f": ("WAV · 32-bit float (no clipping)", ".wav"),
    "flac": ("FLAC · lossless, smaller files", ".flac"),
    "mp3": ("MP3 · 320 kbps, smallest files", ".mp3"),
}

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW


def ffmpeg_exe() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def probe_duration(path: str | os.PathLike) -> float | None:
    """Duration in seconds using ffmpeg's banner (no ffprobe needed)."""
    try:
        out = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)],
                             capture_output=True, text=True, creationflags=_NO_WINDOW,
                             timeout=30, errors="replace")
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", out.stderr)
        if m:
            h, mnt, s = m.groups()
            return int(h) * 3600 + int(mnt) * 60 + float(s)
    except Exception:
        pass
    return None


def decode(path: str | os.PathLike, samplerate: int, channels: int = 2) -> np.ndarray:
    """Decode any audio/video file to float32 array of shape (channels, samples)."""
    cmd = [ffmpeg_exe(), "-nostdin", "-hide_banner", "-loglevel", "error",
           "-i", str(path), "-map", "0:a:0", "-vn",
           "-f", "f32le", "-acodec", "pcm_f32le",
           "-ac", str(channels), "-ar", str(samplerate), "-"]
    proc = subprocess.run(cmd, capture_output=True, creationflags=_NO_WINDOW)
    if proc.returncode != 0 or not proc.stdout:
        msg = proc.stderr.decode(errors="replace").strip().splitlines()
        raise RuntimeError("Couldn't read this file as audio." +
                           (f"\n{msg[-1]}" if msg else ""))
    data = np.frombuffer(proc.stdout, dtype=np.float32)
    return data.reshape(-1, channels).T.copy()


def read_tags(path: str | os.PathLike) -> dict[str, str]:
    """Best-effort title/artist tags so outputs keep useful metadata."""
    try:
        out = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path),
                              "-f", "ffmetadata", "-"],
                             capture_output=True, text=True, creationflags=_NO_WINDOW,
                             timeout=30, errors="replace")
        tags = {}
        for line in out.stdout.splitlines():
            if "=" in line and not line.startswith(";"):
                k, v = line.split("=", 1)
                if k.lower() in ("title", "artist", "album", "date", "genre"):
                    tags[k.lower()] = v
        return tags
    except Exception:
        return {}


def write(path: Path, audio: np.ndarray, samplerate: int, fmt: str,
          tags: dict[str, str] | None = None) -> Path:
    """Write (channels, samples) float32 audio. Returns the final path."""
    path = path.with_suffix(FORMATS[fmt][1])
    path.parent.mkdir(parents=True, exist_ok=True)
    interleaved = np.ascontiguousarray(audio.T, dtype=np.float32)
    tmp = path.with_name(path.stem + ".part" + path.suffix)
    if fmt == "mp3":
        cmd = [ffmpeg_exe(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
               "-f", "f32le", "-ar", str(samplerate), "-ac", str(audio.shape[0]),
               "-i", "-", "-codec:a", "libmp3lame", "-b:a", "320k"]
        for k, v in (tags or {}).items():
            cmd += ["-metadata", f"{k}={v}"]
        cmd += [str(tmp)]
        proc = subprocess.run(cmd, input=interleaved.tobytes(), capture_output=True,
                              creationflags=_NO_WINDOW)
        if proc.returncode != 0:
            raise RuntimeError("MP3 encoding failed: " + proc.stderr.decode(errors="replace"))
    else:
        import soundfile as sf
        subtype = {"wav16": "PCM_16", "wav24": "PCM_24", "wav32f": "FLOAT", "flac": "PCM_24"}[fmt]
        with sf.SoundFile(str(tmp), "w", samplerate=samplerate, channels=audio.shape[0],
                          subtype=subtype, format="FLAC" if fmt == "flac" else "WAV") as f:
            for k, v in (tags or {}).items():
                try:
                    setattr(f, k if k != "date" else "date", v)
                except Exception:
                    pass
            f.write(interleaved)
    os.replace(tmp, path)
    return path


def safe_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name[:150] or "Untitled"


