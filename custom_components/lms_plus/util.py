"""Helpers for LMS command responses."""

from __future__ import annotations

from typing import Any


def lms_response_value(result: Any) -> Any | None:
    """Extract a scalar command value from PySqueezebox's parsed response."""
    if isinstance(result, (str, int, float)):
        return result
    if not isinstance(result, dict):
        return None

    for key in (
        "_p2",
        "_newvalue",
        "value",
        "valid",
        "sleep",
        "will_sleep_in",
        "_p1",
    ):
        value = result.get(key)
        if isinstance(value, (str, int, float)):
            return value

    for value in result.values():
        parsed = lms_response_value(value)
        if parsed is not None:
            return parsed
    return None
