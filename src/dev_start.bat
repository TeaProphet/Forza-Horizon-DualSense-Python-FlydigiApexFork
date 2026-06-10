@echo off
REM FH DualSense - dev launcher (Windows). Runs main.py from source.
setlocal

cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
    echo uv is not installed. Download it from https://docs.astral.sh/uv/getting-started/installation/
    pause & exit /b 1
)

uv run main.py %*
endlocal
exit /b %ERRORLEVEL%
