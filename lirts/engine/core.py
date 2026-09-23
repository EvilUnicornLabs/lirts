"""The :class:`Engine` class itself: the mixins of this package in one object."""

from __future__ import annotations

from lirts.engine.actions import ActionsMixin
from lirts.engine.health import HealthMixin
from lirts.engine.pipeline import RefreshPipelineMixin


class Engine(RefreshPipelineMixin, HealthMixin, ActionsMixin):
    """Owns every collector and is the single object the TUI and the CLI talk to.

    ``refresh()`` is safe to call repeatedly; blocking work is pushed to a worker
    thread so the UI stays responsive.  The demo and replay engines subclass this
    and override the collection steps in
    :class:`lirts.engine.pipeline.RefreshPipelineMixin`.
    """
