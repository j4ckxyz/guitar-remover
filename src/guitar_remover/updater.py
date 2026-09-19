"""Updates from GitHub releases.

How an update is applied depends on how the app was installed:

- "macos" / "windows" / "linux": a packaged app. The new build is downloaded and
  verified against the release's SHA256SUMS.txt, unpacked next to the current
  one, and a small helper script swaps it in once the app has quit (running
  programs can't replace themselves on every OS), then optionally relaunches.
- "uv": installed from source with the one-command installer. The helper runs
  `uv tool install` for the new version's tag once the app has quit.
- "source": running from a git checkout; we only tell you to `git pull`.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import GITHUB_REPO, __version__

API = os.environ.get("GR_UPDATE_API",
                     f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest")
ASSETS = {
    "macos": "Guitar-Remover-macOS-arm64.dmg",
    "windows": "Guitar-Remover-Windows-x64.zip",
    "linux": "Guitar-Remover-Linux-x86_64.tar.gz",
}


@dataclass
class Release:
    version: str
    notes: str
    url: str
    assets: dict[str, str] = field(default_factory=dict)  # name -> download URL


class UpdateError(Exception):
    pass


def parse_version(v: str) -> tuple[int, ...]:
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", v.strip())
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


def is_newer(candidate: str, current: str = __version__) -> bool:
    return parse_version(candidate) > parse_version(current)


def install_kind() -> str:
    if getattr(sys, "frozen", False):
        return {"darwin": "macos", "win32": "windows"}.get(sys.platform, "linux")
    if "uv" in Path(sys.prefix).parts and "tools" in Path(sys.prefix).parts:
        return "uv"
    return "source"


def app_location() -> Path:
    """What gets replaced: the .app bundle, or the folder holding the executable."""
    exe = Path(sys.executable).resolve()
    if sys.platform == "darwin":
        for parent in exe.parents:
            if parent.suffix == ".app":
                return parent
    return exe.parent


def latest_release(timeout: float = 10.0) -> Release:
    import httpx
    try:
        r = httpx.get(API, timeout=timeout, follow_redirects=True,
                      headers={"Accept": "application/vnd.github+json",
                               "User-Agent": f"GuitarRemover/{__version__}"})
        r.raise_for_status()
        data = r.json()
    except Exception as exc:  # noqa: BLE001
        raise UpdateError(f"Couldn't check for updates: {exc}") from exc
    return Release(version=data["tag_name"].lstrip("v"), notes=data.get("body") or "",
                   url=data.get("html_url", ""),
                   assets={a["name"]: a["browser_download_url"] for a in data.get("assets", [])})


def _download(url: str, dest: Path, progress: Callable[[int, int], None] | None) -> None:
    import httpx
    with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(30, read=120),
                      headers={"User-Agent": f"GuitarRemover/{__version__}"}) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        got = 0
        with open(dest, "wb") as fh:
            for chunk in r.iter_bytes(1 << 20):
                fh.write(chunk)
                got += len(chunk)
                if progress:
                    progress(got, total)


def _verify(release: Release, name: str, path: Path) -> None:
    sums_url = release.assets.get("SHA256SUMS.txt")
    if not sums_url:
        return  # older releases had no checksum file
    sums = path.parent / "SHA256SUMS.txt"
    _download(sums_url, sums, None)
    expected = {line.split()[1].lstrip("*"): line.split()[0]
                for line in sums.read_text().splitlines() if len(line.split()) == 2}
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    if expected.get(name) != h.hexdigest():
        raise UpdateError("The download didn't match its checksum, so it wasn't installed.")


def prepare(release: Release, progress: Callable[[int, int], None] | None = None) -> Path:
    """Download and unpack the new version. Returns the staged app to swap in."""
    kind = install_kind()
    if kind in ("uv", "source"):
        return Path()
    name = ASSETS[kind]
    if name not in release.assets:
        raise UpdateError(f"Version {release.version} has no download for this system yet.")
    target = app_location()
    if not os.access(target.parent, os.W_OK):
        raise UpdateError(f"Can't write to {target.parent}. Reinstall with the install "
                          "command, or move the app somewhere you can write to.")
    stage = Path(tempfile.mkdtemp(prefix="guitar-remover-update-"))
    archive = stage / name
    _download(release.assets[name], archive, progress)
    _verify(release, name, archive)
    if kind == "macos":
        mnt = stage / "mnt"
        subprocess.run(["hdiutil", "attach", "-nobrowse", "-quiet", "-mountpoint", str(mnt),
                        str(archive)], check=True)
        try:
            src = next(mnt.glob("*.app"))
            subprocess.run(["ditto", str(src), str(stage / src.name)], check=True)
        finally:
            subprocess.run(["hdiutil", "detach", "-quiet", str(mnt)], check=False)
        staged = stage / src.name
    elif kind == "windows":
        import zipfile
        with zipfile.ZipFile(archive) as z:
            z.extractall(stage / "app")
        staged = stage / "app"
    else:
        import tarfile
        with tarfile.open(archive) as t:
            t.extractall(stage, filter="data")
        staged = stage / "GuitarRemover"
    archive.unlink(missing_ok=True)
    return staged


def _uv() -> str:
    found = shutil.which("uv")
    if found:
        return found
    for p in (Path.home() / ".local/bin/uv", Path.home() / ".cargo/bin/uv",
              Path.home() / ".local/bin/uv.exe"):
        if p.exists():
            return str(p)
    raise UpdateError("Couldn't find uv. Re-run the install command to update.")


def apply(release: Release, staged: Path, relaunch: bool = True) -> None:
    """Start the helper that swaps in the new version after this process exits."""
    kind = install_kind()
    pid = os.getpid()
    if kind == "source":
        raise UpdateError("You're running from source: update with `git pull`.")
    helper_dir = Path(tempfile.mkdtemp(prefix="guitar-remover-helper-"))
    if kind == "windows" or (kind == "uv" and sys.platform == "win32"):
        ps = helper_dir / "update.ps1"
        if kind == "uv":
            body = (f'& "{_uv()}" tool install --force --python 3.12 --torch-backend auto '
                    f'"guitar-remover @ git+https://github.com/{GITHUB_REPO}@v{release.version}"\n')
            launch = f'Start-Process "{Path(sys.executable).parent / "guitar-remover.exe"}"'
        else:
            dest = app_location()
            body = (f'robocopy "{staged}" "{dest}" /MIR /NFL /NDL /NJH /NJS /NP | Out-Null\n'
                    f'Get-ChildItem "{dest}" -Recurse | Unblock-File\n'
                    f'Remove-Item -Recurse -Force "{staged.parent}"\n')
            launch = f'Start-Process "{dest / "GuitarRemover.exe"}"'
        ps.write_text(f"Wait-Process -Id {pid} -ErrorAction SilentlyContinue\n"
                      "Start-Sleep -Milliseconds 500\n" + body +
                      (launch + "\n" if relaunch else ""), encoding="utf-8")
        subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden",
                          "-ExecutionPolicy", "Bypass", "-File", str(ps)],
                         creationflags=0x00000008 | 0x00000200)  # DETACHED, NEW_GROUP
        return
    sh = helper_dir / "update.sh"
    if kind == "uv":
        body = (f'"{_uv()}" tool install --force --python 3.12 --torch-backend auto '
                f'"guitar-remover @ git+https://github.com/{GITHUB_REPO}@v{release.version}"'
                ' >/dev/null 2>&1\n')
        launch = f'"{Path(sys.argv[0]).resolve()}" >/dev/null 2>&1 &\n'
    else:
        dest = app_location()
        old = dest.with_name(dest.name + ".old")
        body = (f'rm -rf "{old}"\nmv "{dest}" "{old}" && mv "{staged}" "{dest}" '
                f'&& rm -rf "{old}" || mv "{old}" "{dest}"\n'
                f'rm -rf "{staged.parent}"\n')
        if kind == "macos":
            body += f'xattr -dr com.apple.quarantine "{dest}" 2>/dev/null\n'
            launch = f'open "{dest}"\n'
        else:
            launch = f'"{dest / "GuitarRemover"}" >/dev/null 2>&1 &\n'
    sh.write_text("#!/bin/sh\n"
                  f"while kill -0 {pid} 2>/dev/null; do sleep 0.3; done\n" + body +
                  (launch if relaunch else "") + f'rm -rf "{helper_dir}"\n')
    sh.chmod(0o755)
    subprocess.Popen(["/bin/sh", str(sh)], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def cli_update(check_only: bool = False) -> int:
    """`guitar-remover --update` / `--check-update` from a terminal."""
    print(f"Guitar Remover {__version__} ({install_kind()} install)")
    try:
        rel = latest_release()
    except UpdateError as exc:
        print(exc)
        return 1
    if not is_newer(rel.version):
        print(f"You're up to date (latest is {rel.version}).")
        return 0
    print(f"Version {rel.version} is available: {rel.url}")
    if check_only:
        return 0
    try:
        last = [-1]

        def show(done, total):
            pct = int(done * 100 / total) if total else 0
            if pct != last[0] and pct % 5 == 0:
                print(f"\rDownloading… {pct}%", end="", flush=True)
                last[0] = pct
        staged = prepare(rel, show)
        print()
        apply(rel, staged, relaunch=False)
    except UpdateError as exc:
        print(exc)
        return 1
    print(f"Installing {rel.version}; it'll be ready in a few seconds"
          + (" (uv is downloading it now)." if install_kind() == "uv" else "."))
    return 0


def attach_console() -> None:
    """Windowed Windows builds have no console; borrow the terminal's for CLI output."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    import ctypes
    if ctypes.windll.kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stderr = sys.stdout


