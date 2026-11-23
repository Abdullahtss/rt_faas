from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "RT-FAAS"
    api_token: str = "rtfaas-dev-token"
    database_url: str = "sqlite+aiosqlite:///./data/rtfaas.db"
    cors_origins: str = "http://localhost:8000,http://127.0.0.1:8000"
    simulator_tps: float = 2.0

    @property
    def origins(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
