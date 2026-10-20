from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://jukebox:jukebox@localhost:5432/jukebox"

    mpd_host: str = "127.0.0.1"
    mpd_port: int = 6600
    mpd_password: str | None = None
    mpd_timeout_seconds: float = 5.0

    # 같은 Wi-Fi 내부망 HTTP 운영 전제(요구사항 2.1). HTTPS 적용 시 true.
    cookie_secure: bool = False
    session_cookie_name: str = "jb_session"
    session_ttl_student_hours: int = 24 * 7
    session_ttl_admin_hours: int = 12

    login_max_failures: int = 5
    login_lock_minutes: int = 10

    # system_settings.max_volume 이 없을 때의 기본 상한(NFR-006)
    default_max_volume: int = 40

    # 음원 저장 위치. MPD music_directory 와 같은 디렉터리여야 한다(요구사항 4.2).
    media_dir: str = "/srv/ax2-jukebox/media"
    max_upload_mb: int = 50

    # YouTube oEmbed 메타데이터 조회(키 불필요). 인터넷이 없으면 수동 입력으로 대체된다.
    youtube_metadata_enabled: bool = True

    # MPD httpd 출력 주소(서버 내부, 예: http://mpd:8001/). 설정하면 관리자 콘솔의 LISTEN 버튼이
    # /api/player/monitor 로 이 스트림을 받아 이 기기 스피커로 들려준다(노트북 테스트용). 비우면 숨긴다.
    mpd_monitor_url: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
