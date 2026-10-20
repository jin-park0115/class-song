#!/usr/bin/env bash
# 저작권 문제가 없는 30초 테스트 음원(사인파)을 만든다. STEP 01 재생 검증용.
# 사용법: scripts/make-test-tone.sh [출력 디렉터리]
set -euo pipefail
out="${1:-/srv/ax2-jukebox/media}"
mkdir -p "$out"
ffmpeg -loglevel error -y -f lavfi -i "sine=frequency=440:duration=30" \
  -metadata title="Test Tone 440Hz" -metadata artist="AX2 Jukebox" \
  -ac 2 -b:a 192k "$out/test-tone-440.mp3"
ffmpeg -loglevel error -y -f lavfi -i "sine=frequency=660:duration=30" \
  -metadata title="Test Tone 660Hz" -metadata artist="AX2 Jukebox" \
  -ac 2 -b:a 192k "$out/test-tone-660.mp3"
echo "만들었습니다: $out/test-tone-440.mp3, $out/test-tone-660.mp3"
