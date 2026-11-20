@echo off
rem Double-click to open ClipStudio in the browser. Close this window to stop it.
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
  echo uv is not installed. Install it with:  pip install uv
  pause
  exit /b 1
)
uv run clipstudio app
pause
