@echo off
setlocal EnableDelayedExpansion
title YouTube Playlist Downloader

set APP_DIR=%USERPROFILE%\YouTubeDownloader
set REPO_ZIP=https://github.com/yoniStein2/youTube-playlist-downloader/archive/refs/heads/main.zip
set EXTRACT_DIR=%TEMP%\ytdl-extract

cls
echo ======================================
echo    YouTube Playlist Downloader
echo ======================================
echo.

:: ── Python ─────────────────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo Installing Python (one-time, takes ~1 min)...
    winget install Python.Python.3.12 --accept-source-agreements --accept-package-agreements --silent
    :: Refresh PATH so python is found
    set PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%
    python --version >nul 2>&1
    if errorlevel 1 (
        echo.
        echo Python install failed. Please install it manually from https://python.org
        pause
        exit /b 1
    )
)

:: ── Download / update app ──────────────────────────────────────────────────────
echo Downloading latest app...
if exist "%EXTRACT_DIR%" rmdir /S /Q "%EXTRACT_DIR%"
powershell -NoProfile -Command "Invoke-WebRequest -Uri '%REPO_ZIP%' -OutFile '%TEMP%\ytdl.zip' -UseBasicParsing"
powershell -NoProfile -Command "Expand-Archive -Path '%TEMP%\ytdl.zip' -DestinationPath '%EXTRACT_DIR%' -Force"
if not exist "%APP_DIR%" mkdir "%APP_DIR%"
xcopy /E /Y /Q "%EXTRACT_DIR%\youTube-playlist-downloader-main\*" "%APP_DIR%\" >nul
del "%TEMP%\ytdl.zip" >nul 2>&1

cd /d "%APP_DIR%"

:: ── Python packages ─────────────────────────────────────────────────────────────
echo Checking Python packages...
pip install flask yt-dlp yt-dlp-youtube-oauth2 --quiet --disable-pip-version-check

:: ── Launch ──────────────────────────────────────────────────────────────────────
echo.
echo Starting... your browser will open automatically.
echo (Leave this window open while using the app. Close it to quit.)
echo.

timeout /t 2 /nobreak >nul
start http://localhost:5001
python app.py

pause
