"""The text beside a star: shortened to fit before it is ever dropped.

A crowded map used to lose labels: if the full text did not fit on the star's row or the two
rows next to it, nothing was written.  Now the text gives up its port first, then the service
name shrinks with an ellipsis to the free cells on that side (never below
:data:`LABEL_MIN_CHARS` characters), and only a star with no room at all stays unlabelled --
its name is still in the panel.
"""

from __future__ import annotations

from lirts.topology import Node
from lirts.tui.starmap_canvas import BrailleCanvas
from lirts.tui.starmap_layout import Placement

# Longest label written next to a star, before any shortening.
LABEL_MAX_CHARS = 18
# A shortened name below this is unreadable, so the label is dropped instead.
LABEL_MIN_CHARS = 6
ELLIPSIS = "…"
# The glyph's own cell plus one blank, between the star and its label.
LABEL_OFFSET = 2
# Rows tried, in order: the star's own row, then the one below, then the one above.
LABEL_ROWS = (0, 1, -1)


def label_candidates(node: Node, *, free: int) -> list[str]:
    """The texts to try for ``node``, longest first, given ``free`` cells to write in."""
    name = node.label[:LABEL_MAX_CHARS]
    texts = []
    if node.ports:
        texts.append(f"{name} {node.port_text.split(',')[0]}")
    texts.append(name)
    room = min(len(name) - 1, free)
    if free < len(name) and room >= LABEL_MIN_CHARS:
        texts.append(name[: room - 1] + ELLIPSIS)
    return texts


def put_label(
    canvas: BrailleCanvas, place: Placement, node: Node, *, style: str, left: bool
) -> str | None:
    """Write the label beside the glyph, away from the centre; the text used, or None."""
    anchor = place.x - LABEL_OFFSET if left else place.x + LABEL_OFFSET
    for dy in LABEL_ROWS:
        y = place.y + dy
        row_style = style if dy == 0 else f"{style} dim"
        for text in label_candidates(node, free=canvas.free_cells(anchor, y, left=left)):
            x = anchor - len(text) + 1 if left else anchor
            if canvas.fits(x, y, len(text)):
                canvas.put(x, y, text, style=row_style)
                return text
    return None
