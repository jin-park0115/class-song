#!/usr/bin/env bash
# MPD 로컬 제어 검증(요구사항 15장 STEP 01-3). mpc 필요: sudo apt install mpc
# 사용법: scripts/mpd-smoke-test.sh   (MPD_HOST/MPD_PORT 환경변수로 대상 변경)
set -euo pipefail
export MPD_HOST="${MPD_HOST:-127.0.0.1}" MPD_PORT="${MPD_PORT:-6600}"
step() { printf '\n== %s\n' "$*"; }

step "버전/출력 장치";  mpc version; mpc outputs
step "라이브러리 갱신"; mpc --wait update
step "대기열 구성";     mpc clear; mpc ls | grep -E '\.(mp3|ogg|flac)$' | mpc add; mpc playlist
step "재생";            mpc play; sleep 3; mpc status
step "일시정지";        mpc pause; sleep 1; mpc status
step "볼륨 30";         mpc volume 30; mpc status | tail -1
step "다음 곡";         mpc play; mpc next; sleep 2; mpc current
step "정지";            mpc stop; mpc status
echo; echo "PASS: MPD 로컬 제어 정상"
