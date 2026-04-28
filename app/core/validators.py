"""
app/core/validators.py
─────────────────────
Reusable input validation helpers for FastAPI routes.

All functions raise ``HTTPException(400, ...)`` on failure so they
drop in naturally at the top of any route handler.
"""
from __future__ import annotations

import re

from fastapi import HTTPException

# Pre-compiled: matches strings that contain NO letters or digits at all.
# Examples that match (and should be rejected): "!!!", "@@@", "---", "   "
_ONLY_SPECIAL_RE = re.compile(r"^[^a-zA-Z0-9]+$")


def validate_text(
    value: str,
    field: str = "Field",
    max_len: int = 200,
    min_len: int = 1,
) -> str:
    """
    Strip, validate, and return the cleaned text value.

    Rules (in order):
      1. Strip surrounding whitespace.
      2. Reject if shorter than *min_len* characters (default: 1 → non-empty).
      3. Reject if longer than *max_len* characters.
      4. Reject if the string contains no letter or digit (e.g. ``"!!!@@@"``).

    Args:
        value:   Raw input string from the form/request.
        field:   Human-readable field label used in error messages.
        max_len: Maximum allowed length after stripping (default 200).
        min_len: Minimum required length after stripping (default 1).

    Returns:
        The stripped, validated string.

    Raises:
        HTTPException: 400 with a descriptive message on any violation.
    """
    v = value.strip()

    if len(v) < min_len:
        raise HTTPException(
            status_code=400,
            detail=f"{field} cannot be empty." if min_len == 1 else f"{field} is too short (min {min_len} characters).",
        )
    if len(v) > max_len:
        raise HTTPException(
            status_code=400,
            detail=f"{field} is too long (max {max_len} characters).",
        )
    if _ONLY_SPECIAL_RE.match(v):
        raise HTTPException(
            status_code=400,
            detail=f"{field} must contain at least one letter or digit.",
        )
    return v
