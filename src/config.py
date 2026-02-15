from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Telegram
    telegram_bot_token: str = ""

    # OpenAI (Whisper)
    openai_api_key: str = ""

    # Anthropic (Claude)
    anthropic_api_key: str = ""

    # API Auth
    api_secret_key: str = "change-me-in-production"
    api_access_token_expire_minutes: int = 1440

    # Google Calendar
    google_calendar_enabled: bool = False
    google_credentials_file: str = "credentials.json"

    # Database
    database_path: str = "data/notetaker.db"

    # Server
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # MCP
    mcp_transport: str = "stdio"  # "stdio" or "sse"
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8080

    @property
    def db_path(self) -> Path:
        path = Path(self.database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
