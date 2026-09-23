from __future__ import annotations

import os
from pathlib import Path

from lirts.collectors.hosts import HostsMap, parse_hosts

SAMPLE = """
# comment
127.0.0.1\tlocalhost
127.0.0.1 local.admin.example.test local.example.test # trailing comment
::1             localhost
192.168.1.10    nas
broken
"""


def test_parse_hosts() -> None:
    mapping = parse_hosts(SAMPLE)
    assert mapping["127.0.0.1"] == ["localhost", "local.admin.example.test", "local.example.test"]
    assert mapping["::1"] == ["localhost"]
    assert mapping["192.168.1.10"] == ["nas"]
    assert "broken" not in mapping


def test_hosts_map_loopback_names_and_reload(tmp_path: Path) -> None:
    path = tmp_path / "hosts"
    path.write_text(SAMPLE)
    hm = HostsMap(path)
    hm.refresh()
    assert hm.loopback_names() == ["local.admin.example.test", "local.example.test"]
    path.write_text("127.0.0.1 only.test\n")
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 10))
    hm.refresh()
    assert hm.loopback_names() == ["only.test"]


def test_hosts_map_missing_file(tmp_path: Path) -> None:
    hm = HostsMap(tmp_path / "nope")
    hm.refresh()
    assert hm.loopback_names() == []
