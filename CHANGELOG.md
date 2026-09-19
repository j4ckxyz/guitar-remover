# Changelog

Every push to `main` is a release. Add a section here for each new version (newest
first), using [Semantic Versioning](https://semver.org). The release workflow publishes
the matching section as the GitHub release notes.

## [1.0.0] - 2026-09-19

### Added

- Desktop app for macOS, Windows and Linux that splits songs into a guitar track and a
  backing track with Demucs `htdemucs_6s`, fully offline after a one-off model download.
- Hardware-aware resource planning (Apple GPU, NVIDIA CUDA, AMD ROCm or CPU) with a
  Lighter ↔ Faster setting and memory-sized block processing.
- Speed ↔ quality slider with time estimates learned from your own computer.
- Built-in multitrack player: waveforms, looping, per-track volume, mute and solo, and
  mix export.
- Settings for output folder, format (WAV, FLAC, MP3), vocal removal and advanced model
  options.
- One-command installers (`install.sh`, `install.ps1`) and an automatic release workflow.
