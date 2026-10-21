@echo off
rem Starts Reforged Studio (build it first: python studio\build.py).
if not exist "%~dp0build\ReforgedStudio.exe" (
  echo Reforged Studio isn't built yet - run: python "%~dp0build.py"
  pause
  exit /b 1
)
start "" "%~dp0build\ReforgedStudio.exe"
