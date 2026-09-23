"""Glyph sets and styles: the shapes, the switching and what applying one changes."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from lirts.identity_rules import Role
from lirts.tui import glyphs
from lirts.tui.app import LirtsApp
from lirts.tui.render import DEFAULT_CLOCK_FORMAT, set_clock_format, sparkline
from lirts.tui.styles import Border, Density, effective_border, next_style, style_for
from tests.conftest import wait_rows


@pytest.fixture(autouse=True)
def _default_look() -> Iterator[None]:
    """Every test starts and leaves the app drawing with the default glyphs and clock."""
    glyphs.set_active(glyphs.DEFAULT_GLYPH_SET)
    set_clock_format(DEFAULT_CLOCK_FORMAT)
    yield
    glyphs.set_active(glyphs.DEFAULT_GLYPH_SET)
    set_clock_format(DEFAULT_CLOCK_FORMAT)


@pytest.mark.parametrize("name", ["block", "braille", "demoscene"])
def test_every_glyph_set_has_the_same_shape(name: str) -> None:
    glyph_set = glyphs.GLYPH_SETS[name]

    assert glyph_set.name == name
    assert len(glyph_set.spark) == 9
    assert len(glyph_set.partial_blocks) == 8
    assert len(glyph_set.bar_full) == 1 and len(glyph_set.bar_empty) == 1
    assert all(role in glyph_set.role_icons for role in Role)
    assert sorted(glyph_set.source_icons) == ["docker", "kubernetes", "local", "ssh"]
    assert sorted(glyph_set.status_glyphs) == ["error", "ok", "warning"]


def test_block_glyphs_are_what_the_ui_has_always_drawn() -> None:
    assert glyphs.BLOCK.spark == " ▁▂▃▄▅▆▇█"
    assert glyphs.BLOCK.bar_full == "█" and glyphs.BLOCK.bar_empty == "░"
    assert glyphs.BLOCK.role_icon(Role.DB) == "◆"
    assert glyphs.BLOCK.role_icon("cluster") == "☁"
    assert glyphs.BLOCK.source_icon("kubernetes") == "☸"


def test_switching_the_glyph_set_changes_a_sparkline() -> None:
    assert sparkline([0, 50, 100], width=3, maximum=100) == "▁▄█"

    glyphs.set_active("demoscene")
    assert sparkline([0, 50, 100], width=3, maximum=100) == "░▒█"

    glyphs.set_active("braille")
    assert sparkline([0, 50, 100], width=3, maximum=100) == "⢀⣤⣿"

    assert glyphs.set_active("nonsense").name == "block"


def test_borders_follow_the_rounded_corners_setting_only_when_they_are_plain() -> None:
    assert effective_border(style_for("classic"), rounded_corners=True) == Border.ROUND
    assert effective_border(style_for("classic"), rounded_corners=False) == Border.SOLID
    assert effective_border(style_for("cracktro"), rounded_corners=False) == Border.DOUBLE
    assert effective_border(style_for("phosphor"), rounded_corners=True) == Border.ASCII


def test_the_style_ring_wraps_and_falls_back() -> None:
    assert next_style("classic").name == "compact"
    assert next_style("phosphor").name == "classic"
    assert next_style("nonsense").name == "classic"
    assert style_for("nonsense").name == "classic"


async def test_applying_a_style_sets_the_classes_and_the_glyphs_but_never_the_theme(
    app: LirtsApp,
) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        theme = app.theme

        app.apply_style("cracktro")
        await pilot.pause()

        assert app.theme == theme
        assert app.has_class("style-cracktro")
        assert app.has_class("borders-double")
        assert app.has_class("density-normal")
        assert app.has_class("corners-round")
        assert glyphs.active().name == "demoscene"

        app.apply_style("phosphor")
        await pilot.pause()

        assert app.theme == theme
        assert app.has_class("style-phosphor") and not app.has_class("style-cracktro")
        assert app.has_class("borders-ascii")
        assert glyphs.active().name == "block"


async def test_the_two_style_themes_are_registered_for_the_theme_picker(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)

        assert "cracktro" in app.available_themes
        assert "phosphor" in app.available_themes


async def test_t_cycles_the_style_and_remembers_it_without_touching_the_theme(
    app: LirtsApp,
) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        theme = app.theme

        await pilot.press("T")
        await pilot.pause()

        assert app.config["ui"]["style"] == "compact"
        assert app.config["ui"]["glyphs"] == "block"
        assert app.theme == theme and app.config["theme"] == theme
        assert app.has_class("style-compact") and app.has_class("density-dense")
        # compact hides the side panel and packs the table.
        assert app.query_one("#side").has_class("hidden")
        assert app.query_one("#table").cell_padding == 0

        await pilot.press("T")
        await pilot.pause()

        assert app.config["ui"]["style"] == "tight"
        assert app.has_class("density-tight")
        assert not app.query_one("#side").has_class("hidden")


async def test_a_style_shows_and_hides_the_boxes_without_writing_the_config(
    app: LirtsApp,
) -> None:
    app.config["side_panel"] = True
    app.config["ui"]["history_panel"] = True
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)

        await pilot.press("T")  # compact: side panel off, history off
        await pilot.pause()

        assert app.query_one("#side").has_class("hidden")
        assert app.query_one("#history").has_class("hidden")
        assert app.config["side_panel"] is True
        assert app.config["net_panel"] is True
        assert app.config["ui"]["history_panel"] is True


async def test_rounded_corners_and_glyph_settings_apply_live(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)

        app.config["ui"]["rounded_corners"] = False
        app.apply_setting("ui.rounded_corners", False)
        await pilot.pause()

        assert app.has_class("corners-square") and app.has_class("borders-solid")

        app.config["ui"]["glyphs"] = "braille"
        app.apply_setting("ui.glyphs", "braille")
        await pilot.pause()

        assert glyphs.active().name == "braille"


async def test_apply_looks_takes_style_glyphs_and_clock_from_the_config(app: LirtsApp) -> None:
    app.config["ui"]["style"] = "tight"
    app.config["ui"]["glyphs"] = "braille"
    app.config["ui"]["clock"] = "%H:%M"
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)

        app.apply_looks()
        await pilot.pause()

        assert app.has_class("style-tight") and app.has_class(f"density-{Density.TIGHT}")
        # The explicit glyph setting wins over the one the style would draw with.
        assert glyphs.active().name == "braille"
        # A style never picks a theme, so the theme setting stays in force.
        assert app.theme == "dracula"


@pytest.mark.parametrize("name", ["classic", "compact", "tight", "cracktro", "phosphor"])
async def test_every_style_applies_on_a_running_dashboard(name: str, app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)

        app.apply_style(name)
        await pilot.pause()

        assert app.has_class(f"style-{name}")
        assert app.query_one("#table").row_count == 3
        assert app.query_one("#side").has_class("hidden") is not style_for(name).side_panel


async def test_a_style_applied_at_start_up_leaves_the_saved_panel_settings_alone(
    app: LirtsApp,
) -> None:
    app.config["ui"]["style"] = "compact"  # compact would hide the side panel
    app.config["side_panel"] = True
    app.config["theme"] = "dracula"
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)

        app.apply_looks()
        await pilot.pause()

        assert app.has_class("style-compact") and app.has_class("borders-none")
        assert not app.query_one("#side").has_class("hidden")
        assert app.theme == "dracula"
