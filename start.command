#!/bin/bash
cd "$(dirname "$0")"
echo "Installing dependencies..."
pip3 install flask --quiet 2>/dev/null || pip install flask --quiet
echo "Starting YouTube Playlist Downloader..."
open http://localhost:5001
python3 app.py
