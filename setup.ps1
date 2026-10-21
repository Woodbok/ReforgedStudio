# Reforged Studio: one-time setup - a Python environment beside the Studio (.venv) with its packages, then the build.
#
#   powershell -ExecutionPolicy Bypass -File setup.ps1
#
# Needs: Python 3.10+ (python.org, "Add to PATH") and the Visual Studio C++ Build Tools
# (x64, "Desktop development with C++") to build the program. Run it again after an update.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py -or $py.Source -like "*WindowsApps*") {
    Write-Host "Python isn't installed (or only the Microsoft Store stub is): install it from python.org with 'Add to PATH'." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Making the Python environment (.venv)..."
    python -m venv .venv
}
Write-Host "Installing the engine's packages (numpy, Pillow)..."
& .venv\Scripts\python.exe -m pip install --upgrade pip | Out-Null
& .venv\Scripts\python.exe -m pip install -r requirements.txt
Write-Host "Building the native archive codec (forgelib\native\ac3lzo.dll: reading and converting maps, many times faster)..."
& .venv\Scripts\python.exe forgelib\native\build.py
if ($LASTEXITCODE -ne 0) { Write-Host "  (not built: the Studio runs on its Python codec, only slower)" -ForegroundColor Yellow }
Write-Host "Building the program (studio\build\ReforgedStudio.exe)..."
& .venv\Scripts\python.exe studio\build.py
Write-Host ""
Write-Host "Done. Start it with studio\start_studio.cmd (or studio\build\ReforgedStudio.exe)." -ForegroundColor Green
Write-Host "The first start asks for your game's multiplayer folder."
