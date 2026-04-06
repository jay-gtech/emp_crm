import logging
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.core.config import settings

_log = logging.getLogger(__name__)

# ▀▀ Safety guard — never allow an in-memory SQLite database in this project ▀▀
if ":memory:" in settings.DATABASE_URL:
    raise RuntimeError(
        "[database.py] In-memory SQLite (':memory:') is NOT allowed. "
        "Set DATABASE_URL to a file-based path in your .env."
    )

_DB_URL = settings.DATABASE_URL
_IS_SQLITE = _DB_URL.startswith("sqlite")

# ▀▀ Dialect-aware engine — SQLite and PostgreSQL have different connect_args ▀▀
if _IS_SQLITE:
    engine = create_engine(
        _DB_URL,
        connect_args={
            "check_same_thread": False,  # required for SQLite with FastAPI
            "timeout": 30,               # wait up to 30s if DB is locked
        },
        pool_pre_ping=True,
        echo=False,
    )
else:
    # PostgreSQL / other RDBMS — no SQLite-specific args; use connection pool
    engine = create_engine(
        _DB_URL,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        pool_timeout=30,
        echo=False,
    )

# WAL mode + FK enforcement — SQLite only
if _IS_SQLITE:
    @event.listens_for(engine, "connect")
    def _set_wal_mode(dbapi_conn, _connection_record):
        """Switch SQLite to WAL mode on every new raw connection."""
        try:
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA synchronous=NORMAL;")
            cursor.execute("PRAGMA foreign_keys=ON;")
            cursor.close()
        except Exception as exc:
            _log.warning("[database.py] PRAGMA setup warning: %s", exc)


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency that yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
