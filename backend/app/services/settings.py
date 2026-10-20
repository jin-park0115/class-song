from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import SystemSetting


def get_setting(db: Session, key: str, default: object) -> object:
    row = db.get(SystemSetting, key)
    return default if row is None else row.value_json


def get_max_volume(db: Session) -> int:
    value = get_setting(db, "max_volume", get_settings().default_max_volume)
    return max(0, min(100, int(value)))
