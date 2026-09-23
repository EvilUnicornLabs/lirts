"""Explain the whole machine: one tab per topic."""

from __future__ import annotations

from rich.console import Group
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Static, TabbedContent, TabPane

from lirts.constants import GLYPH_OK


class ExplainScreen(Screen[None]):
    """The whole machine explained, one tab per topic; opened with `E`."""

    BINDINGS = [
        Binding("escape,q,E", "app.pop_screen", "Close"),
        Binding("right", "next_tab", "Next tab", show=False, priority=True),
        Binding("left", "prev_tab", "Previous tab", show=False, priority=True),
    ]

    def __init__(self, sections: list[tuple[str, str]]) -> None:
        super().__init__()
        self._sections = list(sections)

    @staticmethod
    def colourise(text: str) -> list[Text]:
        """Headings, warning levels, event markers and key hints get colours."""
        out: list[Text] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                out.append(Text(""))
            elif stripped.endswith(":") and not stripped.startswith(("-", "!")):
                out.append(Text(stripped, style="bold underline cyan"))
            elif stripped.startswith("!!"):
                out.append(Text(line, style="bold red"))
            elif stripped.startswith("!"):
                out.append(Text(line, style="yellow"))
            elif stripped.startswith("→"):
                out.append(Text(line, style="italic"))
            elif stripped.startswith("- ") and any(m in stripped[:14] for m in ("[!]", "[-]")):
                out.append(Text(line, style="red" if "[!]" in stripped else "yellow"))
            elif stripped.startswith("- ") and any(
                m in stripped[:14] for m in ("[+]", f"[{GLYPH_OK}]")
            ):
                out.append(Text(line, style="green"))
            elif stripped.startswith("- ") and "[~]" in stripped[:14]:
                out.append(Text(line, style="yellow"))
            elif ":" in stripped and stripped.split(":", 1)[0] in (
                "Inferred stack",
                "Projects",
                "SSH sessions",
                "Started from",
                "Kubernetes",
                "Local connections",
            ):
                head, rest = line.split(":", 1)
                t = Text(head + ":", style="bold cyan")
                t.append(rest)
                out.append(t)
            elif stripped.startswith("- "):
                t = Text("  - ", style="dim")
                t.append(stripped[2:])
                out.append(t)
            else:
                out.append(Text(line, style="bold" if out == [] else ""))
        return out

    def compose(self) -> ComposeResult:
        yield Static(
            "Explain this machine — every listener, not just the selected row (i explains one row)",
            classes="screen-title",
        )
        with TabbedContent(id="explain-tabs"):
            for title, text in self._sections:
                with TabPane(title, id=f"explain-{title.lower()}"):
                    yield VerticalScroll(Static(Group(*self.colourise(text))))
        yield Static("Esc close · ← → switch tabs · ↑ ↓ scroll", classes="screen-footer")

    def on_mount(self) -> None:
        self.query_one("#explain-tabs", TabbedContent).focus()

    def _cycle(self, step: int) -> None:
        tabs = self.query_one("#explain-tabs", TabbedContent)
        ids = [str(pane.id) for pane in tabs.query(TabPane)]
        if not ids or tabs.active not in ids:
            return
        tabs.active = ids[(ids.index(tabs.active) + step) % len(ids)]

    def action_next_tab(self) -> None:
        """Move to the tab on the right (→)."""
        self._cycle(1)

    def action_prev_tab(self) -> None:
        """Move to the tab on the left (←)."""
        self._cycle(-1)
