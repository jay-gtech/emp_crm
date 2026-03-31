from sqlalchemy.orm import Session
from app.models.user import User


def get_manager_team(db: Session, manager_id: int):
    try:
        return db.query(User).filter(User.manager_id == manager_id).all()
    except Exception:
        return []


def get_team_lead_members(db: Session, team_lead_id: int):
    try:
        return db.query(User).filter(User.team_lead_id == team_lead_id).all()
    except Exception:
        return []


def _check_scope(request_user: dict, target_user: User) -> bool:
    """Internal 2-arg scope check against a resolved User ORM object."""
    try:
        role = request_user.get("role")
        req_id = request_user.get("user_id")

        if role == "admin":
            return True
        if req_id == target_user.id:
            return True
        if role == "manager" and target_user.manager_id == req_id:
            return True
        if role == "team_lead" and target_user.team_lead_id == req_id:
            return True
        return False
    except Exception:
        return True  # Failsafe: don't block on error


def is_user_in_scope(db: Session, request_user: dict, target_user_id: int) -> bool:
    """
    Returns True if request_user is allowed to see/act on target_user_id.
    Signature: (db, request_user, target_user_id)
    Fails open (returns True) on any exception to avoid blocking legitimate flows.
    """
    try:
        target = db.query(User).filter(User.id == target_user_id).first()
        if not target:
            return True  # unknown user — don't block
        return _check_scope(request_user, target)
    except Exception:
        return True


def apply_hierarchy_filter(db: Session, request_user: dict, data_list: list) -> list:
    """
    Filters a list of ORM objects or dicts to those in request_user's scope.
    Returns unfiltered list on any exception.
    """
    if not request_user:
        return data_list

    # Admins see everything — skip per-item DB lookups
    if request_user.get("role") == "admin":
        return data_list

    try:
        filtered = []
        for item in data_list:
            # Determine target user ID based on object type
            target_id = None
            if hasattr(item, "assigned_to"):
                target_id = item.assigned_to
            elif hasattr(item, "employee_id"):
                target_id = item.employee_id
            elif isinstance(item, dict) and "id" in item:
                target_id = item["id"]
            elif hasattr(item, "id"):
                target_id = item.id

            if target_id is None:
                filtered.append(item)
                continue

            target_user = db.query(User).filter(User.id == target_id).first()
            if not target_user:
                filtered.append(item)
                continue

            if _check_scope(request_user, target_user):
                filtered.append(item)

        return filtered
    except Exception:
        return data_list
