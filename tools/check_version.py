"""Release version gate. Every push to main must carry a new version.

Checks that:
  1. src/guitar_remover/__init__.py has __version__ = "MAJOR.MINOR.PATCH"
  2. the version is higher than every existing vX.Y.Z tag (so it is unreleased)
  3. CHANGELOG.md has a "## [X.Y.Z]" section for it

usage: python tools/check_version.py [--remote]   (--remote also checks origin's tags)
Prints the version; in GitHub Actions also writes version=... to $GITHUB_OUTPUT.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def fail(msg: str) -> None:
    print(f"version check failed: {msg}", file=sys.stderr)
    print("Bump __version__ in src/guitar_remover/__init__.py and add a matching "
          "section to CHANGELOG.md (see AGENTS.md, 'Versioning and releases').",
          file=sys.stderr)
    sys.exit(1)


def tags(remote: bool) -> set[str]:
    found = set(subprocess.run(["git", "tag", "-l", "v*"], cwd=ROOT, capture_output=True,
                               text=True).stdout.split())
    if remote:
        out = subprocess.run(["git", "ls-remote", "--tags", "origin"], cwd=ROOT,
                             capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            fail("couldn't read tags from origin (are you online?)")
        found |= {line.split("refs/tags/")[1].removesuffix("^{}")
                  for line in out.stdout.splitlines() if "refs/tags/v" in line}
    return found


def main() -> None:
    init = (ROOT / "src/guitar_remover/__init__.py").read_text()
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', init, re.M)
    if not m:
        fail("no __version__ in src/guitar_remover/__init__.py")
    version = m.group(1)
    parsed = SEMVER.match(version)
    if not parsed:
        fail(f"{version!r} is not MAJOR.MINOR.PATCH")
    current = tuple(map(int, parsed.groups()))

    released = [tuple(map(int, SEMVER.match(t[1:]).groups()))
                for t in tags("--remote" in sys.argv) if SEMVER.match(t[1:])]
    if released and current <= max(released):
        latest = ".".join(map(str, max(released)))
        fail(f"version {version} is not higher than the latest release v{latest}")

    changelog = ROOT / "CHANGELOG.md"
    if not changelog.exists() or not re.search(
            rf"^## \[{re.escape(version)}\]", changelog.read_text(), re.M):
        fail(f"CHANGELOG.md has no '## [{version}]' section")

    print(version)
    if out := os.environ.get("GITHUB_OUTPUT"):
        with open(out, "a") as fh:
            fh.write(f"version={version}\n")


if __name__ == "__main__":
    main()
