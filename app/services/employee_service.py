from sqlalchemy.orm import Session
from app.models.user import User, UserRole
from app.core.auth import hash_password

# Import safely just in case
try:
    from app.services.hierarchy_service import apply_hierarchy_filter
except ImportError:
    apply_hierarchy_filter = None


# Which role must the parent be for each child role?
_ROLE_HIERARCHY: dict[str, str | None] = {
    "admin":          None,        # no parent required
    "manager":        "admin",
    "team_lead":      "manager",
    "employee":       "team_lead",
    "security_guard": None,        # no strict parent required
}


class EmployeeError(Exception):
    pass


def validate_reporting(role: str, parent_user: User | None) -> None:
    """
    Raise EmployeeError if `parent_user` is not the correct role for `role`.
    Does nothing for admin / security_guard (no parent required).
    """
    expected_parent_role = _ROLE_HIERARCHY.get(role)
    if expected_parent_role is None:
        return  # no parent required for this role

    if not parent_user:
        raise EmployeeError(
            f"A '{role}' must report to a '{expected_parent_role}'. "
            f"Please select a valid parent user."
        )

    actual_parent_role = (
        parent_user.role.value
        if hasattr(parent_user.role, "value")
        else str(parent_user.role)
    )
    if actual_parent_role != expected_parent_role:
        raise EmployeeError(
            f"A '{role}' must report to a '{expected_parent_role}', "
            f"but the selected user has role '{actual_parent_role}'."
        )


def list_employees(
    db: Session, 
    department: str | None = None, 
    request_user: dict | None = None,
    limit: int | None = None,
    offset: int = 0
) -> list[User]:
    q = db.query(User).filter(User.is_active == 1)
    if department:
        q = q.filter(User.department == department)
    
    q = q.order_by(User.name)

    if offset > 0:
        q = q.offset(offset)
    if limit is not None:
        q = q.limit(limit)
        
    result = q.all()
    
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
    reports_to_id: int | None = None,
) -> User:
    if db.query(User).filter(User.email == email).first():
        raise EmployeeError("Email already in use.")
    try:
        user_role = UserRole(role)
    except ValueError:
        raise EmployeeError(f"Invalid role: {role}")

    # Resolve and validate the parent user
    parent_user: User | None = None
    if reports_to_id:
        parent_user = db.query(User).filter(User.id == reports_to_id, User.is_active == 1).first()
        if reports_to_id and not parent_user:
            raise EmployeeError("Selected parent user not found or is inactive.")

    validate_reporting(role, parent_user)

    # Map the parent to the correct FK column based on role
    manager_id: int | None = None
    team_lead_id: int | None = None
    if parent_user:
        if role == "team_lead":
            manager_id = parent_user.id       # TL reports to Manager
        elif role == "employee":
            team_lead_id = parent_user.id     # Employee reports to Team Lead
        elif role == "manager":
            manager_id = parent_user.id       # Manager reports to Admin (stored for reference)

    user = User(
        name=name,
        email=email,
        hashed_password=hash_password(password),
        role=user_role,
        department=department,
        manager_id=manager_id,
        team_lead_id=team_lead_id,
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
