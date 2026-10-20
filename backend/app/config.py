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


@lru_cache
def get_settings() -> Settings:
    return Settings()
