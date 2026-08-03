from dataclasses import dataclass
from os import getenv


@dataclass(frozen=True)
class Settings:
    env: str = "local"
    database_url: str | None = None
    broker_url: str | None = None
    storage_endpoint: str | None = None
    storage_bucket: str = "evalhub"


def load_settings() -> Settings:
    return Settings(
        env=getenv("EVALHUB_ENV", "local"),
        database_url=getenv("EVALHUB_DATABASE_URL"),
        broker_url=getenv("EVALHUB_BROKER_URL"),
        storage_endpoint=getenv("EVALHUB_STORAGE_ENDPOINT"),
        storage_bucket=getenv("EVALHUB_STORAGE_BUCKET", "evalhub"),
    )
