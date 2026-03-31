import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

class Settings:
    APP_NAME: str = "Employee CRM"
    APP_VERSION: str = "1.0.0"
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-this-super-secret-key-in-production")
    DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR}/emp_crm.db")
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
