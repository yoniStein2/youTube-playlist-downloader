@echo off
cd /d "%~dp0"
echo Installing dependencies...
pip install flask --quiet
echo Starting YouTube Playlist Downloader...
start http://localhost:5001
python app.py
pause
