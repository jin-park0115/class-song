# AX2 Jukebox API 이미지 (노트북 테스트·강의실 서버 공용)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH

# ffmpeg: yt-dlp가 받은 오디오를 mp3로 바꿀 때 쓴다
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir uv==0.11.32

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# Windows에서 받은 저장소라 줄바꿈이 CRLF여도 시작 스크립트가 동작하도록 CR을 지운다.
# 음원 디렉터리는 MPD 컨테이너와 공유한다. 파일은 0644로 저장되어 MPD가 읽을 수 있다.
RUN useradd --uid 1000 --create-home jukebox \
 && mkdir -p /srv/ax2-jukebox/media && chown jukebox:jukebox /srv/ax2-jukebox/media \
 && sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh \
 && chmod +x /usr/local/bin/docker-entrypoint.sh
USER jukebox

EXPOSE 8000
ENTRYPOINT ["docker-entrypoint.sh"]
# 워커는 반드시 1개(PlayerController 상태가 프로세스 안에 있음)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
