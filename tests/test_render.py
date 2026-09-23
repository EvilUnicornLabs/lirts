"""The cell renderers, the column registry and the traffic graph."""

from __future__ import annotations

import time

from lirts.config_view import ConfigView
from lirts.models import HttpProbe, Identity, Status
from lirts.tui.netpanel import format_bytes, format_rate_bits, render_net_graph
from lirts.tui.render import (
    COLUMNS,
    DEFAULT_CLOCK_FORMAT,
    activity_text,
    bandwidth_text,
    bar,
    clock_format,
    cpu_text,
    format_time,
    http_text,
    level_marker,
    mem_text,
    pids_text,
    resolve_columns,
    service_text,
    set_clock_format,
    sparkline,
    status_text,
    threshold_style,
)
from tests.conftest import make_listener, make_process


def test_sparkline_and_bar() -> None:
    assert sparkline([]) == ""
    assert sparkline([0, 0]) == "▁▁"
    line = sparkline([0, 50, 100], width=3, maximum=100)
    assert len(line) == 3 and line[-1] == "█"
    assert len(sparkline(list(range(100)), width=5)) == 5
    assert bar(0, 4) == "░░░░" and bar(100, 4) == "████" and bar(50, 4) == "██░░"


def test_threshold_styles() -> None:
    limits = (40.0, 80.0)
    assert threshold_style(10, limits) == "green"
    assert threshold_style(50, limits) == "yellow"
    assert threshold_style(90, limits) == "bold red"
    assert threshold_style(90, None) == ""
    assert cpu_text(90.0, limits).style == "bold red"
    assert mem_text(2048, limits).plain == "2.0 GB"


def test_level_markers() -> None:
    assert level_marker("error") == ("✖", "bold red")
    assert level_marker("warning") == ("⚠", "yellow")
    assert level_marker("info") == ("•", "dim")
    assert level_marker("something-else") == ("•", "")


def test_status_and_activity_text() -> None:
    assert status_text(Status.ERROR).plain == "● error"
    assert status_text(Status.WARNING, short=True).plain == "●"
    assert status_text(Status.HEALTHY).style == "green"
    assert "n/a" in status_text("unknown").plain
    lst = make_listener()
    lst.activity = "hot"
    assert "hot" in activity_text(lst).plain
    lst.activity = "active"
    assert "active" in activity_text(lst).plain


def test_misc_cells() -> None:
    lst = make_listener(processes=[make_process(pid=1), make_process(pid=2)])
    assert pids_text(lst).plain == "1 +1"
    assert pids_text(make_listener(processes=[])).plain == "-"
    assert bandwidth_text(lst).plain == "-"
    lst.bytes_in_rate = 2048.0
    lst.bytes_out_rate = 10.0
    lst.bandwidth_shared = True
    assert bandwidth_text(lst).plain.startswith("~↓2.0KB/s")
    lst.identity.confidence = 0.2
    lst.identity.service = "Maybe"
    assert service_text(lst).plain == "Maybe?"
    lst.http = HttpProbe(attempted=True, ok=True, status=503)
    assert http_text(lst).style == "red"
    assert http_text(make_listener()).plain == "-"


def test_columns_registry_renders_everything() -> None:
    lst = make_listener()
    cfg = ConfigView({})

    for col in COLUMNS.values():
        cell = col.render(lst, cfg)
        assert cell.plain is not None
        col.sort_key(lst)
    assert [c.key for c in resolve_columns(["port", "bogus", "cpu"])] == ["port", "cpu"]
    assert resolve_columns(["bogus"])[0].key == "port"


def test_columns_colour_cpu_and_memory_from_the_config() -> None:
    lst = make_listener(processes=[make_process(pid=1, cpu_percent=95.0, memory_mb=4000.0)])
    cfg = ConfigView({"thresholds": {"cpu": {"yellow": 40, "red": 80}}})

    assert COLUMNS["cpu"].render(lst, cfg).style == "bold red"


def test_render_net_graph_shape() -> None:
    lines, scale = render_net_graph([0, 512, 1024], up=[256, 0, 1024], width=6, height=6)
    assert len(lines) == 6 and scale == 1024.0
    assert all(len(line.plain) == 6 for line in lines)
    assert lines[0].plain.startswith("1K")  # scale label top-left
    assert lines[-1].plain.startswith("1K")
    # the latest download sample is full height: top row ends with a full block
    assert lines[0].plain.endswith("█") and lines[2].plain.endswith("█")
    # the latest upload sample too, mirrored below the midline
    assert lines[3].plain.endswith("█") and lines[5].plain.endswith("█")
    # a half-height sample fills the lower half of the rows only
    assert lines[2].plain[-2] == "█" and lines[0].plain[-2] == " "
    empty, scale = render_net_graph([], up=[], width=5, height=4)
    assert len(empty) == 4 and scale == 1.0
    assert format_bytes(2.3 * 1024**3) == "2.30 GiB" and format_bytes(10) == "10 B"
    assert format_rate_bits(3584) == "28.0 Kibps"


def test_row_icons_put_the_role_and_source_glyph_in_front_of_the_cells() -> None:
    lst = make_listener(port=5432, name="postgres", identity=Identity("PostgreSQL", "db", 0.95))
    off = ConfigView({"ui": {"row_icons": False}})
    on = ConfigView({"ui": {"row_icons": True}})

    assert COLUMNS["service"].render(lst, off).plain == "PostgreSQL"
    assert COLUMNS["src"].render(lst, off).plain == "local"
    assert COLUMNS["service"].render(lst, on).plain == "◆ PostgreSQL"
    assert COLUMNS["src"].render(lst, on).plain == "⌂ local"


def test_role_is_accepted_as_a_name_for_the_role_column() -> None:
    assert [c.key for c in resolve_columns(["port", "role"])] == ["port", "identity"]
    assert COLUMNS["identity"].label == "ROLE"
    assert COLUMNS["identity"].sort_key(make_listener()) is not None


def test_format_time_uses_the_given_format_and_then_the_configured_one() -> None:
    # 2021-01-01 00:00:00 UTC, read back in the local zone the test machine runs in.
    stamp = 1609459200.0
    expected_date = time.strftime("%Y-%m-%d", time.localtime(stamp))
    expected_clock = time.strftime("%H:%M", time.localtime(stamp))

    assert format_time(stamp, "%Y-%m-%d") == expected_date
    assert format_time(stamp, "%H:%M") == expected_clock

    set_clock_format("%H:%M")
    try:
        assert clock_format() == "%H:%M"
        assert format_time(stamp) == expected_clock
    finally:
        set_clock_format(DEFAULT_CLOCK_FORMAT)
    assert format_time(stamp) == time.strftime(DEFAULT_CLOCK_FORMAT, time.localtime(stamp))
