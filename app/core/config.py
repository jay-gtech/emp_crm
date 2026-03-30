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

settings = Settings()
