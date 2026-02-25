FROM python:3.11-slim

# Install ffmpeg (yt-dlp is installed via pip in requirements.txt)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render injects $PORT at runtime; default to 5001 locally
ENV PORT=5001
EXPOSE 5001

CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT} --timeout 600 --worker-class gevent --workers 1 app:app"]
