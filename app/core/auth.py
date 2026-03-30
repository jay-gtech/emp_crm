from passlib.context import CryptContext
from fastapi import Request, HTTPException, status
from functools import wraps
from typing import Optional

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def set_session_user(request: Request, user_id: int, role: str, name: str) -> None:
    request.session["user_id"] = user_id
    request.session["role"] = role
    request.session["name"] = name


def get_session_user(request: Request) -> Optional[dict]:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return {
        "user_id": user_id,
        "role": request.session.get("role"),
        "name": request.session.get("name"),
    }


def clear_session(request: Request) -> None:
    request.session.clear()


# ---------------------------------------------------------------------------
# Route-level guards (use as dependencies)
# ---------------------------------------------------------------------------

def login_required(request: Request):
    """Dependency: raises 302-redirect if not logged in."""
    user = get_session_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/auth/login"},
        )
    return user


def role_required(*allowed_roles: str):
    """Dependency factory: raises 403 if role not in allowed_roles."""
    def dependency(request: Request):
        user = login_required(request)
        if user["role"] not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action.",
            )
        return user
    return dependency
