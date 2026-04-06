from datetime import date as _date
from sqlalchemy.orm import Session
from app.models.user import User, UserRole


def get_manager_team(db: Session, manager_id: int):
    """
    Manager sees team leads + their employees strictly via hierarchy.
    """
    try:
        team_leads = db.query(User).filter(User.manager_id == manager_id).all()
        if not team_leads:
            return []
        tl_ids = [tl.id for tl in team_leads]
        employees = db.query(User).filter(User.team_lead_id.in_(tl_ids)).all()
        return team_leads + employees
    except Exception:
        return []


def get_team_lead_members(db: Session, team_lead_id: int):
    """
    Team Lead sees ONLY their direct employees.
    """
    try:
        return db.query(User).filter(User.team_lead_id == team_lead_id).all()
    except Exception:
        return []


def _check_scope(request_user: dict, target_user: User) -> bool:
    """
    Internal scope check against a resolved User ORM object.
    NOTE: Cannot resolve the manager→TL→employee transitive chain without DB access.
    Use is_user_in_scope() for full transitive checking.
    """
    try:
        role   = request_user.get("role")
        req_id = request_user.get("user_id")

        if role == "admin":
            return True
        if req_id == target_user.id:
            return True
        if role == "manager":
            # Direct report: team lead whose manager_id == this manager
            if target_user.manager_id == req_id:
                return True
            # Transitive (employee under one of this manager's team leads) requires
            # a DB call — handled in is_user_in_scope() instead.
            return False
        if role == "team_lead":
            return target_user.team_lead_id == req_id
        return False
    except Exception:
        return False  # Fail-closed: deny on error rather than grant

def is_user_in_scope(db: Session, request_user: dict, target_user_id: int) -> bool:
    """
    Returns True if request_user is allowed to see/act on target_user_id.

    Handles the full transitive hierarchy:
      Admin    → always True
      Manager  → True for their direct team-leads AND those team-leads' employees
      Team Lead → True for their direct employees only
      Employee  → True only for themselves

    Fails closed (returns False) on any exception to prevent unauthorised access.
    """
    try:
        target = db.query(User).filter(User.id == target_user_id).first()
        if not target:
            return False  # unknown user — deny access (fail-closed)

        role   = request_user.get("role")
        req_id = request_user.get("user_id")

        if role == "admin":
            return True
        if req_id == target.id:
            return True

        if role == "manager":
            # Direct team lead
            if target.manager_id == req_id:
                return True
            # Transitive: employee whose team lead belongs to this manager
            if target.team_lead_id:
                tl = db.query(User).filter(User.id == target.team_lead_id).first()
                if tl and tl.manager_id == req_id:
                    return True
            return False

        if role == "team_lead":
            return target.team_lead_id == req_id

        return False
    except Exception:
        return False  # Fail-closed: deny on error rather than grant


def get_full_hierarchy(db: Session) -> list:
    """
    Returns the full org hierarchy for admin view.

    Structure:
      [
        {
          "id": int, "name": str, "department": str,
          "team_leads": [
            {
              "id": int, "name": str, "department": str,
              "employees": [{"id": int, "name": str, "department": str}, ...]
            }
          ]
        }, ...
      ]
    """
    try:
        managers = db.query(User).filter(User.role == UserRole.manager, User.is_active == 1).all()
        hierarchy = []
        for mgr in managers:
            team_leads = (
                db.query(User)
                .filter(User.manager_id == mgr.id, User.role == UserRole.team_lead, User.is_active == 1)
                .all()
            )
            tl_list = []
            for tl in team_leads:
                emps = (
                    db.query(User)
                    .filter(User.team_lead_id == tl.id, User.role == UserRole.employee, User.is_active == 1)
                    .all()
                )
                tl_list.append({
                    "id": tl.id,
                    "name": tl.name,
                    "department": tl.department or "",
                    "employees": [
                        {"id": e.id, "name": e.name, "department": e.department or ""}
                        for e in emps
                    ],
                })
            hierarchy.append({
                "id": mgr.id,
                "name": mgr.name,
                "department": mgr.department or "",
                "team_leads": tl_list,
            })
        return hierarchy
    except Exception:
        return []


def get_org_attendance_today(db: Session) -> dict:
    """
    Returns org-wide attendance for today, used by admin attendance view.
    Each row contains user info + today's attendance record.
    """
    try:
        from app.models.attendance import Attendance
        today = _date.today()
        records = (
            db.query(Attendance, User)
            .join(User, Attendance.employee_id == User.id)
            .filter(Attendance.date == today)
            .order_by(User.role, User.name)
            .all()
        )
        rows = [
            {
                "employee_name": u.name,
                "role": u.role.value,
                "department": u.department or "",
                "clock_in": a.clock_in_time,
                "clock_out": a.clock_out_time,
                "total_hours": a.total_hours,
                "work_mode": a.work_mode.value if a.work_mode else "",
                "total_break_hours": a.total_break_hours or 0,
            }
            for a, u in records
        ]
        summary = {
            "total_clocked_in": len(rows),
            "office": sum(1 for r in rows if r["work_mode"] == "office"),
            "remote": sum(1 for r in rows if r["work_mode"] == "remote"),
            "clocked_out": sum(1 for r in rows if r["clock_out"] is not None),
        }
        return {"rows": rows, "summary": summary}
    except Exception:
        return {"rows": [], "summary": {"total_clocked_in": 0, "office": 0, "remote": 0, "clocked_out": 0}}


def get_team_lead_team_attendance_today(db: Session, tl_id: int) -> dict:
    """
    Returns today's attendance for all employees directly under a Team Lead.

    Structure:
      {
        "rows": [
          {
            "name": str, "department": str,
            "clock_in": time|None, "clock_out": time|None,
            "total_hours": float|None, "work_mode": str,
            "total_break_hours": float, "clocked_in": bool,
          }, ...
        ],
        "summary": {"total": int, "clocked_in": int, "clocked_out": int, "absent": int}
      }
    """
    try:
        from app.models.attendance import Attendance
        today = _date.today()

        employees = (
            db.query(User)
            .filter(
                User.team_lead_id == tl_id,
                User.role == UserRole.employee,
                User.is_active == 1,
            )
            .order_by(User.name)
            .all()
        )

        rows = []
        for emp in employees:
            att = (
                db.query(Attendance)
                .filter(Attendance.employee_id == emp.id, Attendance.date == today)
                .first()
            )
            rows.append({
                "name": emp.name,
                "department": emp.department or "",
                "clock_in": att.clock_in_time if att else None,
                "clock_out": att.clock_out_time if att else None,
                "total_hours": att.total_hours if att else None,
                "work_mode": att.work_mode.value if att and att.work_mode else "",
                "total_break_hours": att.total_break_hours or 0 if att else 0,
                "clocked_in": att is not None and att.clock_in_time is not None,
                "status": "Done" if (att and att.clock_out_time) else "Working" if (att and att.clock_in_time) else "Absent",
            })

        total = len(rows)
        clocked_in = sum(1 for r in rows if r["clocked_in"])
        clocked_out = sum(1 for r in rows if r["clock_out"] is not None)
        return {
            "rows": rows,
            "summary": {
                "total": total,
                "clocked_in": clocked_in,
                "clocked_out": clocked_out,
                "absent": total - clocked_in,
            },
        }
    except Exception:
        return {"rows": [], "summary": {"total": 0, "clocked_in": 0, "clocked_out": 0, "absent": 0}}


def get_manager_team_attendance_today(db: Session, manager_id: int) -> list:
    """
    Returns today's attendance for a manager's team, grouped by Team Lead.

    Structure:
      [
        {
          "team_lead_name": str,
          "team_lead_dept": str,
          "employees": [
            {
              "name": str, "department": str,
              "clock_in": time|None, "clock_out": time|None,
              "total_hours": float|None, "work_mode": str,
              "clocked_in": bool,
            }, ...
          ]
        }, ...
      ]
    """
    try:
        from app.models.attendance import Attendance
        today = _date.today()

        team_leads = (
            db.query(User)
            .filter(
                User.manager_id == manager_id,
                User.role == UserRole.team_lead,
                User.is_active == 1,
            )
            .order_by(User.name)
            .all()
        )

        result = []
        for tl in team_leads:
            employees = (
                db.query(User)
                .filter(
                    User.team_lead_id == tl.id,
                    User.role == UserRole.employee,
                    User.is_active == 1,
                )
                .order_by(User.name)
                .all()
            )

            emp_rows = []
            for emp in employees:
                att = (
                    db.query(Attendance)
                    .filter(Attendance.employee_id == emp.id, Attendance.date == today)
                    .first()
                )
                emp_rows.append({
                    "name": emp.name,
                    "department": emp.department or "",
                    "clock_in": att.clock_in_time if att else None,
                    "clock_out": att.clock_out_time if att else None,
                    "total_hours": att.total_hours if att else None,
                    "work_mode": att.work_mode.value if att and att.work_mode else "",
                    "clocked_in": att is not None and att.clock_in_time is not None,
                })

            result.append({
                "team_lead_name": tl.name,
                "team_lead_dept": tl.department or "",
                "employees": emp_rows,
            })

        return result
    except Exception:
        return []


def apply_hierarchy_filter(db: Session, request_user: dict, data_list: list) -> list:
    """
    Filters a list of ORM objects or dicts to those in request_user's scope.
    Uses is_user_in_scope() for full transitive hierarchy checking.
    Returns empty list on any exception (fail-closed — never leaks data).
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

            # Use is_user_in_scope for full transitive check (manager→TL→employee)
            if is_user_in_scope(db, request_user, target_id):
                filtered.append(item)

        return filtered
    except Exception:
        return []  # Fail-closed: never leak data on filter error
