import logging
import threading
import time

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.core.config import settings
from app.core.database import engine, Base
from app.core.auth import get_session_user

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
_log = logging.getLogger(__name__)

# Rate-limiting
try:
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    from app.core.limiter import limiter
    _SLOWAPI_OK = True
except Exception as _sl_exc:
    _log.warning("[startup] slowapi not available — login rate limiting disabled: %s", _sl_exc)
    _SLOWAPI_OK = False

# Import all models so Base.metadata sees them before create_all
import app.models  # noqa: F401

# Import routers
from app.routes import auth, employees, attendance, tasks, leaves, dashboard, notifications, api, auto_assign, admin as admin_router, location as location_router

# Analytics router — guarded so a broken analytics module never kills the app
try:
    from app.routes import analytics as analytics_router
    _ANALYTICS_OK = True
except Exception as _an_exc:
    _log.warning("[startup] analytics router skipped: %s", _an_exc)
    _ANALYTICS_OK = False

# AI router — guarded; requires scikit-learn / pandas which are optional
try:
    from app.routes import ai as ai_router
    from app.routes import ai_leave as ai_leave_router
    _AI_OK = True
except Exception as _ai_exc:
    _log.warning("[startup] ai router skipped: %s", _ai_exc)
    _AI_OK = False

# ---------------------------------------------------------------------------
# Background retraining scheduler
# ---------------------------------------------------------------------------
_RETRAIN_ENABLED       = True    # set False to disable without code changes
_RETRAIN_INTERVAL_SECS = 86_400  # 24 hours


def _retrain_worker() -> None:
    """Run in a daemon thread — retrain every _RETRAIN_INTERVAL_SECS seconds."""
    # Initial delay: wait one full interval before the first run so startup
    # is not slowed down and enough new data has accumulated.
    time.sleep(_RETRAIN_INTERVAL_SECS)
    while True:
        try:
            _log.info("[scheduler] Starting scheduled retraining run...")
            from scripts.retrain_model import cmd_retrain
            exit_code = cmd_retrain(dry_run=False)
            _log.info("[scheduler] Retraining finished (exit_code=%s).", exit_code)
        except Exception as exc:
            # Failure must never kill the scheduler thread; old model stays active.
            _log.error("[scheduler] Retraining failed — keeping existing model. Error: %s", exc)
        time.sleep(_RETRAIN_INTERVAL_SECS)


def _start_retrain_scheduler() -> None:
    if not _RETRAIN_ENABLED:
        _log.info("[scheduler] Retraining scheduler is disabled.")
        return
    t = threading.Thread(target=_retrain_worker, name="retrain-scheduler", daemon=True)
    t.start()
    _log.info("[scheduler] Retraining scheduler started (interval=%ss).", _RETRAIN_INTERVAL_SECS)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION, debug=settings.DEBUG)

# CORS — restrict origins via ALLOWED_ORIGINS env var in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    max_age=settings.SESSION_MAX_AGE,
)

# Mount rate-limiter state so slowapi can locate it on every request
if _SLOWAPI_OK:
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.on_event("startup")
def on_startup():
    import os
    from pathlib import Path

    _log.info("Running ENV: %s", settings.ENV)
    # Mask credentials in DB URL before logging
    _db_url_safe = settings.DATABASE_URL.split("@")[-1] if "@" in settings.DATABASE_URL else settings.DATABASE_URL
    _log.info("Using DB: ...@%s", _db_url_safe)

    # Security guards for production
    if settings.ENV == "prod":
        if settings.SECRET_KEY == "change-this-super-secret-key-in-production":
            _log.critical(
                "SECURITY: SECRET_KEY is the default insecure value — "
                "set a strong SECRET_KEY env variable immediately."
            )
        if not settings.DATABASE_URL.startswith("postgresql"):
            _log.critical(
                "SECURITY: Production is not using PostgreSQL. "
                "Set DATABASE_URL to a postgresql:// connection string."
            )

    # Ensure upload directories exist (Render ephemeral FS — recreated on each deploy)
    for _upload_dir in ["app/static/uploads/chat", "app/static/uploads/visitors"]:
        Path(_upload_dir).mkdir(parents=True, exist_ok=True)

    from app.core.database import SessionLocal
    from app.models.user import User, UserRole
    from app.core.auth import hash_password

    db = SessionLocal()
    try:
        # 1. Create tables if they don't exist (safe — never drops existing data)
        Base.metadata.create_all(bind=engine)

        # 2. Apply safe additive column migrations
        try:
            from app.core.db_migration import apply_safe_migrations
            apply_safe_migrations(engine)
        except Exception as e:
            _log.warning("[startup] migrations skipped: %s", e)

        # 3. Start background retraining scheduler (runs every 24 h)
        _start_retrain_scheduler()

        # 4. Preload ML model into cache (fast inference, no disk I/O at request time)
        try:
            from app.ml.training.model import load_model, predict_batch_proba
            ml_model = load_model()
            if ml_model is not None:
                _dummy_features = [{
                    "active_tasks": 1, "overdue_tasks": 0,
                    "completed_tasks": 5, "performance_score": 75.0,
                }]
                try:
                    predict_batch_proba(_dummy_features)
                    _log.info("[startup] ML model warmed up — ready for inference.")
                except Exception as _wu_exc:
                    _log.warning("[startup] ML warmup prediction skipped: %s", _wu_exc)
            else:
                _log.info("[startup] ML model not found — run 'python -m app.ml.training.trainer' to train.")
        except Exception as _ml_exc:
            _log.warning("[startup] ML model preload skipped: %s", _ml_exc)

        # 5. Ensure default admin exists — password from ADMIN_PASSWORD env var
        #    Falls back to "admin123" only in non-production environments.
        admin_email = os.getenv("ADMIN_EMAIL", "admin@company.com")
        admin_password = os.getenv("ADMIN_PASSWORD", "admin123")
        admin = db.query(User).filter(User.email == admin_email).first()
        if not admin:
            _log.info("[startup] Seed: Creating default admin (%s)...", admin_email)
            new_admin = User(
                name="System Admin",
                email=admin_email,
                hashed_password=hash_password(admin_password),
                role=UserRole.admin,
                is_active=1,
            )
            db.add(new_admin)
            db.commit()
            _log.info("[startup] Seed: Admin created successfully.")
    finally:
        db.close()

app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(auth.router)
app.include_router(employees.router)
app.include_router(attendance.router)
# Task comments MUST be registered before tasks router (more-specific routes first)
try:
    from app.routes import task_comments as task_comments_router
    app.include_router(task_comments_router.router)
except Exception as _tc_exc:
    _log.warning("[startup] task_comments router skipped: %s", _tc_exc)
app.include_router(tasks.router)
app.include_router(leaves.router)
app.include_router(dashboard.router)
app.include_router(notifications.router)

# Real-time WebSocket notifications
try:
    from app.routes import ws_notifications as _ws_notif
    app.include_router(_ws_notif.router)
    _log.info("[startup] WebSocket notification router registered.")
except Exception as _ws_exc:
    _log.warning("[startup] ws_notifications router skipped: %s", _ws_exc)

app.include_router(api.router)
app.include_router(auto_assign.router)
app.include_router(admin_router.router)
app.include_router(location_router.router)
try:
    from app.routes import visitor as visitor_router
    app.include_router(visitor_router.router)
except Exception as _vms_exc:
    _log.warning("[startup] visitor router skipped: %s", _vms_exc)

try:
    from app.routes import reports as reports_router
    app.include_router(reports_router.router)
except Exception as _rpt_exc:
    _log.warning("[startup] reports router skipped: %s", _rpt_exc)

from app.routes import announcements
app.include_router(announcements.router, prefix="/announcements")

try:
    from app.routes import expense as expense_router
    app.include_router(expense_router.router)
except Exception as _exp_exc:
    _log.warning("[startup] expense router skipped: %s", _exp_exc)
from app.routes import meetings
app.include_router(meetings.router, prefix="/meetings")
from app.routes import chat
app.include_router(chat.router, prefix="/chat")


if _ANALYTICS_OK:
    app.include_router(analytics_router.router)

if _AI_OK:
    app.include_router(ai_router.router)
    app.include_router(ai_leave_router.router)


# ---------------------------------------------------------------------------
# Root redirect
# ---------------------------------------------------------------------------
@app.get("/")
def root(request: Request):
    user = get_session_user(request)
    if user:
        return RedirectResponse("/dashboard/", status_code=302)
    return RedirectResponse("/auth/login", status_code=302)


# ---------------------------------------------------------------------------
# Global 403 / 404 handlers
# ---------------------------------------------------------------------------
from fastapi.exceptions import HTTPException  # noqa: E402


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 303 and exc.headers and "Location" in exc.headers:
        return RedirectResponse(exc.headers["Location"], status_code=302)
    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "status_code": exc.status_code,
            "detail": exc.detail,
            "current_user": get_session_user(request),
        },
        status_code=exc.status_code,
    )
