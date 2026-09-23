"""The top bar: the clock, the densities and the cracktro scroller."""

from __future__ import annotations

import io
import re
from collections.abc import Iterator

import pytest
from rich.console import Console, RenderableType

from lirts.tui.app import LirtsApp
from lirts.tui.render import DEFAULT_CLOCK_FORMAT, set_clock_format
from lirts.tui.topbar import SCROLLER_WIDTH, TopBar, sine_scroller
from tests.conftest import wait_rows


@pytest.fixture(autouse=True)
def _default_clock() -> Iterator[None]:
    """The clock format is shared by every renderer; put it back after each test."""
    set_clock_format(DEFAULT_CLOCK_FORMAT)
    yield
    set_clock_format(DEFAULT_CLOCK_FORMAT)


def drawn(renderable: RenderableType) -> str:
    """What a Rich renderable prints, as plain text."""
    console = Console(width=200, record=True, file=io.StringIO())
    console.print(renderable)
    return console.export_text()


def test_sine_scroller_moves_one_cell_per_step_and_wraps() -> None:
    text = "ABCDEFGH"

    assert sine_scroller(text, width=4, offset=0).plain == "ABCD"
    assert sine_scroller(text, width=4, offset=1).plain == "BCDE"
    assert sine_scroller(text, width=4, offset=7).plain == "HABC"
    assert sine_scroller("", width=4, offset=0).plain == ""
    # The wave colours the window: neighbouring cells are not all one style.
    assert len({span.style for span in sine_scroller(text, width=8, offset=0).spans}) > 1


async def test_the_top_bar_draws_the_clock_in_the_configured_format(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        top = app.query_one("#top", TopBar)

        top.set_clock_format("%Y-%m-%d")
        await pilot.pause()
        as_date = drawn(top.content)

        assert re.search(r"\d{4}-\d{2}-\d{2}", as_date)
        assert not re.search(r"\d{2}:\d{2}:\d{2}", as_date)

        top.set_clock_format("%H:%M:%S")
        await pilot.pause()

        assert re.search(r"\d{2}:\d{2}:\d{2}", drawn(top.content))


async def test_the_scroller_advances_and_the_dense_bar_is_one_line(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        top = app.query_one("#top", TopBar)

        top.set_scroller("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
        await pilot.pause()
        before = drawn(top.content)
        top.advance_scroller()
        await pilot.pause()

        assert "ABCDEFGH" in before
        assert drawn(top.content) != before

        top.set_scroller("")
        await pilot.pause()

        assert "ABCDEFGH" not in drawn(top.content)

        top.set_density("dense")
        await pilot.pause()

        assert len(drawn(top.content).strip().splitlines()) == 1


def test_the_scroller_window_is_as_wide_as_the_bar_reserves() -> None:
    assert len(sine_scroller("lirts ", width=SCROLLER_WIDTH, offset=3).plain) == SCROLLER_WIDTH
