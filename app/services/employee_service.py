from sqlalchemy.orm import Session
from app.models.user import User, UserRole
from app.core.auth import hash_password

# Import safely just in case
try:
    from app.services.hierarchy_service import apply_hierarchy_filter
except ImportError:
    apply_hierarchy_filter = None



class EmployeeError(Exception):
    pass


def list_employees(
    db: Session, 
    department: str | None = None, 
    request_user: dict | None = None
) -> list[User]:
    q = db.query(User).filter(User.is_active == 1)
    if department:
        q = q.filter(User.department == department)
        
    result = q.order_by(User.name).all()
    
    if request_user and apply_hierarchy_filter:
        result = apply_hierarchy_filter(db, request_user, result)
        
    return result


def get_employee(db: Session, employee_id: int) -> User:
    user = db.query(User).filter(User.id == employee_id, User.is_active == 1).first()
    if not user:
        raise EmployeeError("Employee not found.")
    return user


def create_employee(
    db: Session,
    name: str,
    email: str,
    password: str,
    role: str,
    department: str | None,
) -> User:
    if db.query(User).filter(User.email == email).first():
        raise EmployeeError("Email already in use.")
    try:
        user_role = UserRole(role)
    except ValueError:
        raise EmployeeError(f"Invalid role: {role}")

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


def update_employee(
    db: Session,
    employee_id: int,
    name: str | None = None,
    department: str | None = None,
    role: str | None = None,
) -> User:
    user = get_employee(db, employee_id)
    if name:
        user.name = name
    if department is not None:
        user.department = department
    if role:
        try:
            user.role = UserRole(role)
        except ValueError:
            raise EmployeeError(f"Invalid role: {role}")
    db.commit()
    db.refresh(user)
    return user


def deactivate_employee(db: Session, employee_id: int) -> None:
    user = get_employee(db, employee_id)
    user.is_active = 0
    db.commit()


def list_departments(db: Session) -> list[str]:
    rows = (
        db.query(User.department)
        .filter(User.is_active == 1, User.department.isnot(None))
        .distinct()
        .all()
    )
    return sorted({r[0] for r in rows if r[0]})
