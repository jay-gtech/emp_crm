from __future__ import annotations

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.auth import login_required
from app.core.constants import MAX_TITLE_LENGTH
from app.core.database import get_db
from app.core.validators import validate_text as _validate_text
from app.models.meeting import Meeting, MeetingParticipant
from app.models.user import User
from app.services.hierarchy_service import is_user_in_scope

import logging

log = logging.getLogger(__name__)

# Audit trigger — imported defensively so meetings work even if audit table is missing
try:
    from app.services.audit_service import log_action as _audit
    _AUDIT_OK = True
except Exception:
    _AUDIT_OK = False

router = APIRouter(tags=["meetings"])
templates = Jinja2Templates(directory="app/templates")


def _validate_mtg_title(title: str) -> None:
    _validate_text(title, field="Meeting title", max_len=MAX_TITLE_LENGTH)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _participant_user_ids(db: Session, meeting: Meeting) -> set[int]:
    """
    Return the set of user IDs who are participants in a meeting.
    Checks both the new meeting_participants table AND the legacy participant_id
    column so existing meetings remain visible after the migration.
    """
    ids: set[int] = set()

    # New-style participants
    for mp in db.query(MeetingParticipant).filter(
        MeetingParticipant.meeting_id == meeting.id
    ).all():
        ids.add(mp.user_id)

    # Legacy single-participant fallback
    if meeting.participant_id:
        ids.add(meeting.participant_id)

    return ids


def _is_visible_to(db: Session, meeting: Meeting, user_id: int, creators: dict) -> bool:
    """Return True if *user_id* should see *meeting*."""
    # Creator always sees their own meetings
    if meeting.created_by == user_id:
        return True

    # Direct participant (new or legacy)
    if user_id in _participant_user_ids(db, meeting):
        return True

    # Hierarchy: manager/team_lead can see meetings they organised for their team
    creator = creators.get(meeting.created_by)
    if creator and creator.role.value in ("admin", "manager", "team_lead"):
        creator_dict = {"role": creator.role.value, "user_id": creator.id}
        if is_user_in_scope(db, creator_dict, user_id):
            return True

    return False


# ── Meeting list page ─────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def meetings_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    try:
        role    = current_user.get("role")
        user_id = current_user["user_id"]

        all_meetings = (
            db.query(Meeting)
            .order_by(Meeting.scheduled_time.desc())
            .all()
        )

        # Batch-load creators to avoid N+1
        creator_ids = {m.created_by for m in all_meetings}
        creators = (
            {u.id: u for u in db.query(User).filter(User.id.in_(creator_ids)).all()}
            if creator_ids else {}
        )

        if role == "admin":
            visible = all_meetings
        else:
            visible = [
                m for m in all_meetings
                if _is_visible_to(db, m, user_id, creators)
            ]

        # Build participant name map: {meeting_id: [user_name, ...]}
        participant_map: dict[int, list[str]] = {}
        if visible:
            meeting_ids = [m.id for m in visible]
            all_mps = (
                db.query(MeetingParticipant)
                .filter(MeetingParticipant.meeting_id.in_(meeting_ids))
                .all()
            )
            mp_user_ids = {mp.user_id for mp in all_mps}
            # Also collect legacy participant_ids
            legacy_ids = {m.participant_id for m in visible if m.participant_id}
            all_user_ids = mp_user_ids | legacy_ids

            user_name_map = (
                {u.id: u.name for u in db.query(User).filter(User.id.in_(all_user_ids)).all()}
                if all_user_ids else {}
            )

            for m in visible:
                names: list[str] = []
                # New-style
                for mp in all_mps:
                    if mp.meeting_id == m.id:
                        name = user_name_map.get(mp.user_id, f"#{mp.user_id}")
                        if name not in names:
                            names.append(name)
                # Legacy fallback (don't duplicate)
                if m.participant_id and not names:
                    name = user_name_map.get(m.participant_id, f"#{m.participant_id}")
                    names.append(name)

                participant_map[m.id] = names

        # Assignable users for the "schedule meeting" form
        filtered_users: list[User] = []
        if role == "admin":
            filtered_users = db.query(User).filter(User.is_active == 1).all()
        elif role == "manager":
            filtered_users = (
                db.query(User)
                .filter(
                    User.manager_id == user_id,
                    User.is_active == 1,
                )
                .all()
            )
        elif role == "team_lead":
            filtered_users = (
                db.query(User)
                .filter(
                    User.team_lead_id == user_id,
                    User.is_active == 1,
                )
                .all()
            )

    except Exception as exc:
        log.error("meetings_page failed: %s", exc, exc_info=True)
        visible, filtered_users, participant_map = [], [], {}

    return templates.TemplateResponse(
        "meetings/index.html",
        {
            "request":         request,
            "current_user":    current_user,
            "meetings":        visible,
            "filtered_users":  filtered_users,
            "participant_map": participant_map,
            "now":             datetime.now(),
        },
    )


# ── Create meeting ────────────────────────────────────────────────────────────

@router.post("/create")
def create_meeting(
    title:          str        = Form(...),
    description:    str        = Form(""),
    scheduled_time: str        = Form(...),
    participant_ids: List[int] = Form(default=[]),
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    role = current_user.get("role")
    if role not in ("admin", "manager", "team_lead"):
        raise HTTPException(status_code=403, detail="Not allowed")

    _validate_mtg_title(title)
    title = title.strip()

    # ── 107: minimum 2 participants required ──────────────────────────────────
    if not participant_ids or len(participant_ids) < 2:
        raise HTTPException(status_code=400, detail="At least 2 participants are required.")

    try:
        dt = datetime.fromisoformat(scheduled_time)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")

    if dt < datetime.now():
        raise HTTPException(
            status_code=400,
            detail="Meeting scheduled time cannot be in the past.",
        )

    # De-duplicate while preserving order
    seen: set[int] = set()
    unique_ids: list[int] = []
    for uid in participant_ids:
        if uid not in seen:
            seen.add(uid)
            unique_ids.append(uid)

    try:
        meeting = Meeting(
            title=title,
            description=description or None,
            scheduled_time=dt,
            created_by=current_user["user_id"],
            creator_role=role,
        )
        db.add(meeting)
        db.flush()  # get meeting.id

        for uid in unique_ids:
            db.add(MeetingParticipant(meeting_id=meeting.id, user_id=uid))

        db.commit()
    except Exception as exc:
        log.error("create_meeting failed: %s", exc, exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create meeting: {exc}")

    # Audit log — fire-and-forget
    if _AUDIT_OK:
        try:
            _audit(db, current_user["user_id"], "meeting_created", "meeting", meeting.id,
                   f'"{title}" with {len(unique_ids)} participant(s)')
        except Exception:
            pass

    # Notify each participant — fire-and-forget
    try:
        from app.services.notification_service import create_task_notification as _notify
        for uid in unique_ids:
            try:
                _notify(
                    db, uid,
                    f'📅 You have been invited to a meeting: "{title}"',
                    actor_id=current_user["user_id"],
                )
            except Exception:
                pass
    except Exception:
        pass

    return RedirectResponse(url="/meetings/", status_code=302)


# ── JSON list endpoint (kept for API consumers) ───────────────────────────────

@router.get("/list")
def get_meetings(
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    role    = current_user.get("role")
    user_id = current_user["user_id"]

    meetings = db.query(Meeting).order_by(Meeting.scheduled_time.desc()).all()
    if not meetings:
        return []

    if role == "admin":
        return meetings

    creator_ids = {m.created_by for m in meetings}
    creators = {u.id: u for u in db.query(User).filter(User.id.in_(creator_ids)).all()}

    return [m for m in meetings if _is_visible_to(db, m, user_id, creators)]
