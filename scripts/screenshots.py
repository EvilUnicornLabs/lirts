"""Take the README screenshots from the demo machine: nothing personal is ever in them.

Run with ``venv/bin/python scripts/screenshots.py``; writes SVG files under ``docs/images/``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from lirts.config import DEFAULT_CONFIG, validate
from lirts.demo import DemoEngine
from lirts.settings import set_path
from lirts.tui.app import LirtsApp

OUT = Path(__file__).resolve().parent.parent / "docs" / "images"
SIZE = (150, 42)
# (file name, config overrides, keys pressed before the shot, seconds to wait after the keys)
SHOTS: list[tuple[str, dict[str, object], list[str], float]] = [
    ("main.svg", {}, [], 6.0),
    ("starmap.svg", {}, ["M"], 0.5),
    ("explain.svg", {}, ["E"], 0.8),
    ("who.svg", {}, ["w"], 0.5),
    ("kube.svg", {}, ["K"], 0.8),
    ("details.svg", {}, ["down", "down", "down", "i"], 0.5),
    # The cracktro style with the cracktro theme: a style never changes colours by itself.
    ("cracktro.svg", {"theme": "cracktro", "ui.style": "cracktro"}, [], 6.0),
]


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, overrides, keys, wait in SHOTS:
        cfg = validate(dict(DEFAULT_CONFIG))
        cfg["refresh_interval"] = 1.0
        for path, value in overrides.items():
            set_path(cfg, path=path, value=value)
        engine = DemoEngine(cfg)
        app = LirtsApp(engine, cfg)
        async with app.run_test(size=SIZE) as pilot:
            for _ in range(30):
                await pilot.pause(0.2)
                if app.snapshot.listeners:
                    break
            await pilot.pause(0.5)
            for key in keys:
                await pilot.press(key)
                await pilot.pause(0.2)
            await pilot.pause(wait)
            app.save_screenshot(str(OUT / name))
        engine.close()
        print("wrote", OUT / name)


if __name__ == "__main__":
    asyncio.run(main())
