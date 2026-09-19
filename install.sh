#!/usr/bin/env sh
# Guitar Remover installer for macOS and Linux.
#
#   curl -fsSL https://raw.githubusercontent.com/j4ckxyz/guitar-remover/main/install.sh | sh
#
# Installs the latest release for this computer. Falls back to installing from
# source with uv (https://docs.astral.sh/uv/) when there is no matching release,
# and always uses the source install on Linux machines with an NVIDIA GPU so the
# CUDA build of PyTorch is used. Options (after "sh -s --"):
#   --source      install from source with uv instead of the prebuilt app
#   --uninstall   remove Guitar Remover
set -eu

REPO="${GR_REPO:-j4ckxyz/guitar-remover}"
SOURCE="${GR_SOURCE:-git+https://github.com/$REPO}"  # pip-style spec; a local path works
RELEASES="https://github.com/$REPO/releases/latest/download"
MODE=release
for arg in "$@"; do
  case "$arg" in
    --source) MODE=source ;;
    --uninstall) MODE=uninstall ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

OS=$(uname -s)
ARCH=$(uname -m)
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT INT TERM

download() {  # url dest -> 0 if it worked
  if command -v curl >/dev/null 2>&1; then
    curl -fL --progress-bar -o "$2" "$1"
  else
    wget -q --show-progress -O "$2" "$1"
  fi
}

mac_app_dir() {
  if [ -w /Applications ]; then echo /Applications; else mkdir -p "$HOME/Applications"; echo "$HOME/Applications"; fi
}

linux_desktop_entry() {  # exec_path
  mkdir -p "$DATA/applications" "$DATA/icons/hicolor/512x512/apps"
  "$1" --export-icon "$DATA/icons/hicolor/512x512/apps/guitar-remover.png" 2>/dev/null || true
  cat > "$DATA/applications/guitar-remover.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Guitar Remover
GenericName=Backing Track Maker
Comment=Make guitar backing tracks from any song, on your own computer
Exec=$1 %F
Icon=guitar-remover
Terminal=false
Categories=AudioVideo;Audio;Music;
MimeType=audio/mpeg;audio/x-wav;audio/wav;audio/flac;audio/mp4;audio/ogg;audio/opus;audio/aac;
StartupWMClass=guitar-remover
DESKTOP
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DATA/applications" 2>/dev/null || true
}

linux_check_libs() {
  missing=""
  ldconfig -p 2>/dev/null | grep -q libxcb-cursor || missing="$missing libxcb-cursor0"
  ldconfig -p 2>/dev/null | grep -q libportaudio || missing="$missing libportaudio2"
  if [ -n "$missing" ]; then
    say "You may need a couple of system libraries:"
    echo "    Debian/Ubuntu: sudo apt install$missing"
    echo "    Fedora:        sudo dnf install xcb-util-cursor portaudio"
    echo "    Arch:          sudo pacman -S xcb-util-cursor portaudio"
  fi
}

install_release_macos() {
  [ "$ARCH" = "arm64" ] || return 1  # PyTorch no longer ships Intel Mac builds
  say "Downloading Guitar Remover for macOS…"
  download "$RELEASES/Guitar-Remover-macOS-arm64.dmg" "$TMP/gr.dmg" || return 1
  hdiutil attach -nobrowse -quiet -mountpoint "$TMP/mnt" "$TMP/gr.dmg"
  DEST=$(mac_app_dir)
  rm -rf "$DEST/Guitar Remover.app"
  cp -R "$TMP/mnt/Guitar Remover.app" "$DEST/"
  hdiutil detach -quiet "$TMP/mnt"
  # Downloads made by curl aren't quarantined, but clear the flag in case, so
  # macOS doesn't show the "can't be opened" warning for this unsigned app.
  xattr -dr com.apple.quarantine "$DEST/Guitar Remover.app" 2>/dev/null || true
  # A terminal command too, e.g. for "guitar-remover --update".
  mkdir -p "$HOME/.local/bin"
  ln -sf "$DEST/Guitar Remover.app/Contents/MacOS/GuitarRemover" "$HOME/.local/bin/guitar-remover"
  say "Installed to $DEST/Guitar Remover.app"
  open -R "$DEST/Guitar Remover.app" 2>/dev/null || true
}

install_release_linux() {
  command -v nvidia-smi >/dev/null 2>&1 && { say "NVIDIA GPU found: installing the CUDA build from source."; return 1; }
  say "Downloading Guitar Remover for Linux ($ARCH)…"
  download "$RELEASES/Guitar-Remover-Linux-$ARCH.tar.gz" "$TMP/gr.tar.gz" || return 1
  tar -xzf "$TMP/gr.tar.gz" -C "$TMP"
  sh "$TMP/GuitarRemover/install-local.sh"
  linux_check_libs
}

install_source() {
  if ! command -v uv >/dev/null 2>&1; then
    say "Installing uv (Python package manager)…"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  fi
  say "Installing Guitar Remover from source (downloads PyTorch, a few minutes)…"
  uv tool install --force --python 3.12 --torch-backend auto "guitar-remover @ $SOURCE"
  BIN="$(uv tool dir --bin)/guitar-remover"
  [ -x "$BIN" ] || fail "install finished but $BIN is missing"
  if [ "$OS" = "Darwin" ]; then
    # A small .app so it lives in Launchpad/Spotlight with the proper icon.
    DEST="$(mac_app_dir)/Guitar Remover.app"
    rm -rf "$DEST"
    mkdir -p "$DEST/Contents/MacOS" "$DEST/Contents/Resources"
    printf '#!/bin/sh\nexec "%s" "$@"\n' "$BIN" > "$DEST/Contents/MacOS/Guitar Remover"
    chmod +x "$DEST/Contents/MacOS/Guitar Remover"
    "$BIN" --export-icon "$TMP/icon.png"
    mkdir -p "$TMP/icon.iconset"
    for s in 16 32 128 256 512; do
      sips -z $s $s "$TMP/icon.png" --out "$TMP/icon.iconset/icon_${s}x${s}.png" >/dev/null
      sips -z $((s * 2)) $((s * 2)) "$TMP/icon.png" --out "$TMP/icon.iconset/icon_${s}x${s}@2x.png" >/dev/null
    done
    iconutil -c icns "$TMP/icon.iconset" -o "$DEST/Contents/Resources/AppIcon.icns"
    cat > "$DEST/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Guitar Remover</string>
  <key>CFBundleDisplayName</key><string>Guitar Remover</string>
  <key>CFBundleIdentifier</key><string>app.guitarremover.GuitarRemover</string>
  <key>CFBundleExecutable</key><string>Guitar Remover</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
    say "Installed. Open Guitar Remover from Launchpad or $DEST"
  else
    linux_desktop_entry "$BIN"
    linux_check_libs
    say "Installed. Find Guitar Remover in your app menu, or run: guitar-remover"
  fi
}

uninstall() {
  if [ "$OS" = "Darwin" ]; then
    rm -rf "/Applications/Guitar Remover.app" "$HOME/Applications/Guitar Remover.app"
    rm -f "$HOME/.local/bin/guitar-remover"
  else
    rm -rf "$DATA/guitar-remover" "$HOME/.local/bin/guitar-remover" \
      "$DATA/applications/guitar-remover.desktop" \
      "$DATA/icons/hicolor/512x512/apps/guitar-remover.png"
  fi
  command -v uv >/dev/null 2>&1 && uv tool uninstall guitar-remover 2>/dev/null || true
  say "Guitar Remover removed. (Downloaded models stay in your app data folder.)"
}

case "$MODE" in
  uninstall) uninstall ;;
  source) install_source ;;
  release)
    case "$OS" in
      Darwin) install_release_macos || { say "No prebuilt app for this Mac, installing from source."; install_source; } ;;
      Linux) install_release_linux || install_source ;;
      *) fail "Unsupported system: $OS (use install.ps1 on Windows)" ;;
    esac ;;
esac
