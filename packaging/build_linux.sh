#!/usr/bin/env bash
# Build packaging/dist/GuitarRemover/ and a .tar.gz with a desktop entry.
#   packaging/build_linux.sh          CPU build (small, runs everywhere)
#   packaging/build_linux.sh --cuda   NVIDIA build (several GB larger)
# Needs: python3.12 (with venv), libportaudio2, libxcb-cursor0.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PYTHON:-python3.12}
# Use uv when available (much faster), otherwise plain venv + pip.
if command -v uv >/dev/null 2>&1; then
  [ -d .venv ] || uv venv -q -p 3.12 .venv
  PIP="uv pip install -q -p .venv/bin/python"
else
  [ -d .venv ] || "$PY" -m venv .venv
  .venv/bin/python -m pip install -q --upgrade pip
  PIP=".venv/bin/python -m pip install -q"
fi
if [ "${1:-}" = "--cuda" ]; then
  $PIP torch
else
  $PIP torch --index-url https://download.pytorch.org/whl/cpu
fi
$PIP -r requirements.txt pyinstaller pillow

.venv/bin/python packaging/make_icon.py
.venv/bin/pyinstaller --noconfirm --clean \
  --distpath packaging/dist --workpath packaging/build/pyi packaging/guitar_remover.spec

DIST=packaging/dist/GuitarRemover
cp packaging/build/icon.png "$DIST/guitar-remover.png"
cp packaging/linux/guitar-remover.desktop "$DIST/"
cp packaging/linux/install-local.sh "$DIST/"
chmod +x "$DIST/install-local.sh"

TAR="packaging/dist/Guitar-Remover-Linux-$(uname -m).tar.gz"
tar -C packaging/dist -czf "$TAR" GuitarRemover
echo "Built: $DIST/GuitarRemover"
echo "Built: $TAR"
