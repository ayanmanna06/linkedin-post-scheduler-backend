import urllib.parse

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # App
    FLASK_ENV: str = Field(default="development")
    SECRET_KEY: str = Field(default="dev-secret-key-replace-in-production")
    FRONTEND_URL: str = Field(default="http://localhost:5173")
    BACKEND_URL: str = Field(default="http://localhost:5000")

    # Database
    DB_HOST: str = Field(default="127.0.0.1")
    DB_PORT: int = Field(default=3306)
    DB_NAME: str = Field(default="linkedin_scheduler")
    DB_USER: str = Field(default="root")
    DB_PASSWORD: str = Field(default="")
    DB_APP_USER: str | None = Field(default=None)
    DB_APP_PASSWORD: str | None = Field(default=None)

    # LLM (Self-hosted Mistral)
    LLM_API_URL: str = Field(default="http://localhost:11434/api/generate")
    LLM_MODEL: str = Field(default="mistral-small:24b")
    LLM_TIMEOUT_SECONDS: int = Field(default=120)

    # LinkedIn OAuth & API
    LINKEDIN_CLIENT_ID: str = Field(default="")
    LINKEDIN_CLIENT_SECRET: str = Field(default="")
    LINKEDIN_REDIRECT_URI: str = Field(
        default="http://localhost:5000/api/v1/auth/linkedin/callback"
    )
    LINKEDIN_API_VERSION: str = Field(default="202401")

    # Security
    TOKEN_ENCRYPTION_KEY: str = Field(default="")

    # Worker
    WORKER_POLL_INTERVAL_SECONDS: int = Field(default=15)
    MAX_PUBLISH_ATTEMPTS: int = Field(default=5)
    PROCESSING_TIMEOUT_MINUTES: int = Field(default=10)

    model_config = SettingsConfigDict(
        env_file=(".env", "backend/.env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def effective_db_user(self) -> str:
        if self.DB_APP_USER and self.DB_APP_PASSWORD is not None:
            return self.DB_APP_USER
        return self.DB_USER

    @property
    def effective_db_password(self) -> str:
        if self.DB_APP_USER and self.DB_APP_PASSWORD is not None:
            return self.DB_APP_PASSWORD
        return self.DB_PASSWORD

    @property
    def sqlalchemy_database_uri(self) -> str:
        user = urllib.parse.quote_plus(self.effective_db_user)
        password = urllib.parse.quote_plus(self.effective_db_password)
        return (
            f"mysql+pymysql://{user}:{password}@"
            f"{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}?charset=utf8mb4"
        )

    def validate_for_runtime(self) -> None:
        errors = []
        if not self.SECRET_KEY or self.SECRET_KEY == "change-me-to-a-secure-random-string":
            errors.append("SECRET_KEY must be configured in .env")
        if not self.TOKEN_ENCRYPTION_KEY:
            errors.append("TOKEN_ENCRYPTION_KEY must be configured in .env")
        if not self.DB_NAME:
            errors.append("DB_NAME must be specified in .env")
        if errors:
            raise ValueError("Configuration error:\n" + "\n".join(f" - {e}" for e in errors))


settings = Settings()
