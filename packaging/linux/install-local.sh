#!/usr/bin/env sh
# Installs this extracted folder for the current user (no root needed):
# app in ~/.local/share/guitar-remover, command in ~/.local/bin, menu entry + icon.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
DATA=${XDG_DATA_HOME:-$HOME/.local/share}
DEST="$DATA/guitar-remover"
if [ "$HERE" != "$DEST" ]; then
  rm -rf "$DEST"
  mkdir -p "$DATA"
  cp -R "$HERE" "$DEST"
fi
mkdir -p "$HOME/.local/bin" "$DATA/applications" "$DATA/icons/hicolor/512x512/apps"
ln -sf "$DEST/GuitarRemover" "$HOME/.local/bin/guitar-remover"
cp "$DEST/guitar-remover.png" "$DATA/icons/hicolor/512x512/apps/guitar-remover.png"
sed "s|^Exec=.*|Exec=$DEST/GuitarRemover %F|" "$DEST/guitar-remover.desktop" \
  > "$DATA/applications/guitar-remover.desktop"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DATA/applications" || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q "$DATA/icons/hicolor" || true
echo "Installed Guitar Remover. Find it in your app menu, or run: guitar-remover"
