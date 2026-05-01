"""
app/core/rbac.py
─────────────────
Centralized Role-Based Access Control helpers.

Usage::

    from app.core.rbac import has_permission, can_act_on_roles, ROLE_LEVEL, PERMISSIONS
"""

ROLE_LEVEL: dict[str, int] = {
    "admin": 5,
    "manager": 4,
    "team_lead": 3,
    "employee": 2,
    "security_guard": 1,
}

PERMISSIONS: dict[str, list[str]] = {
    "assign_task":    ["admin", "manager", "team_lead"],
    "approve_leave":  ["admin", "manager", "team_lead"],
    "create_meeting": ["admin", "manager", "team_lead"],
    "view_team_data": ["admin", "manager", "team_lead"],
    "submit_leave":   ["admin", "manager", "team_lead", "employee", "security_guard"],
}


def has_permission(user_role: str, action: str) -> bool:
    """Return True if user_role is allowed to perform action."""
    return user_role in PERMISSIONS.get(action, [])


def can_act_on_roles(
    current_role: str,
    current_id: int,
    target_role: str,
    target_id: int,
) -> bool:
    """
    Return True if the current user may act on the target user.

    Rules:
      - A user cannot act on themselves (same id → False).
      - The actor must have a strictly higher ROLE_LEVEL than the target.
    """
    if current_id == target_id:
        return False
    return ROLE_LEVEL.get(current_role, 0) > ROLE_LEVEL.get(target_role, 0)
