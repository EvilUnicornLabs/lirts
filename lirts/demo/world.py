"""The demo world: state, timeline and views in one object."""

from __future__ import annotations

from lirts.demo.timeline import DemoTimelineMixin
from lirts.demo.views import DemoViewsMixin


class DemoWorld(DemoTimelineMixin, DemoViewsMixin):
    """State of the pretend machine plus the timeline that changes it."""
