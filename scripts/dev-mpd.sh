#!/usr/bin/env bash
# 개발용 MPD를 null 출력으로 포그라운드 실행한다. 테스트 음원이 없으면 만든다.
# 사용법: scripts/dev-mpd.sh   (종료: Ctrl+C)
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
state="$here/.dev/mpd"
media="$here/.dev/media"
mkdir -p "$state" "$media"
[ -n "$(ls -A "$media" 2>/dev/null)" ] || "$here/scripts/make-test-tone.sh" "$media"
sed -e "s|MEDIA_DIR|$media|" -e "s|STATE_DIR|$state|" "$here/deploy/mpd/mpd.dev.conf" > "$state/mpd.conf"
exec mpd --no-daemon --stderr "$state/mpd.conf"
