"""The refresh pipeline: collect → enrich → identify → history → insights.

:class:`Engine` owns every collector and is the single object the TUI and
the CLI talk to.  ``refresh()`` is safe to call repeatedly; blocking work is
pushed to a worker thread so the UI stays responsive.
"""

from __future__ import annotations

from lirts.engine.core import Engine

__all__ = ["Engine"]
