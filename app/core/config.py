import os
from pathlib import Path

# ── Load .env so os.getenv() picks up values from the file ───────────────────
try:
    from dotenv import load_dotenv
    _ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"
    if _ENV_FILE.exists():
        load_dotenv(dotenv_path=_ENV_FILE, override=False)  # won't override real env vars
except ImportError:
    pass  # python-dotenv is optional; fall back to os defaults

BASE_DIR = Path(__file__).resolve().parent.parent.parent

_DEFAULT_SECRET = "change-this-super-secret-key-in-production"  # noqa: S105


class Settings:
    APP_NAME: str = "Employee CRM"
    APP_VERSION: str = "1.0.0"
    SECRET_KEY: str = os.getenv("SECRET_KEY", _DEFAULT_SECRET)

    ENV: str = os.getenv("ENV", "dev")  # dev | test | prod
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"

    # Comma-separated allowed CORS origins (e.g. "https://crm.company.com")
    # Defaults to * in dev, but must be set explicitly in production.
    ALLOWED_ORIGINS: list[str] = [
        o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()
    ]

    # Absolute-path SQLite URLs — data lives in the project root, not a temp dir
    _DATABASE_URL_DEFAULT: str = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR}/emp_crm.db")
    TEST_DATABASE_URL: str = os.getenv("TEST_DATABASE_URL", f"sqlite:///{BASE_DIR}/test_crm.db")

    # ML readiness: set ML_LOGGING=true to enable extra audit/history tables
    ML_LOGGING_ENABLED: bool = os.getenv("ML_LOGGING", "true").lower() == "true"

    @property
    def DATABASE_URL(self) -> str:
        if self.ENV == "test":
            return self.TEST_DATABASE_URL
        return self._DATABASE_URL_DEFAULT

    SESSION_MAX_AGE: int = 60 * 60 * 8  # 8 hours
    LEAVE_ANNUAL_QUOTA: int = 20  # default annual leave days per employee

    # ---------------------------------------------------------------------------
    # Email / SMTP settings
    # Set EMAIL_ENABLED=true in your environment (or .env file) to activate.
    # All other values are read from environment variables; no hard-coded secrets.
    # ---------------------------------------------------------------------------
    EMAIL_ENABLED: bool = os.getenv("EMAIL_ENABLED", "false").lower() == "true"
    SMTP_HOST: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    EMAIL_USER: str = os.getenv("EMAIL_USER", "")        # sender address
    EMAIL_PASSWORD: str = os.getenv("EMAIL_PASSWORD", "")  # app-password / token
    EMAIL_FROM_NAME: str = os.getenv("EMAIL_FROM_NAME", "Employee CRM")

settings = Settings()
