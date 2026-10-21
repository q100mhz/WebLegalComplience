from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://wlc:wlc@localhost:5432/wlc"
    redis_url: str = "redis://localhost:6379/0"

    # Лимиты открытой версии
    max_pages: int = 30
    max_depth: int = 3
    page_timeout_ms: int = 15000
    scan_timeout_s: int = 300
    rate_limit_per_hour: int = 10
    dedup_minutes: int = 15
    worker_concurrency: int = 2

    data_dir: str = "/data"  # скриншоты-доказательства
    screenshots_keep_days: int = 90

    # Админка
    admin_login: str = "admin"
    admin_password: str = ""  # пустой пароль = вход в админку отключён
    secret_key: str = "dev-secret-change-me"
    cookie_secure: bool = True

    # Открытые данные РКН: реестр операторов ПДн
    rkn_opendata_url: str = "https://rkn.gov.ru/opendata/7705846236-OperatorsPD/"

    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36 WebLegalComplianceBot/1.0 "
        "(+https://weblegalcompliance.itb2bhost.ru)"
    )


settings = Settings()


def shots_dir(scan_id: str) -> Path:
    return Path(settings.data_dir) / "shots" / scan_id
