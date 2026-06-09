@echo off
setlocal EnableDelayedExpansion

set "DIR=%~dp0"
set "SRC=%DIR%src"
set "APP=%DIR%app"
set "BUNDLE=%APP%\fhds.zuv.py"

echo =========================================
echo  FH DualSense - Local Builder and Runner
echo =========================================

where uv >nul 2>nul
if errorlevel 1 (
    echo Installing uv...
    powershell -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex"
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
    where uv >nul 2>nul || (
        echo [!] uv not found. Please install it manually or restart your terminal.
        pause & exit /b 1
    )
)

echo Syncing Python dependencies...
cd /d "%SRC%"
uv sync || (
    echo [!] uv sync failed.
    pause & exit /b 1
)

echo Building zuv bundle locally...
cd /d "%DIR%"
if not exist "%APP%" mkdir "%APP%"
uvx zuv build src -o "%BUNDLE%" || (
    echo [!] Failed to build zuv bundle.
    pause & exit /b 1
)

echo Bundle successfully built at: %BUNDLE%
echo =========================================

echo Launching local bundle...
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONNOUSERSITE=1"
set "UV_PYTHON_PREFERENCE=only-managed"

uv run "%BUNDLE%" %*
endlocal
