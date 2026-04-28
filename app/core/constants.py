"""
app/core/constants.py
─────────────────────
Application-wide constants — single source of truth for limits,
thresholds, and other hardcoded values.

Usage::

    from app.core.constants import MAX_TITLE_LENGTH, MAX_EXPENSE_AMOUNT

DO NOT put secrets or environment-dependent values here; those belong
in ``app/core/config.py`` (which reads from env vars).
"""

# ── Text field length limits ──────────────────────────────────────────────────

MAX_TITLE_LENGTH:   int = 200   # task title, meeting title, announcement title
MAX_NAME_LENGTH:    int = 100   # employee name, visitor name
MAX_COMMENT_LENGTH: int = 1_000 # task comment body
MAX_PURPOSE_LENGTH: int = 500   # visitor purpose

# ── Expense limits ────────────────────────────────────────────────────────────

MAX_EXPENSE_AMOUNT: float = 1_000_000.00  # ₹10 lakh cap per expense group

# ── Task assignment limits ────────────────────────────────────────────────────

MAX_BATCH_ASSIGN: int = 20  # max employees selectable in a single multi-assign action

# ── Notification ──────────────────────────────────────────────────────────────

NOTIFICATION_PREVIEW_LEN: int = 80   # chars of comment/message shown in notification
DM_PREVIEW_LEN:           int = 60   # chars of DM content shown in notification

# ── Dashboard / performance thresholds ───────────────────────────────────────

LOW_HOURS_THRESHOLD:    float = 20.0  # hours/week below which employee is flagged
LOW_TASK_RATE:          int   = 40    # % task completion rate below which flagged
PERFORMANCE_HOURS_CAP:  float = 40.0  # denominator for hours component of score
LATE_THRESHOLD_HOUR:    int   = 9     # clock-ins after 09:30 are considered late
LATE_THRESHOLD_MINUTE:  int   = 30
BREAK_ALERT_HOURS:      float = 1.0   # break > 60 min triggers personal alert
NOT_CLOCKED_IN_ALERT_HOUR: int = 10   # "not clocked in" alert fires only after 10 AM
