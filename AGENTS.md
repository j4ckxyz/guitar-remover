# AGENTS.md

Guidance for AI coding agents (and humans) working on Guitar Remover.

## Versioning and releases (mandatory)

**Every push to `main` is a release, and must carry a new version.** CI rejects a push
that doesn't, and the local pre-push hook blocks it before it leaves your machine.

For every change you push to `main`:

1. Bump `__version__` in `src/guitar_remover/__init__.py`. This is the only place the
   version lives: `pyproject.toml`, the macOS bundle and the About box all read it.
   Follow [Semantic Versioning](https://semver.org):
   - **PATCH** (1.0.0 → 1.0.1): bug fixes, docs, build or CI changes, dependency bumps
   - **MINOR** (1.0.1 → 1.1.0): new features or settings, backwards compatible
   - **MAJOR** (1.1.0 → 2.0.0): breaking changes (removed features, settings or output
     names that break existing setups)
2. Add a `## [X.Y.Z] - YYYY-MM-DD` section at the top of `CHANGELOG.md`, using `### Added`,
   `### Changed` and `### Fixed` subsections. Write it for users, not developers. It becomes
   the GitHub release notes.
3. Run `python tools/check_version.py --remote` and make sure it passes.
4. Commit the version bump and changelog entry in the same push as the change. If you
   push several commits at once, one bump covering them all is enough.

Never:

- create or push `v*` tags by hand (the release workflow does it)
- reuse, decrease or skip-check a version
- edit or delete published releases
- bypass the hook with `git push --no-verify`

What happens on push (`.github/workflows/release.yml`):

1. **check**: version gate, pyflakes, pytest.
2. **build**: macOS (arm64 `.dmg`), Windows (x64 `.zip`, CPU) and Linux (x86_64
   `.tar.gz`, CPU), each with a smoke test.
3. **release**: tag `vX.Y.Z` on the pushed commit, then a GitHub release with the three
   files and the changelog section.

Pull requests run the **check** job only. If a build fails, fix it and push again with
the **next patch version**, because the failed version may already be recorded in history.

Enable the hook once per clone: `git config core.hooksPath .githooks`

The README's download links point at `releases/latest/download/<file>`, so asset names
must stay exactly:

- `Guitar-Remover-macOS-arm64.dmg`
- `Guitar-Remover-Windows-x64.zip`
- `Guitar-Remover-Linux-x86_64.tar.gz`

## What this is

A native desktop app (macOS, Windows, Linux) that splits songs into a guitar track and a
backing track with Demucs (`htdemucs_6s` by default), entirely locally. It has a
built-in multitrack player. Written in Python 3.12 with PySide6 (Qt 6) and PyTorch.

```
src/guitar_remover/
  __init__.py    APP_NAME and __version__ (single source of truth)
  hardware.py    hardware detection → ResourcePlan (device, threads, block size)
  models.py      model catalogue, downloads with progress, loading
  separator.py   block-wise separation engine and output writing
  playback.py    real-time mixer for the player (pure mix() function + PortAudio stream)
  audio_io.py    decode/encode through bundled FFmpeg (imageio-ffmpeg)
  settings.py    QSettings with typed DEFAULTS
  runner.py      QThread job queue (torch is imported here, off the UI thread)
  app.py         entry point, platform styling, --selftest, --export-icon
  ui/            main_window, player, settings_dialog, widgets, icon
tools/           check_version.py (release gate)
packaging/       PyInstaller spec, build scripts, Linux desktop entry
install.sh / install.ps1   one-command installers (release download, else uv source install)
```

## Development

```sh
uv venv -p 3.12 && uv pip install --torch-backend auto -e . pytest pyflakes pyinstaller pillow
.venv/bin/guitar-remover                                  # run the app
.venv/bin/python -m pyflakes src tests tools packaging/*.py
.venv/bin/python -m pytest tests                          # add TEST_SONG=song.mp3 for engine tests
.venv/bin/python tests/ui_smoke.py SONG OUT_DIR [light|dark]   # drives the UI, saves screenshots
packaging/build_macos.sh                                  # or build_linux.sh / build_windows.ps1
"packaging/dist/Guitar Remover.app/Contents/MacOS/GuitarRemover" --selftest SONG OUT_DIR
```

- Test changes by running them: engine changes with `tests/run_headless.py` (it has a
  memory watchdog) and UI changes with `tests/ui_smoke.py`, looking at the screenshots in
  both light and dark mode.
- Development machines may have only 8 GB of RAM. Keep test runs sequential, use the
  Fastest quality preset, and never run several separations in parallel.
- Don't play audio out loud in tests. Set the player's master volume to 0 (as `ui_smoke.py` does).
- Test songs, downloaded models (`.models-cache/`) and build output are git-ignored. Never
  commit audio files, model weights or personal file paths.

## Code and UI conventions

- **Native, not web**: Qt widgets only, no web views or Electron-style wrappers. Use the
  platform style (macOS native, `windows11`, Fusion on Windows 10 and Linux), and
  `theme_icon()` for icons (SF Symbols, Fluent or the freedesktop theme).
- **Light and dark mode**: custom-painted widgets must take colours from `QPalette`
  (including `Accent`) and repaint on palette change. Don't hard-code light or dark colours;
  track colours in the player are the only fixed palette.
- **Intuitive over technical**: people pick "Faster ↔ Better" or "Lighter ↔ Faster" and
  never type thread counts or segment sizes. Numeric tuning belongs in Settings → Advanced.
  Every default must work well without changes.
- **Efficient and safe on small machines**: work stays off the UI thread, songs are
  processed in memory-sized blocks (`hardware.plan_resources`), and caches are freed after
  each job. Block processing must stay aligned to the window stride (see
  `Separator._separate`); its output is verified identical to a single pass.
- **User-facing text and docs**: British English (colour, behaviour, licence as a noun,
  practise as a verb), no em dashes, plain words over jargon. Say "backing track", "guitar",
  "part", not "stem" or "inference", except in Advanced settings.
- Match the existing style: type hints, `from __future__ import annotations`, small
  functions, comments only where the why isn't obvious. Keep pyflakes clean.
- New dependencies: add them to both `requirements.txt` and `pyproject.toml`, and check that
  the PyInstaller build still bundles them (`--selftest` on the built app).

## Platform notes

- **macOS**: Apple Silicon only (PyTorch has no Intel Mac builds). GPU is MPS; keep
  `PYTORCH_ENABLE_MPS_FALLBACK=1` set before torch is imported (`app.py`).
- **Windows**: GitHub release builds are CPU-only (CUDA exceeds the 2 GB asset limit);
  NVIDIA users get CUDA through `install.ps1` → uv source install.
- **Linux**: needs `libxcb-cursor0` and `libportaudio2` on the system; release builds on
  Ubuntu 22.04 for glibc compatibility. AMD GPUs work through ROCm builds of PyTorch
  (`hardware.gpu_vendor`).
