from datetime import datetime
from zoneinfo import ZoneInfo

INDONESIA_TZ = ZoneInfo("Asia/Jakarta")


def now_indonesia() -> datetime:
    return datetime.now(INDONESIA_TZ)


def convert_to_indonesia(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=INDONESIA_TZ)
    return dt.astimezone(INDONESIA_TZ)


def format_indonesia(dt: datetime | None, fmt: str = "%d/%m/%Y %H:%M:%S WIB") -> str:
    if dt is None:
        return "-"
    return convert_to_indonesia(dt).strftime(fmt)
