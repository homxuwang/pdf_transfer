$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

python -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --windowed `
  --name MinerUPdfTool `
  mineru_gui.py

Write-Host ""
Write-Host "Build complete: $root\dist\MinerUPdfTool.exe"
