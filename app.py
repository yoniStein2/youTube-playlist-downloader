import sys
import os
import json
import shutil
import subprocess
import uuid
import zipfile
import threading
import time
import tempfile
from flask import Flask, render_template, request, Response, stream_with_context, send_file, after_this_request

app = Flask(__name__)

IS_WINDOWS = sys.platform == 'win32'
IS_LINUX   = sys.platform == 'linux'


# ── Tool search paths ─────────────────────────────────────────────────────────

def _tool_dirs():
    if IS_WINDOWS:
        local = os.environ.get('LOCALAPPDATA', '')
        return [
            os.path.join(local, 'Microsoft', 'WinGet', 'Links'),
            r'C:\Program Files\FFmpeg\bin',
            r'C:\Program Files (x86)\FFmpeg\bin',
            r'C:\ProgramData\ffmpeg\bin',
            r'C:\ffmpeg\bin',
        ]
    elif IS_LINUX:
        return ['/usr/local/bin', '/usr/bin']
    else:  # macOS
        return ['/opt/homebrew/bin', '/usr/local/bin']

TOOL_DIRS = _tool_dirs()
PATH_SEP  = ';' if IS_WINDOWS else ':'
ENV = os.environ.copy()
ENV['PATH'] = PATH_SEP.join(TOOL_DIRS) + PATH_SEP + ENV.get('PATH', '')

POPEN_KWARGS = {'encoding': 'utf-8'}
if IS_WINDOWS:
    POPEN_KWARGS['creationflags'] = subprocess.CREATE_NO_WINDOW

# ── YouTube cookies (set YOUTUBE_COOKIES env var on the server to bypass bot detection) ──
# Paste the full contents of a cookies.txt file into the Render environment variable.
_COOKIES_FILE = None
_cookies_content = os.environ.get('YOUTUBE_COOKIES', '').strip()
if _cookies_content:
    _tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
    _tmp.write(_cookies_content)
    _tmp.close()
    _COOKIES_FILE = _tmp.name


# ── Session management ────────────────────────────────────────────────────────
# Each download gets a unique session ID. Files live in a temp dir until
# the session expires (2 hours) or the server restarts.

sessions: dict = {}
sessions_lock = threading.Lock()


def _cleanup_loop():
    while True:
        time.sleep(1800)
        cutoff = time.time() - 7200
        with sessions_lock:
            stale = [k for k, v in sessions.items() if v['created'] < cutoff]
            for k in stale:
                shutil.rmtree(sessions[k]['dir'], ignore_errors=True)
                del sessions[k]


threading.Thread(target=_cleanup_loop, daemon=True).start()


# ── Helpers ───────────────────────────────────────────────────────────────────

def find_tool(name):
    filename = (name + '.exe') if IS_WINDOWS else name
    for d in TOOL_DIRS:
        full = os.path.join(d, filename)
        if os.path.isfile(full) and os.access(full, os.X_OK):
            return full
    return shutil.which(name, path=ENV['PATH'])


def sse(data):
    return f"data: {json.dumps(data)}\n\n"


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/download')
def download():
    url = request.args.get('url', '').strip()

    def generate(url):
        if not url:
            yield sse({'status': 'error', 'message': 'Please enter a URL.'})
            return

        # ── Auto-install missing dependencies (local Mac/Windows only) ─────────
        for package in ['yt-dlp', 'ffmpeg']:
            if find_tool(package):
                continue

            if IS_LINUX:
                yield sse({'status': 'error',
                           'message': f'{package} is not installed on the server. '
                                      'Contact the server admin.'})
                return

            yield sse({'status': 'installing', 'package': package})

            if IS_WINDOWS:
                winget = shutil.which('winget', path=ENV['PATH'])
                if not winget:
                    yield sse({'status': 'error',
                               'message': 'winget not found. Visit https://aka.ms/getwinget'})
                    return
                winget_ids = {'yt-dlp': 'yt-dlp.yt-dlp', 'ffmpeg': 'Gyan.FFmpeg'}
                yield sse({'line': f'--- Installing {package} via winget ---'})
                proc = subprocess.Popen(
                    [winget, 'install', winget_ids[package],
                     '--accept-source-agreements', '--accept-package-agreements', '--silent'],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **POPEN_KWARGS
                )
                for line in iter(proc.stdout.readline, ''):
                    line = line.rstrip()
                    if line:
                        yield sse({'line': line})
                proc.wait()
                if proc.returncode != 0:
                    yield sse({'status': 'error',
                               'message': f'Failed to install {package}.'})
                    return

            else:  # macOS
                brew = find_tool('brew') or shutil.which('brew')
                if not brew:
                    yield sse({'status': 'error',
                               'message': 'Homebrew not found. Visit https://brew.sh'})
                    return
                yield sse({'line': f'--- Installing {package} via Homebrew ---'})
                proc = subprocess.Popen(
                    [brew, 'install', package],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **POPEN_KWARGS
                )
                for line in iter(proc.stdout.readline, ''):
                    line = line.rstrip()
                    if line:
                        yield sse({'line': line})
                proc.wait()
                if proc.returncode != 0:
                    yield sse({'status': 'error',
                               'message': f'Failed to install {package}. Try: brew install {package}'})
                    return

            yield sse({'line': f'--- {package} installed successfully ---'})

        # ── Download ──────────────────────────────────────────────────────────
        session_id = str(uuid.uuid4())
        tmp_dir = tempfile.mkdtemp(prefix='ytdl-')
        with sessions_lock:
            sessions[session_id] = {'dir': tmp_dir, 'files': [], 'created': time.time()}

        yield sse({'status': 'downloading'})

        yt_dlp = find_tool('yt-dlp')
        try:
            cmd = [
                yt_dlp, '-x',
                '--audio-format', 'mp3',
                '--audio-quality', '0',
                '--newline',
                '--extractor-args', 'youtube:player_client=android,ios,web',
                '--js-runtimes', 'node',
                '-o', os.path.join(tmp_dir, '%(title)s.%(ext)s'),
            ]
            if _COOKIES_FILE:
                cmd += ['--cookies', _COOKIES_FILE]
            cmd.append(url)

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **POPEN_KWARGS
            )
            for line in iter(process.stdout.readline, ''):
                line = line.rstrip()
                if line:
                    yield sse({'line': line})
            process.wait()

            if process.returncode == 0:
                files = sorted(f for f in os.listdir(tmp_dir) if f.endswith('.mp3'))
                with sessions_lock:
                    sessions[session_id]['files'] = files
                yield sse({'status': 'done', 'session_id': session_id, 'files': files})
            else:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                with sessions_lock:
                    sessions.pop(session_id, None)
                yield sse({'status': 'error', 'message': 'Download failed. Check the log above.'})

        except Exception as e:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            with sessions_lock:
                sessions.pop(session_id, None)
            yield sse({'status': 'error', 'message': str(e)})

    return Response(
        stream_with_context(generate(url)),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
    )


@app.route('/files/<session_id>/<path:filename>')
def serve_file(session_id, filename):
    with sessions_lock:
        session = sessions.get(session_id)
    if not session:
        return 'Session expired — please download the playlist again.', 404

    # Prevent path traversal
    safe_path = os.path.realpath(os.path.join(session['dir'], filename))
    if not safe_path.startswith(os.path.realpath(session['dir'])):
        return 'Invalid path', 400
    if not os.path.isfile(safe_path):
        return 'File not found', 404

    return send_file(safe_path, as_attachment=True,
                     download_name=os.path.basename(safe_path))


@app.route('/zip/<session_id>')
def serve_zip(session_id):
    with sessions_lock:
        session = sessions.get(session_id)
    if not session:
        return 'Session expired — please download the playlist again.', 404

    # Build (or reuse) the ZIP inside the session's temp dir
    zip_path = os.path.join(session['dir'], '_playlist.zip')
    if not os.path.isfile(zip_path):
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for filename in session['files']:
                filepath = os.path.join(session['dir'], filename)
                if os.path.isfile(filepath):
                    zf.write(filepath, filename)

    return send_file(zip_path, as_attachment=True, download_name='playlist.zip',
                     mimetype='application/zip')


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(debug=False, port=port, threaded=True)
