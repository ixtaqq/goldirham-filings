from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    sec_user_agent: str = Field(
        default="",
        description="SEC fair-access policy requires a real name and contact email.",
    )
    database_url: str = "postgresql://filings:filings@127.0.0.1:5442/filings"
    ollama_url: str = "http://127.0.0.1:11434"
    answer_model: str = "qwen3:8b"
    embedding_model: str = "nomic-embed-text"
    abstain_min_similarity: float = 0.0
    data_dir: Path = ROOT / "data"
    sec_requests_per_second: float = 6.0


@lru_cache
def settings() -> Settings:
    return Settings()
