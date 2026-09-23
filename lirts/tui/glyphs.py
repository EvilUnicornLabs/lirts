"""Glyph sets: the characters the dashboard draws sparklines, bars and row icons with.

``block`` is what lirts has always drawn (the ramps in :mod:`lirts.constants`); ``braille``
and ``demoscene`` are the alternatives the ``ui.glyphs`` setting and the layout presets pick
between.

The active set is module state on purpose.  It is the one place the look is switched
(:func:`set_active`, called by the app at start-up and when the setting changes), so every
renderer -- the table cells, the top bar, the traffic graph -- reads :func:`active` instead of
taking a glyph table through every call.  The star map keeps its own ``NODE_GLYPHS``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from lirts.constants import (
    BAR_EMPTY,
    BAR_FULL,
    GLYPH_ERROR,
    GLYPH_OK,
    GLYPH_SSH,
    GLYPH_WARNING,
    PARTIAL_BLOCKS,
    SPARK_CHARS,
)
from lirts.identity_rules import Role

# Icons per identity role and per star-map kind; the same table for every set, because the
# meaning is the shape, not the texture.
ROLE_ICONS: Mapping[str, str] = MappingProxyType(
    {
        Role.FRONTEND: "○",
        Role.BACKEND: "●",
        Role.DB: "◆",
        Role.CACHE: "◆",
        Role.QUEUE: "◆",
        Role.PROXY: "⬢",
        Role.DOCKER: "▫",
        Role.TOOL: "▫",
        Role.SYSTEM: "·",
        Role.TUNNEL: "⇄",
        Role.SSH: GLYPH_SSH,
        Role.UNKNOWN: "·",
        # Star-map kinds that are not identity roles.
        "host": "⌂",
        "cluster": "☁",
        "service": "▫",
        "machine": "⌂",
    }
)

SOURCE_ICONS: Mapping[str, str] = MappingProxyType(
    {"local": "⌂", "docker": "▣", "kubernetes": "☸", "ssh": GLYPH_SSH}
)

STATUS_GLYPHS: Mapping[str, str] = MappingProxyType(
    {"ok": GLYPH_OK, "warning": GLYPH_WARNING, "error": GLYPH_ERROR}
)


@dataclass(frozen=True, slots=True)
class GlyphSet:
    """Every character the shared renderers draw, as one named bundle."""

    name: str
    spark: str
    partial_blocks: str
    bar_full: str
    bar_empty: str
    role_icons: Mapping[str, str] = ROLE_ICONS
    source_icons: Mapping[str, str] = SOURCE_ICONS
    status_glyphs: Mapping[str, str] = STATUS_GLYPHS

    def role_icon(self, role: str) -> str:
        """The icon of an identity role or a star-map kind; a dot for an unknown one."""
        return self.role_icons.get(role, self.role_icons[Role.UNKNOWN])

    def source_icon(self, source: str) -> str:
        """The icon of a row's source (local, docker, kubernetes, ssh)."""
        return self.source_icons.get(source, self.source_icons["local"])

    def status_glyph(self, name: str) -> str:
        """The ok / warning / error glyph."""
        return self.status_glyphs.get(name, self.status_glyphs["ok"])


BLOCK = GlyphSet(
    name="block",
    spark=SPARK_CHARS,
    partial_blocks=PARTIAL_BLOCKS,
    bar_full=BAR_FULL,
    bar_empty=BAR_EMPTY,
)

# Braille dot rows fill from the bottom up, which gives a finer ramp than the block one on
# terminals whose block glyphs are drawn with gaps.
_BRAILLE_SPARK = " ⢀⣀⣄⣤⣦⣶⣷⣿"
BRAILLE = GlyphSet(
    name="braille",
    spark=_BRAILLE_SPARK,
    partial_blocks=_BRAILLE_SPARK[:-1],
    bar_full="━",
    bar_empty="─",
)

_DEMOSCENE_SPARK = " ░░▒▒▓▓██"
DEMOSCENE = GlyphSet(
    name="demoscene",
    spark=_DEMOSCENE_SPARK,
    partial_blocks=_DEMOSCENE_SPARK[:-1],
    bar_full="█",
    bar_empty="▒",
)

GLYPH_SETS: Mapping[str, GlyphSet] = MappingProxyType(
    {s.name: s for s in (BLOCK, BRAILLE, DEMOSCENE)}
)

DEFAULT_GLYPH_SET = BLOCK.name

_active: GlyphSet = BLOCK


def active() -> GlyphSet:
    """The glyph set every renderer draws with right now."""
    return _active


def set_active(name: str) -> GlyphSet:
    """Switch the look to a named glyph set, falling back to ``block`` for an unknown name."""
    global _active
    _active = GLYPH_SETS.get(name, BLOCK)
    return _active
