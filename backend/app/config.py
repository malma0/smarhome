import os
from dataclasses import dataclass

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str
    anthropic_model: str
    home_assistant_url: str
    home_assistant_token: str
    port: int


def load_settings() -> Settings:
    return Settings(
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        anthropic_model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
        home_assistant_url=os.environ.get("HOME_ASSISTANT_URL", "http://localhost:8123").rstrip("/"),
        home_assistant_token=os.environ.get("HOME_ASSISTANT_TOKEN", ""),
        port=int(os.environ.get("JARVIS_PORT", "8000")),
    )


settings = load_settings()
