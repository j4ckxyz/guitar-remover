# Build GuitarRemover (folder with GuitarRemover.exe) and a zip.
# Usage:  powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1 [-Cpu]
# By default installs the CUDA build of PyTorch so NVIDIA GPUs are used (it still
# runs on the CPU on machines without one). -Cpu builds a much smaller CPU-only app
# (the GitHub release uses -Cpu, as CUDA builds exceed the 2 GB asset limit).
param([switch]$Cpu)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Test-Path .venv)) { py -3.12 -m venv .venv }
$py = ".venv\Scripts\python.exe"
& $py -m pip install -q --upgrade pip
if ($Cpu) {
  & $py -m pip install -q torch --index-url https://download.pytorch.org/whl/cpu
} else {
  & $py -m pip install -q torch --index-url https://download.pytorch.org/whl/cu128
}
& $py -m pip install -q -r requirements.txt pyinstaller pillow

& $py packaging\make_icon.py
& .venv\Scripts\pyinstaller.exe --noconfirm --clean `
  --distpath packaging\dist --workpath packaging\build\pyi packaging\guitar_remover.spec

$zip = "packaging\dist\Guitar-Remover-Windows-x64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path packaging\dist\GuitarRemover\* -DestinationPath $zip
Write-Host "Built: packaging\dist\GuitarRemover\GuitarRemover.exe"
Write-Host "Built: $zip"
