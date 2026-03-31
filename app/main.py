from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from app.core.config import settings
from app.core.database import engine, Base
from app.core.auth import get_session_user

# Import all models so Base.metadata sees them before create_all
import app.models  # noqa: F401

# Import routers
from app.routes import auth, employees, attendance, tasks, leaves, dashboard, notifications, api

# Analytics router — guarded so a broken analytics module never kills the app
try:
    from app.routes import analytics as analytics_router
    _ANALYTICS_OK = True
except Exception as _an_exc:
    print(f"[startup] analytics router skipped: {_an_exc}")
    _ANALYTICS_OK = False

# ---------------------------------------------------------------------------
# Create tables & Run safe schema migrations
# ---------------------------------------------------------------------------
Base.metadata.create_all(bind=engine)

try:
    from app.core.db_migration import apply_safe_migrations
    apply_safe_migrations(engine)
except Exception as e:
    print(f"Skipping migrations, error: {e}")

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    max_age=settings.SESSION_MAX_AGE,
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(auth.router)
app.include_router(employees.router)
app.include_router(attendance.router)
app.include_router(tasks.router)
app.include_router(leaves.router)
app.include_router(dashboard.router)
app.include_router(notifications.router)
app.include_router(api.router)

if _ANALYTICS_OK:
    app.include_router(analytics_router.router)


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
from fastapi.responses import HTMLResponse     # noqa: E402


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
