#!/bin/bash
# YouTube Playlist Downloader — Mac launcher
# Double-click this file to install everything and open the app.

APP_DIR="$HOME/YouTubeDownloader"
REPO_URL="https://github.com/yoniStein2/youTube-playlist-downloader.git"

clear
echo "======================================"
echo "   YouTube Playlist Downloader"
echo "======================================"
echo ""

# ── Homebrew ───────────────────────────────────────────────────────────────────
if ! command -v brew &>/dev/null; then
    # Try common paths first (faster than running the installer)
    for p in /opt/homebrew/bin/brew /usr/local/bin/brew; do
        [ -f "$p" ] && eval "$($p shellenv)" && break
    done
fi

if ! command -v brew &>/dev/null; then
    echo "Installing Homebrew (one-time, takes ~2 min)..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Re-source for Apple Silicon
    [ -f /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)"
    [ -f /usr/local/bin/brew ]    && eval "$(/usr/local/bin/brew shellenv)"
fi

# ── Python ─────────────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "Installing Python (one-time)..."
    brew install python
fi

# ── Git ────────────────────────────────────────────────────────────────────────
if ! command -v git &>/dev/null; then
    echo "Installing git (one-time)..."
    brew install git
fi

# ── Download / update the app ──────────────────────────────────────────────────
if [ -d "$APP_DIR/.git" ]; then
    echo "Checking for updates..."
    git -C "$APP_DIR" pull --quiet
else
    echo "Downloading app (one-time)..."
    git clone "$REPO_URL" "$APP_DIR" --quiet
fi

cd "$APP_DIR"

# ── Python packages ────────────────────────────────────────────────────────────
echo "Checking Python packages..."
python3 -m pip install flask yt-dlp --quiet --disable-pip-version-check

# ── Launch ─────────────────────────────────────────────────────────────────────
echo ""
echo "Starting... your browser will open automatically."
echo "(Leave this window open while using the app. Close it to quit.)"
echo ""

# Start Flask in the background
python3 app.py &
FLASK_PID=$!

# Wait until Flask is ready (up to 15 seconds), then open the browser
for i in {1..15}; do
    sleep 1
    if curl -s http://localhost:5001 > /dev/null 2>&1; then
        open "http://localhost:5001"
        break
    fi
done

# Keep terminal open while Flask runs
wait $FLASK_PID
