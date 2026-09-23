"""Secondary screens: details, logs, kill confirmation, explain, help."""

from __future__ import annotations

from lirts.tui.screens.columns import ColumnsScreen
from lirts.tui.screens.details import DetailsScreen
from lirts.tui.screens.events import EVENT_LEGEND, EventsScreen
from lirts.tui.screens.explain import ExplainScreen
from lirts.tui.screens.fix import FixScreen
from lirts.tui.screens.graph import GraphScreen
from lirts.tui.screens.health import HealthScreen
from lirts.tui.screens.help_confirm import KEY_HELP, ConfirmScreen, HelpScreen
from lirts.tui.screens.insight_text import insight_lines
from lirts.tui.screens.kill_restart import KillScreen, RestartScreen
from lirts.tui.screens.kube import KubeScreen
from lirts.tui.screens.log import LogScreen
from lirts.tui.screens.menu import MENU_ITEMS, MenuScreen, open_menu
from lirts.tui.screens.pane_focus import focus_active_pane
from lirts.tui.screens.picker import PickerScreen, glyph_preview
from lirts.tui.screens.prompt import PromptScreen
from lirts.tui.screens.reach_namespace import NamespaceScreen, ReachScreen
from lirts.tui.screens.settings import SettingsScreen
from lirts.tui.screens.setup import SETUP_STEPS, SetupScreen
from lirts.tui.screens.stack import StackScreen
from lirts.tui.screens.starmap import StarMapScreen

__all__ = [
    "EVENT_LEGEND",
    "KEY_HELP",
    "MENU_ITEMS",
    "SETUP_STEPS",
    "ColumnsScreen",
    "ConfirmScreen",
    "DetailsScreen",
    "EventsScreen",
    "ExplainScreen",
    "FixScreen",
    "GraphScreen",
    "HealthScreen",
    "HelpScreen",
    "KillScreen",
    "KubeScreen",
    "LogScreen",
    "MenuScreen",
    "NamespaceScreen",
    "PickerScreen",
    "PromptScreen",
    "ReachScreen",
    "RestartScreen",
    "SettingsScreen",
    "SetupScreen",
    "StackScreen",
    "StarMapScreen",
    "focus_active_pane",
    "glyph_preview",
    "insight_lines",
    "open_menu",
]
