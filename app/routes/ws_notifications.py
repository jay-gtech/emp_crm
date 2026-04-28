"""
ws_notifications.py — WebSocket real-time notification push.

Architecture
────────────
• One WebSocket endpoint per user: /ws/notifications/{user_id}
• Connection registry lives in this module as a process-level dict.
• push_notification() is called from create_notification() via
  asyncio.get_running_loop().create_task() — fire-and-forget, never blocks.
• Polling (pollUnread every 60 s) is kept as a fallback.
• Handles reconnects transparently — dead sockets are pruned on write failure.
"""
from __future__ import annotations

import logging
from collections import defaultdict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])

# ── Connection registry ────────────────────────────────────────────────────────
# user_id → list of open WebSocket connections (supports multiple tabs/devices)
_connections: dict[int, list[WebSocket]] = defaultdict(list)


# ── WebSocket endpoint ─────────────────────────────────────────────────────────

@router.websocket("/ws/notifications/{user_id}")
async def ws_notifications(websocket: WebSocket, user_id: int) -> None:
    """
    Accept a WebSocket connection for *user_id*.
    Keeps the connection alive by reading (and discarding) any client pings.
    Cleans up from the registry on disconnect or error.
    """
    await websocket.accept()
    _connections[user_id].append(websocket)
    logger.debug("[ws] user_id=%s connected (%d open)", user_id, len(_connections[user_id]))

    try:
        while True:
            # Drain client messages (keep-alive pings); we never use the payload.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        try:
            _connections[user_id].remove(websocket)
        except ValueError:
            pass
        logger.debug(
            "[ws] user_id=%s disconnected (%d remaining)",
            user_id, len(_connections[user_id]),
        )


# ── Push helper ────────────────────────────────────────────────────────────────

async def push_notification(user_id: int, payload: dict) -> None:
    """
    Send *payload* as JSON to every open connection for *user_id*.
    Dead sockets are silently removed from the registry.
    This is a coroutine — callers must either await it or schedule it
    via loop.create_task() for fire-and-forget.
    """
    sockets = list(_connections.get(user_id, []))
    if not sockets:
        return

    dead: list[WebSocket] = []
    for ws in sockets:
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)

    for ws in dead:
        try:
            _connections[user_id].remove(ws)
        except ValueError:
            pass

    if dead:
        logger.debug("[ws] pruned %d dead socket(s) for user_id=%s", len(dead), user_id)
