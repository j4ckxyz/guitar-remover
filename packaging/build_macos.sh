#!/usr/bin/env bash
# Build "Guitar Remover.app" and a .dmg. Run on the architecture you are
# targeting (Apple Silicon build -> arm64 app with Metal GPU support).
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
$PIP -r requirements.txt pyinstaller pillow

.venv/bin/python packaging/make_icon.py
.venv/bin/pyinstaller --noconfirm --clean \
  --distpath packaging/dist --workpath packaging/build/pyi packaging/guitar_remover.spec

APP="packaging/dist/Guitar Remover.app"
# Ad-hoc sign so macOS will run it locally (use a Developer ID to distribute).
codesign --force --deep --sign - "$APP"

DMG="packaging/dist/Guitar-Remover-macOS-$(uname -m).dmg"
rm -f "$DMG"
STAGE=$(mktemp -d)
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "Guitar Remover" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
rm -rf "$STAGE"
echo "Built: $APP"
echo "Built: $DMG"
