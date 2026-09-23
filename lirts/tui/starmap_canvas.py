"""A character canvas with braille lines: 2 by 4 dots per cell for smooth diagonals.

Lines are drawn in dot space, text (glyphs, labels) is laid over whole cells and wins over
dots.  Each cell remembers the style of the last line through it, so a highlighted edge
keeps its colour where it crosses a dim one.
"""

from __future__ import annotations

from rich.text import Text

BRAILLE_BASE = 0x2800
# Bit of each dot: column 0 rows 0-3, column 1 rows 0-3.
_DOT_BITS = ((0x01, 0x02, 0x04, 0x40), (0x08, 0x10, 0x20, 0x80))


class BrailleCanvas:
    """A ``width`` by ``height`` grid of cells, each holding 2 by 4 braille dots or a character."""

    def __init__(self, width: int, height: int) -> None:
        self.width = max(0, width)
        self.height = max(0, height)
        self._dots = [[0] * self.width for _ in range(self.height)]
        self._dot_style: dict[tuple[int, int], str] = {}
        self._text: dict[tuple[int, int], tuple[str, str]] = {}

    def _set_dot(self, dx: int, dy: int, style: str) -> None:
        cx, cy = dx // 2, dy // 4
        if 0 <= cx < self.width and 0 <= cy < self.height:
            self._dots[cy][cx] |= _DOT_BITS[dx % 2][dy % 4]
            self._dot_style[(cx, cy)] = style

    def line(self, x0: float, y0: float, x1: float, y1: float, *, style: str = "") -> None:
        """A line between two cell positions (floats allowed), in dot resolution."""
        ax, ay = round(x0 * 2 + 1), round(y0 * 4 + 2)
        bx, by = round(x1 * 2 + 1), round(y1 * 4 + 2)
        dx, dy = abs(bx - ax), -abs(by - ay)
        sx, sy = (1 if ax < bx else -1), (1 if ay < by else -1)
        err = dx + dy
        while True:
            self._set_dot(ax, ay, style)
            if ax == bx and ay == by:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                ax += sx
            if e2 <= dx:
                err += dx
                ay += sy

    def put(self, x: int, y: int, text: str, *, style: str = "") -> None:
        """Write ``text`` over the cells starting at (x, y); off-canvas parts are dropped."""
        for i, ch in enumerate(text):
            cx = x + i
            if 0 <= cx < self.width and 0 <= y < self.height:
                self._text[(cx, y)] = (ch, style)

    def fits(self, x: int, y: int, length: int) -> bool:
        """True when ``length`` cells from (x, y) are inside the canvas and free of text."""
        if y < 0 or y >= self.height or x < 0 or x + length > self.width:
            return False
        return all((x + i, y) not in self._text for i in range(length))

    def free_cells(self, x: int, y: int, *, left: bool) -> int:
        """How many cells from (x, y) outwards (left or right) are on the canvas and free."""
        if y < 0 or y >= self.height:
            return 0
        count, cx = 0, x
        while 0 <= cx < self.width and (cx, y) not in self._text:
            count += 1
            cx += -1 if left else 1
        return count

    def render(self) -> list[Text]:
        """One rich Text per row."""
        rows: list[Text] = []
        for y in range(self.height):
            row = Text()
            for x in range(self.width):
                over = self._text.get((x, y))
                if over is not None:
                    row.append(over[0], style=over[1])
                    continue
                bits = self._dots[y][x]
                if bits:
                    row.append(chr(BRAILLE_BASE + bits), style=self._dot_style.get((x, y), ""))
                else:
                    row.append(" ")
            rows.append(row)
        return rows
