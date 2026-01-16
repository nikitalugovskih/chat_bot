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

    # Postgres
    pg_host: str = os.getenv("PG_HOST", "localhost")
    pg_port: int = int(os.getenv("PG_PORT", "5432"))
    pg_user: str = os.getenv("PG_USER", "postgres")
    pg_password: str = os.getenv("PG_PASSWORD", "")
    pg_database: str = os.getenv("PG_DATABASE", "postgres")
    pg_sslmode: str = os.getenv("PG_SSLMODE", "disable")

    # YooKassa
    yookassa_enabled: bool = os.getenv("YOOKASSA_ENABLED", "0") == "1"
    yookassa_shop_id: str = os.getenv("YOOKASSA_SHOP_ID", "")
    yookassa_secret_key: str = os.getenv("YOOKASSA_SECRET_KEY", "")
    yookassa_return_url: str = os.getenv("YOOKASSA_RETURN_URL", "")
    # card_price_rub: str = os.getenv("CARD_PRICE_RUB", "199.00")
    card_price_rub: str = os.getenv("CARD_PRICE_RUB", "1")

    @property
    def pg_dsn(self) -> str:
        return (
            f"postgresql://{self.pg_user}:{self.pg_password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_database}"
            f"?sslmode={self.pg_sslmode}"
        )

settings = Settings()