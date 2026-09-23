"""Plain-text rendering of insights."""

from __future__ import annotations

from lirts.models import Insight


def insight_lines(insights: list[Insight]) -> list[str]:
    """One ``[level] message`` line per insight, for plain-text output."""
    return [f"[{i.level}] {i.message}" for i in insights]
