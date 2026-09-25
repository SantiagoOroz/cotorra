# One image for everything: it runs the gateway, and the gateway spawns the
# workers from the same image. Nothing to build twice, nothing to keep in sync.
FROM python:3.12-slim AS base

# ffmpeg is the only system dependency. yt-dlp is there so a conference can
# caption a YouTube stream without rebuilding the image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg curl \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so editing source does not invalidate the layer.
COPY pyproject.toml README.md ./
COPY src/cotorra/__init__.py src/cotorra/__init__.py
RUN pip install --no-cache-dir ".[gemini]" yt-dlp

COPY src/ src/
COPY web/ web/
COPY samples/ samples/
COPY cotorra.yaml ./
COPY data/glossaries/ data/glossaries/
RUN pip install --no-cache-dir --no-deps -e .

# Captions are public; there is no reason for this process to be root.
RUN useradd --create-home --uid 10001 cotorra \
 && mkdir -p /app/data/transcripts \
 && chown -R cotorra:cotorra /app
USER cotorra

EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=4s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:8080/api/health || exit 1

CMD ["cotorra", "serve"]
