from sqlalchemy.orm import Session
from app.models.user import User, UserRole
from app.core.auth import hash_password, verify_password


class AuthError(Exception):
    pass


def register_user(
    db: Session,
    name: str,
    email: str,
    password: str,
    role: str = "employee",
    department: str | None = None,
) -> User:
    if db.query(User).filter(User.email == email).first():
        raise AuthError("Email already registered.")
    try:
        user_role = UserRole(role)
    except ValueError:
        raise AuthError(f"Invalid role: {role}")

    user = User(
        name=name,
        email=email,
        hashed_password=hash_password(password),
        role=user_role,
        department=department,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, email: str, password: str) -> User:
    user = db.query(User).filter(User.email == email, User.is_active == 1).first()
    if not user or not verify_password(password, user.hashed_password):
        raise AuthError("Invalid email or password.")
    return user


def get_user_by_id(db: Session, user_id: int) -> User | None:
    return db.query(User).filter(User.id == user_id, User.is_active == 1).first()
