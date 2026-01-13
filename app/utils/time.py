from datetime import datetime, date
from zoneinfo import ZoneInfo

def now_msk(tz_name: str = "Europe/Moscow") -> datetime:
    return datetime.now(tz=ZoneInfo(tz_name))

def today_msk(tz_name: str = "Europe/Moscow") -> date:
    return now_msk(tz_name).date()