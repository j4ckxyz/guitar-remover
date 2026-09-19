# Changelog

Every push to `main` is a release. Add a section here for each new version (newest
first), using [Semantic Versioning](https://semver.org). The release workflow publishes
the matching section as the GitHub release notes.

## [1.1.0] - 2026-09-19

### Added

- **Remembered separations**: every part of each song you separate is kept (lossless
  FLAC, size limit in Settings → Performance). Changing the file format or vocal
  setting, or pressing **Save Again**, takes seconds instead of re-running the model.
- **Tempo, bars and count-in** in the player: the beat is detected automatically, bar
  numbers and bar lines are drawn on the waveforms, selections snap to bars, and
  **Count-in** plays a bar of clicks before the music. Fix half or double time, beats
  per bar or where bar 1 starts from the tempo menu.
- **Transpose** the player by semitones (♭ / ♯) without changing speed, for example to
  play songs recorded in E♭ tuning on a guitar in standard tuning.
- **Saved loops** per song, shown on the ruler and remembered with your tempo and key
  settings, even if you move or rename the files.
- **Guitar to Tab**: writes out the guitar part as tab (with a choice of tunings) and
  MIDI, using Spotify's Basic Pitch model. Runs locally in a second or two.
- **Updates**: the app checks for new versions and can install them for you (Help →
  Check for Updates…, or automatically when you quit). From a terminal:
  `guitar-remover --update`, `--check-update` or `--version`.

### Changed

- Separation now keeps its working data on disk rather than in memory, so every part can
  be remembered without using more RAM.

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
