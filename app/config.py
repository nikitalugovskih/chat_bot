from dataclasses import dataclass, field
import os
from dotenv import load_dotenv

load_dotenv()

def _parse_admin_ids(raw: str) -> set[int]:
    return {int(x.strip()) for x in raw.split(",") if x.strip()}

@dataclass(frozen=True)
class Settings:
    bot_token: str = os.getenv("BOT_TOKEN", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-5")
    free_limit: int = int(os.getenv("FREE_LIMIT", "5"))
    daily_hard_limit: int = int(os.getenv("DAILY_HARD_LIMIT", "30"))
    use_fake_db: bool = os.getenv("USE_FAKE_DB", "1") == "1"
    tz: str = os.getenv("TZ", "Europe/Moscow")
    admin_ids: set[int] = field(default_factory=lambda: _parse_admin_ids(os.getenv("ADMIN_IDS", "")))

settings = Settings()