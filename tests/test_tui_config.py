"""Config hot reload, the outdated-config notice and the setup wizard."""

from __future__ import annotations

import time
from typing import Any

from textual.widgets import Input

from lirts.config import load_config
from lirts.tui.app import LirtsApp
from tests.conftest import FakeEngine, wait_rows


async def test_config_hot_reload(tmp_path) -> None:
    import yaml

    from lirts.config import load_config as _lc

    cfg_path = tmp_path / "hot.yaml"
    cfg_path.write_text("refresh_interval: 60\nshow_udp: false\n")
    cfg = _lc(cfg_path)
    app = LirtsApp(FakeEngine(), cfg)  # type: ignore[arg-type]  # FakeEngine stands in for Engine
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        assert app.engine.show_udp is False
        data = yaml.safe_load(cfg_path.read_text())
        data["show_udp"] = True
        data["group_by_stack"] = True
        cfg_path.write_text(yaml.safe_dump(data))
        import os

        os.utime(cfg_path, (time.time() + 5, time.time() + 5))
        app._check_config_file()
        await pilot.pause()
        assert app.engine.show_udp is True and app.grouped is True
        assert app.config["group_by_stack"] is True


async def test_outdated_config_is_announced_not_migrated(tmp_path: Any) -> None:
    path = tmp_path / "c.yaml"
    text = "version: 2\nrefresh_interval: 60\nhealth:\n  enabled: true\n"
    path.write_text(text)
    cfg = load_config(path)
    app = LirtsApp(FakeEngine(), cfg)  # type: ignore[arg-type]  # FakeEngine stands in for Engine
    toasts: list[str] = []
    original = app.notify

    def spy(message, **kw):
        toasts.append(str(message))
        return original(message, **kw)

    app.notify = spy  # type: ignore[method-assign]  # spy on notify
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        assert any("outdated" in t and "health.enabled" in t for t in toasts)
    assert path.read_text() == text


async def test_setup_wizard_walks_and_saves(tmp_path: Any) -> None:
    from textual.widgets import OptionList, SelectionList

    from lirts.tui.screens import SETUP_STEPS, SetupScreen

    path = tmp_path / "c.yaml"
    cfg = load_config(path)
    cfg["refresh_interval"] = 60.0
    engine = FakeEngine()
    app = LirtsApp(engine, cfg)  # type: ignore[arg-type]  # FakeEngine stands in for Engine
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        await pilot.press("W")
        await pilot.pause()
        assert isinstance(app.screen, SetupScreen)
        screen = app.screen
        assert screen.index == 0 and screen.steps[0].path == "theme"
        assert [s.path for s in screen.steps[:8]] == [
            "theme",
            "ui.style",
            "ui.layout",
            "ui.glyphs",
            "ui.rounded_corners",
            "ui.row_icons",
            "ui.history_panel",
            "ui.clock",
        ]
        for _ in range(8):  # keep the theme and every look step as it is
            await pilot.press("right")
            await pilot.pause()
        assert screen.values["ui.style"] == "classic" and screen.values["ui.glyphs"] == "block"
        assert screen.values["ui.rounded_corners"] is True
        assert screen.values["ui.clock"] == "%H:%M:%S"
        assert screen.steps[screen.index].path == "sources"
        multi = screen.query_one("#setup-multi", SelectionList)
        assert multi.display and sorted(multi.selected) == ["docker", "kubernetes", "local", "ssh"]
        multi.deselect("kubernetes")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert screen.values["sources"] == ["local", "docker", "ssh"]
        assert screen.steps[screen.index].path == "columns"
        await pilot.press("left")
        await pilot.pause()
        assert screen.steps[screen.index].path == "sources"
        await pilot.press("right", "right")  # sources, columns
        await pilot.pause()
        assert screen.steps[screen.index].path == "refresh_interval"
        field = screen.query_one("#setup-input", Input)
        assert field.display and field.value == "60"
        field.value = "abc"
        await pilot.press("enter")  # invalid number: stays on the step
        await pilot.pause()
        assert screen.steps[screen.index].path == "refresh_interval"
        field.value = "3"
        await pilot.press("enter")
        await pilot.pause()
        assert screen.values["refresh_interval"] == 3.0
        assert screen.steps[screen.index].path == "group_by_stack"
        options = screen.query_one("#setup-options", OptionList)
        assert options.display
        await pilot.press("up", "enter")  # "on"
        await pilot.pause()
        assert screen.values["group_by_stack"] is True
        while screen.index < len(screen.steps):
            await pilot.press("right")
            await pilot.pause()
        assert screen.query_one("#setup-summary").display
        await pilot.press("enter")  # save
        await pilot.pause()
        assert not isinstance(app.screen, SetupScreen)
        assert path.exists()
        saved = load_config(path)
        assert saved["sources"] == ["local", "docker", "ssh"] and saved["refresh_interval"] == 3.0
        assert saved["group_by_stack"] is True and engine.sources == {"local", "docker", "ssh"}
        assert app.grouped is True
        assert len(SETUP_STEPS) == len(screen.steps)
