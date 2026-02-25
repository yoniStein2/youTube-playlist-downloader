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
import base64
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

# ── OAuth2 token (preferred) ──────────────────────────────────────────────────
# Set YT_OAUTH_TOKEN in Render env vars after completing /auth setup.
CACHE_DIR        = '/tmp/yt-dlp-cache'
OAUTH_TOKEN_FILE = os.path.join(CACHE_DIR, 'youtube', 'oauth2_token.json')

_oauth_token_b64 = os.environ.get('YT_OAUTH_TOKEN', '').strip()
if _oauth_token_b64:
    os.makedirs(os.path.dirname(OAUTH_TOKEN_FILE), exist_ok=True)
    with open(OAUTH_TOKEN_FILE, 'wb') as _f:
        _f.write(base64.b64decode(_oauth_token_b64))

# ── YouTube cookies (fallback if OAuth2 not set up) ───────────────────────────
_COOKIES_FILE = None
_cookies_content = os.environ.get('YOUTUBE_COOKIES', '').strip().replace('\r\n', '\n').replace('\r', '\n')
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
        if os.path.isfile(OAUTH_TOKEN_FILE):
            yield sse({'line': '--- YouTube OAuth2 token loaded ---'})
        elif _COOKIES_FILE:
            yield sse({'line': '--- YouTube cookies loaded ---'})
        else:
            yield sse({'line': 'WARNING: No YouTube authentication set. Downloads may be blocked.'})

        yt_dlp = find_tool('yt-dlp')
        try:
            cmd = [
                yt_dlp, '-x',
                '--audio-format', 'mp3',
                '--audio-quality', '0',
                '--newline',
                '--extractor-args', 'youtube:player_client=android,ios,web',
                '--extractor-args', 'youtubetab:skip=authcheck',
                '--js-runtimes', 'node',
                '-o', os.path.join(tmp_dir, '%(title)s.%(ext)s'),
            ]
            if os.path.isfile(OAUTH_TOKEN_FILE):
                cmd += ['--username', 'oauth2', '--password', '', '--cache-dir', CACHE_DIR]
            elif _COOKIES_FILE:
                cmd += ['--cookies', _COOKIES_FILE]
            proxy = os.environ.get('PROXY_URL', '').strip()
            if proxy:
                cmd += ['--proxy', proxy]
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


@app.route('/auth')
def auth_page():
    return render_template('auth.html')


@app.route('/auth-stream')
def auth_stream():
    def generate():
        os.makedirs(os.path.dirname(OAUTH_TOKEN_FILE), exist_ok=True)
        yt_dlp = find_tool('yt-dlp')
        process = subprocess.Popen(
            [yt_dlp, '--username', 'oauth2', '--password', '',
             '--cache-dir', CACHE_DIR,
             '--skip-download',
             'https://www.youtube.com/watch?v=jNQXAC9IVRw'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **POPEN_KWARGS
        )
        for line in iter(process.stdout.readline, ''):
            line = line.rstrip()
            if line:
                yield sse({'line': line})
        process.wait()

        # Find token file — search the whole cache dir since the plugin
        # may store it at a different subpath depending on version
        import glob
        token_file = None
        for path in glob.glob(os.path.join(CACHE_DIR, '**', '*.json'), recursive=True):
            try:
                with open(path) as f:
                    data = json.load(f)
                if 'access_token' in data or 'token_type' in data or 'refresh_token' in data:
                    token_file = path
                    break
            except Exception:
                pass

        if token_file:
            with open(token_file, 'rb') as f:
                token_b64 = base64.b64encode(f.read()).decode()
            yield sse({'status': 'done', 'token': token_b64, 'path': token_file})
        else:
            yield sse({'status': 'error', 'message': 'Auth completed but token file not found. Try again.'})

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
    )


@app.route('/cookies')
def cookies_page():
    return render_template('cookies.html')


@app.route('/update-cookies', methods=['POST'])
def update_cookies():
    global _COOKIES_FILE
    f = request.files.get('cookies')
    if not f:
        return 'No file provided', 400
    content = f.read().decode('utf-8').strip().replace('\r\n', '\n').replace('\r', '\n')
    if not content:
        return 'Empty file', 400
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
    tmp.write(content)
    tmp.close()
    _COOKIES_FILE = tmp.name
    return 'OK', 200


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
