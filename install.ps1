# Guitar Remover installer for Windows (PowerShell).
#
#   irm https://raw.githubusercontent.com/j4ckxyz/guitar-remover/main/install.ps1 | iex
#
# Installs the latest release into %LOCALAPPDATA%\Programs\Guitar Remover with a
# Start menu shortcut. PCs with an NVIDIA GPU get a from-source install through uv
# instead, so the CUDA build of PyTorch is used. To force a mode, download the
# script and run:  .\install.ps1 -Source   or   .\install.ps1 -Uninstall
param([switch]$Source, [switch]$Uninstall)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # makes Invoke-WebRequest much faster

$Repo = if ($env:GR_REPO) { $env:GR_REPO } else { "j4ckxyz/guitar-remover" }
$Spec = if ($env:GR_SOURCE) { $env:GR_SOURCE } else { "git+https://github.com/$Repo" }
$Asset = "https://github.com/$Repo/releases/latest/download/Guitar-Remover-Windows-x64.zip"
$Dest = Join-Path $env:LOCALAPPDATA "Programs\Guitar Remover"
$Menu = Join-Path ([Environment]::GetFolderPath("Programs")) "Guitar Remover.lnk"
$Desk = Join-Path ([Environment]::GetFolderPath("Desktop")) "Guitar Remover.lnk"

function Say($m) { Write-Host "==> $m" -ForegroundColor Cyan }

function New-Shortcut($path, $target) {
  $s = (New-Object -ComObject WScript.Shell).CreateShortcut($path)
  $s.TargetPath = $target
  $s.WorkingDirectory = Split-Path $target
  $s.Description = "Make guitar backing tracks"
  $s.Save()
}

function Install-Release {
  Say "Downloading Guitar Remover for Windows..."
  $zip = Join-Path $env:TEMP "guitar-remover.zip"
  try { Invoke-WebRequest $Asset -OutFile $zip } catch { return $false }
  if (Test-Path $Dest) { Remove-Item $Dest -Recurse -Force }
  Expand-Archive $zip -DestinationPath $Dest -Force
  Remove-Item $zip
  # Clear the "downloaded from the internet" mark so SmartScreen doesn't block it.
  Get-ChildItem $Dest -Recurse | Unblock-File
  $exe = Join-Path $Dest "GuitarRemover.exe"
  New-Shortcut $Menu $exe
  New-Shortcut $Desk $exe
  Say "Installed. Open Guitar Remover from the Start menu or your desktop."
  return $true
}

function Install-Source {
  if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Say "Installing uv (Python package manager)..."
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
  }
  Say "Installing Guitar Remover from source (downloads PyTorch, a few minutes)..."
  uv tool install --force --python 3.12 --torch-backend auto "guitar-remover @ $Spec"
  if ($LASTEXITCODE -ne 0) { throw "uv tool install failed" }
  $exe = Join-Path (uv tool dir --bin) "guitar-remover.exe"
  New-Shortcut $Menu $exe
  New-Shortcut $Desk $exe
  Say "Installed. Open Guitar Remover from the Start menu or your desktop."
}

if ($Uninstall) {
  Remove-Item $Dest -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item $Menu, $Desk -Force -ErrorAction SilentlyContinue
  if (Get-Command uv -ErrorAction SilentlyContinue) { uv tool uninstall guitar-remover 2>$null }
  Say "Guitar Remover removed. (Downloaded models stay in your AppData folder.)"
  return
}

$nvidia = [bool](Get-Command nvidia-smi -ErrorAction SilentlyContinue)
if ($Source -or $nvidia) {
  if ($nvidia -and -not $Source) { Say "NVIDIA GPU found: installing the CUDA build." }
  Install-Source
} elseif (-not (Install-Release)) {
  Say "No prebuilt release found, installing from source instead."
  Install-Source
}
