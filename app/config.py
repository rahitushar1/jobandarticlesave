from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache
from typing import Optional


class Settings(BaseSettings):
    # ── OpenAI ────────────────────────────────────────────────
    openai_api_key: Optional[str] = Field(None, env="OPENAI_API_KEY")
    openai_base_url: Optional[str] = Field(None, env="OPENAI_BASE_URL")
    groq_api_key: Optional[str] = Field(None, env="GROQ_API_KEY")
    groq_base_url: Optional[str] = Field("https://api.groq.com/openai/v1", env="GROQ_BASE_URL")
    openai_model: Optional[str] = Field(None, env="OPENAI_MODEL")
    openai_vision_model: Optional[str] = Field(None, env="OPENAI_VISION_MODEL")

    # ── Telegram ──────────────────────────────────────────────
    telegram_bot_token: str = Field(..., env="TELEGRAM_BOT_TOKEN")
    telegram_webhook_url: Optional[str] = Field(None, env="TELEGRAM_WEBHOOK_URL")
    telegram_allowed_user_ids: str = Field("", env="TELEGRAM_ALLOWED_USER_IDS")

    # ── Google Sheets ─────────────────────────────────────────
    google_service_account_json: str = Field(..., env="GOOGLE_SERVICE_ACCOUNT_JSON")
    google_spreadsheet_id: str = Field(..., env="GOOGLE_SPREADSHEET_ID")
    sheet_jobs_tab: str = Field("Jobs_Internships", env="SHEET_JOBS_TAB")
    sheet_other_tab: str = Field("Other_Captures", env="SHEET_OTHER_TAB")

    # ── App ───────────────────────────────────────────────────
    app_env: str = Field("development", env="APP_ENV")
    log_level: str = Field("INFO", env="LOG_LEVEL")
    sqlite_db_path: str = Field("data/capture.db", env="SQLITE_DB_PATH")
    upload_dir: str = Field("data/uploads", env="UPLOAD_DIR")

    # ── Web adapter ───────────────────────────────────────────
    web_api_key: Optional[str] = Field(None, env="WEB_API_KEY")
    web_enabled: bool = Field(False, env="WEB_ENABLED")

    # ── Behaviour ─────────────────────────────────────────────
    dedup_enabled: bool = Field(True, env="DEDUP_ENABLED")
    max_image_size_mb: int = Field(20, env="MAX_IMAGE_SIZE_MB")
    url_fetch_timeout: int = Field(15, env="URL_FETCH_TIMEOUT")
    ai_max_retries: int = Field(3, env="AI_MAX_RETRIES")

    @property
    def allowed_telegram_ids(self) -> list[int]:
        raw = self.telegram_allowed_user_ids.strip()
        if not raw:
            return []
        return [int(x.strip()) for x in raw.split(",") if x.strip()]

    @property
    def ai_api_key(self) -> str:
        api_key = self.openai_api_key or self.groq_api_key
        if not api_key:
            raise ValueError("Set OPENAI_API_KEY or GROQ_API_KEY in .env")
        return api_key

    @property
    def ai_base_url(self) -> Optional[str]:
        if self.openai_base_url:
            return self.openai_base_url
        if self.groq_api_key:
            return self.groq_base_url
        return None

    @property
    def ai_text_model(self) -> str:
        if self.openai_model:
            return self.openai_model
        if self.groq_api_key:
            return "llama-3.3-70b-versatile"
        return "gpt-4o"

    @property
    def ai_vision_model(self) -> str:
        if self.openai_vision_model:
            return self.openai_vision_model
        if self.groq_api_key:
            return "meta-llama/llama-4-scout-17b-16e-instruct"
        return "gpt-4o"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
