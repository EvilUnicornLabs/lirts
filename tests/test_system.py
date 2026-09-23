from __future__ import annotations

import socket
from types import SimpleNamespace

import psutil

from lirts.collectors.system import SystemSampler


def _addr(address: str, family: int = socket.AF_INET) -> SimpleNamespace:
    return SimpleNamespace(family=family, address=address)


def test_own_address_prefers_the_usual_primary_interface(monkeypatch) -> None:
    monkeypatch.setattr(
        psutil,
        "net_if_addrs",
        lambda: {
            "lo0": [_addr("127.0.0.1")],
            "en5": [_addr("192.168.9.9")],
            "en0": [_addr("192.168.1.42")],
        },
    )
    monkeypatch.setattr(
        psutil,
        "net_if_stats",
        lambda: {name: SimpleNamespace(isup=True) for name in ("lo0", "en5", "en0")},
    )
    sampler = SystemSampler()

    assert sampler.own_address(now=1000.0) == ("192.168.1.42", "en0")


def test_own_address_skips_interfaces_that_are_down_or_virtual(monkeypatch) -> None:
    monkeypatch.setattr(
        psutil,
        "net_if_addrs",
        lambda: {
            "docker0": [_addr("172.17.0.1")],
            "utun3": [_addr("10.8.0.2")],
            "en0": [_addr("192.168.1.42")],
            "en7": [_addr("192.168.7.7")],
        },
    )
    monkeypatch.setattr(
        psutil,
        "net_if_stats",
        lambda: {
            "docker0": SimpleNamespace(isup=True),
            "utun3": SimpleNamespace(isup=True),
            "en0": SimpleNamespace(isup=False),
            "en7": SimpleNamespace(isup=True),
        },
    )
    sampler = SystemSampler()

    assert sampler.own_address(now=1000.0) == ("192.168.7.7", "en7")


def test_own_address_is_a_dash_when_nothing_qualifies(monkeypatch) -> None:
    monkeypatch.setattr(psutil, "net_if_addrs", lambda: {"lo0": [_addr("127.0.0.1")]})
    monkeypatch.setattr(psutil, "net_if_stats", lambda: {"lo0": SimpleNamespace(isup=True)})
    sampler = SystemSampler()

    assert sampler.own_address(now=1000.0) == ("-", "-")


def test_own_address_is_cached_for_a_minute(monkeypatch) -> None:
    calls: list[int] = []

    def counted() -> dict[str, list[SimpleNamespace]]:
        calls.append(1)
        return {"en0": [_addr("192.168.1.42")]}

    monkeypatch.setattr(psutil, "net_if_addrs", counted)
    monkeypatch.setattr(psutil, "net_if_stats", lambda: {"en0": SimpleNamespace(isup=True)})
    sampler = SystemSampler()

    sampler.own_address(now=1000.0)
    sampler.own_address(now=1030.0)
    sampler.own_address(now=1090.0)

    assert len(calls) == 2


def test_own_address_degrades_when_psutil_cannot_list_interfaces(monkeypatch) -> None:
    def boom() -> dict[str, list[SimpleNamespace]]:
        raise OSError("no interfaces here")

    monkeypatch.setattr(psutil, "net_if_stats", boom)
    sampler = SystemSampler()

    assert sampler.own_address(now=1000.0) == ("-", "-")
