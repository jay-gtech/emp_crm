"""
chat_service.py — Group chat business logic.

DM (1-to-1) operations are handled directly in the route layer (preserved as-is).
This service covers group creation, membership, group message history, and
the in-memory WebSocket connection manager.
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

from fastapi import WebSocket
from sqlalchemy.orm import Session

from app.models.chat_group import ChatGroup, ChatGroupMember
from app.models.message import Message
from app.models.user import User

logger = logging.getLogger(__name__)

# ── Upload directory ────────────────────────────────────────────────────────────
CHAT_UPLOAD_DIR = "app/static/uploads/chat"
os.makedirs(CHAT_UPLOAD_DIR, exist_ok=True)

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf", ".doc", ".docx"}
MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MB


# ── WebSocket Connection Manager ────────────────────────────────────────────────

class ConnectionManager:
    """Manages active WebSocket connections keyed by group_id."""

    def __init__(self) -> None:
        # group_id -> list of (user_id, websocket)
        self._connections: dict[int, list[tuple[int, WebSocket]]] = {}

    def _group(self, group_id: int) -> list[tuple[int, WebSocket]]:
        return self._connections.setdefault(group_id, [])

    async def connect(self, group_id: int, user_id: int, ws: WebSocket) -> None:
        await ws.accept()
        self._group(group_id).append((user_id, ws))
        logger.debug("[ws] user %s joined group %s", user_id, group_id)

    def disconnect(self, group_id: int, ws: WebSocket) -> None:
        conns = self._connections.get(group_id, [])
        self._connections[group_id] = [(uid, w) for uid, w in conns if w is not ws]

    async def broadcast(self, group_id: int, payload: dict) -> None:
        """Send JSON payload to all connected members of a group."""
        dead: list[WebSocket] = []
        for _, ws in list(self._group(group_id)):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(group_id, ws)

    def online_count(self, group_id: int) -> int:
        return len(self._connections.get(group_id, []))


# Singleton shared across the app
manager = ConnectionManager()


# ── Group CRUD ──────────────────────────────────────────────────────────────────

def create_group(db: Session, name: str, creator_id: int, member_ids: list[int]) -> ChatGroup:
    """Create a chat group and add the creator + listed members."""
    name = name.strip()
    if not name:
        raise ValueError("Group name cannot be empty")

    group = ChatGroup(name=name, created_by=creator_id)
    db.add(group)
    db.flush()  # get group.id

    # Deduplicate; always include creator
    all_ids: set[int] = set(member_ids) | {creator_id}
    # Validate user IDs exist
    existing = {u.id for u in db.query(User.id).filter(User.id.in_(all_ids)).all()}  # type: ignore[attr-defined]
    members = [ChatGroupMember(group_id=group.id, user_id=uid) for uid in existing]
    db.bulk_save_objects(members)
    db.commit()
    db.refresh(group)
    return group


def get_my_groups(db: Session, user_id: int) -> list[dict]:
    """Return all groups the user belongs to, enriched with member count."""
    memberships = (
        db.query(ChatGroupMember.group_id)
        .filter(ChatGroupMember.user_id == user_id)
        .all()
    )
    group_ids = [m.group_id for m in memberships]
    if not group_ids:
        return []

    groups = db.query(ChatGroup).filter(ChatGroup.id.in_(group_ids)).order_by(ChatGroup.created_at).all()

    # Member counts in one query
    from sqlalchemy import func
    counts_q = (
        db.query(ChatGroupMember.group_id, func.count(ChatGroupMember.id).label("cnt"))
        .filter(ChatGroupMember.group_id.in_(group_ids))
        .group_by(ChatGroupMember.group_id)
        .all()
    )
    count_map = {row.group_id: row.cnt for row in counts_q}

    result = []
    for g in groups:
        result.append({
            "id": g.id,
            "name": g.name,
            "created_by": g.created_by,
            "created_at": g.created_at,
            "member_count": count_map.get(g.id, 0),
        })
    return result


def get_group_members(db: Session, group_id: int) -> list[dict]:
    """Return member list with user names for a group."""
    members = (
        db.query(ChatGroupMember)
        .filter(ChatGroupMember.group_id == group_id)
        .all()
    )
    if not members:
        return []
    uid_set = {m.user_id for m in members}
    users = {u.id: u for u in db.query(User).filter(User.id.in_(uid_set)).all()}
    return [
        {"user_id": m.user_id, "name": users[m.user_id].name if m.user_id in users else "Unknown"}
        for m in members
    ]


def is_group_member(db: Session, group_id: int, user_id: int) -> bool:
    return (
        db.query(ChatGroupMember)
        .filter(ChatGroupMember.group_id == group_id, ChatGroupMember.user_id == user_id)
        .first()
    ) is not None


def add_members(db: Session, group_id: int, user_ids: list[int], requester_id: int) -> int:
    """Add new members to an existing group. Returns count added."""
    group = db.query(ChatGroup).filter(ChatGroup.id == group_id).first()
    if not group:
        raise ValueError("Group not found")
    if group.created_by != requester_id:
        raise PermissionError("Only the group creator can add members")

    existing = {
        m.user_id
        for m in db.query(ChatGroupMember.user_id)
        .filter(ChatGroupMember.group_id == group_id)
        .all()
    }
    new_ids = set(user_ids) - existing
    valid = {u.id for u in db.query(User.id).filter(User.id.in_(new_ids)).all()}  # type: ignore[attr-defined]
    new_members = [ChatGroupMember(group_id=group_id, user_id=uid) for uid in valid]
    if new_members:
        db.bulk_save_objects(new_members)
        db.commit()
    return len(new_members)


# ── Group message history ────────────────────────────────────────────────────────

def get_group_history(db: Session, group_id: int, limit: int = 100) -> list[dict]:
    """Return the most-recent messages for a group, oldest-first, with sender names."""
    msgs = (
        db.query(Message)
        .filter(Message.group_id == group_id)
        .order_by(Message.timestamp.desc())
        .limit(limit)
        .all()
    )
    msgs = list(reversed(msgs))

    uid_set = {m.sender_id for m in msgs}
    users = {u.id: u.name for u in db.query(User).filter(User.id.in_(uid_set)).all()}

    return [
        {
            "id": m.id,
            "sender_id": m.sender_id,
            "sender_name": users.get(m.sender_id, "Unknown"),
            "content": m.content,
            "file_url": m.file_url,
            "timestamp": m.timestamp.strftime("%H:%M") if m.timestamp else "",
        }
        for m in msgs
    ]


def save_group_message(
    db: Session,
    group_id: int,
    sender_id: int,
    content: str,
    file_url: Optional[str] = None,
) -> dict:
    """Persist a group message and return a serialised dict."""
    msg = Message(
        sender_id=sender_id,
        group_id=group_id,
        content=content,
        file_url=file_url,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)

    sender = db.query(User).filter(User.id == sender_id).first()
    return {
        "id": msg.id,
        "sender_id": sender_id,
        "sender_name": sender.name if sender else "Unknown",
        "content": content,
        "file_url": file_url,
        "timestamp": msg.timestamp.strftime("%H:%M") if msg.timestamp else "",
    }


# ── File upload helper ───────────────────────────────────────────────────────────

async def save_uploaded_file(upload) -> Optional[str]:
    """Validate and persist an uploaded file. Returns relative URL or None."""
    if upload is None or not upload.filename:
        return None

    ext = os.path.splitext(upload.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"File type '{ext}' not allowed")

    data = await upload.read()
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("File exceeds 5 MB limit")

    filename = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(CHAT_UPLOAD_DIR, filename)
    with open(path, "wb") as f:
        f.write(data)

    return f"/static/uploads/chat/{filename}"
