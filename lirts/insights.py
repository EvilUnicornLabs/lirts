"""Anomaly detection, action suggestions, proxy chains, blast radius
and the "explain my machine" summary.  Pure functions over :class:`Snapshot`.

The implementation lives in :mod:`lirts.insights_analyse`, :mod:`lirts.insights_events`
and :mod:`lirts.insights_explain`; this module re-exports it so ``lirts.insights``
stays the single import path.
"""

from __future__ import annotations

from lirts.insights_analyse import (
    PROXY_ROLES,
    WEB_ROLES,
    analyse,
    build_chain,
    classify_activity,
    detect_proxies,
)
from lirts.insights_events import (
    blast_radius,
    event_insights,
)
from lirts.insights_explain import (
    explain,
    explain_sections,
    format_duration,
    format_rate,
    infer_architecture,
    render_graph_text,
    spark,
)

__all__ = [
    "PROXY_ROLES",
    "WEB_ROLES",
    "analyse",
    "blast_radius",
    "build_chain",
    "classify_activity",
    "detect_proxies",
    "event_insights",
    "explain",
    "explain_sections",
    "format_duration",
    "format_rate",
    "infer_architecture",
    "render_graph_text",
    "spark",
]
