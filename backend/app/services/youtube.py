"""YouTube URL 정규화와 메타데이터 조회.

URL은 곡 식별·검토 용도로만 쓴다. 영상을 내려받지 않는다(요구사항 3장 음원 이용 원칙).
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import httpx

log = logging.getLogger("jukebox.youtube")

_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtu.be"}


@dataclass
class VideoMeta:
    title: str
    channel: str | None
    thumbnail_url: str | None


MetadataFetcher = Callable[[str], VideoMeta | None]


def parse_video_id(url: str) -> str | None:
    """watch?v=, youtu.be/, shorts/, embed/, live/ 형식에서 11자리 video ID를 뽑는다."""
    raw = url.strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "https://" + raw
    try:
        u = urlparse(raw)
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or (u.hostname or "").lower() not in _HOSTS:
        return None
    host = u.hostname.lower()
    parts = [p for p in u.path.split("/") if p]
    candidate = None
    if host.endswith("youtu.be"):
        candidate = parts[0] if parts else None
    elif parts[:1] == ["watch"]:
        candidate = (parse_qs(u.query).get("v") or [None])[0]
    elif len(parts) >= 2 and parts[0] in ("shorts", "embed", "live", "v"):
        candidate = parts[1]
    return candidate if candidate and _ID.fullmatch(candidate) else None


def canonical_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def thumbnail_for(video_id: str) -> str:
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def fetch_oembed(video_id: str) -> VideoMeta | None:
    """oEmbed로 제목·채널·썸네일을 가져온다. 길이(duration)는 oEmbed가 주지 않는다."""
    try:
        res = httpx.get(
            "https://www.youtube.com/oembed",
            params={"url": canonical_url(video_id), "format": "json"},
            timeout=5.0,
        )
        if res.status_code != 200:
            log.info("oembed status=%s video_id=%s", res.status_code, video_id)
            return None
        data = res.json()
        return VideoMeta(
            title=str(data.get("title") or "")[:300] or video_id,
            channel=(str(data["author_name"])[:200] if data.get("author_name") else None),
            thumbnail_url=data.get("thumbnail_url") or thumbnail_for(video_id),
        )
    except (httpx.HTTPError, ValueError) as exc:
        log.info("oembed failed video_id=%s err=%s", video_id, exc)
        return None


def disabled_fetcher(_: str) -> VideoMeta | None:
    return None
