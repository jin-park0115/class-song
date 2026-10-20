"""권리가 확인된 음원 파일 저장(요구사항 4.2, FR-005).

파일명은 사용자 입력이 아니라 트랙 ID로 만든다. 업로드는 임시 파일에 받은 뒤 검증을 통과하면 옮긴다.
"""

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import mutagen

from app.errors import ApiError

ALLOWED = {"mp3": "MP3", "ogg": "OggVorbis", "flac": "FLAC"}
CHUNK = 1024 * 1024


@dataclass
class StoredMedia:
    relpath: str
    sha256: str
    duration_seconds: int


def extension_of(filename: str | None) -> str:
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    if ext not in ALLOWED:
        raise ApiError(422, "UNSUPPORTED_FORMAT", "MP3, OGG, FLAC 파일만 등록할 수 있습니다.")
    return ext


def receive(src: BinaryIO, media_dir: Path, max_bytes: int) -> tuple[Path, str]:
    """임시 파일로 받으며 크기 상한과 SHA-256을 함께 계산한다."""
    media_dir.mkdir(parents=True, exist_ok=True)
    tmp = media_dir / f".incoming-{uuid.uuid4().hex}.part"
    digest = hashlib.sha256()
    size = 0
    try:
        with tmp.open("wb") as out:
            while chunk := src.read(CHUNK):
                size += len(chunk)
                if size > max_bytes:
                    raise ApiError(413, "FILE_TOO_LARGE", f"파일은 {max_bytes // (1024 * 1024)}MB 이하여야 합니다.")
                digest.update(chunk)
                out.write(chunk)
    except BaseException:
        tmp.unlink(missing_ok=True)  # 실패한 부분 파일 정리
        raise
    if size == 0:
        tmp.unlink(missing_ok=True)
        raise ApiError(422, "EMPTY_FILE", "빈 파일입니다.")
    return tmp, digest.hexdigest()


def probe(tmp: Path, ext: str) -> int:
    """실제로 재생 가능한 오디오인지 확인하고 길이(초)를 돌려준다."""
    try:
        audio = mutagen.File(tmp)
    except mutagen.MutagenError:
        audio = None
    if audio is None or type(audio).__name__ != ALLOWED[ext] or not getattr(audio.info, "length", 0):
        raise ApiError(422, "INVALID_AUDIO", "재생할 수 있는 오디오 파일이 아니거나 확장자와 형식이 다릅니다.")
    return max(1, round(audio.info.length))


def finalize(tmp: Path, media_dir: Path, track_id: int, ext: str, old_relpath: str | None) -> str:
    relpath = f"{track_id}.{ext}"
    final = media_dir / relpath
    os.replace(tmp, final)
    final.chmod(0o644)
    if old_relpath and old_relpath != relpath:
        (media_dir / old_relpath).unlink(missing_ok=True)
    return relpath
