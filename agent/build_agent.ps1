param(
    [string]$OutputDir = "dist"
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command pyinstaller -ErrorAction SilentlyContinue)) {
    Write-Host "PyInstaller not found. Install with: pip install pyinstaller" -ForegroundColor Yellow
    exit 1
}

pyinstaller --onefile --name cmapp-agent --distpath $OutputDir agent.py
