from __future__ import annotations

import shutil
from types import SimpleNamespace

from lirts import doctor
from lirts.doctor import (
    FAIL,
    OK,
    WARN,
    Check,
    as_dict,
    check_bandwidth,
    check_notify,
    check_python,
    check_terminal,
)


def test_check_python_reports_the_running_interpreter() -> None:
    check = check_python()

    assert check.status == OK
    assert check.name == "lirts / Python"
    assert "Python" in check.detail and check.hint is None


def test_check_python_fails_on_an_unsupported_interpreter(monkeypatch) -> None:
    monkeypatch.setattr(doctor.sys, "version_info", (3, 10, 0))

    check = check_python()

    assert check.status == FAIL
    assert check.hint == "lirts needs Python 3.11 or newer"


def test_check_terminal_warns_about_a_narrow_window(monkeypatch) -> None:
    monkeypatch.setattr(
        shutil, "get_terminal_size", lambda default=(0, 0): SimpleNamespace(columns=80, lines=24)
    )

    check = check_terminal()

    assert check.status == WARN
    assert "80x24" in check.detail
    assert check.hint is not None and "140 columns" in check.hint


def test_check_terminal_is_happy_with_a_wide_window(monkeypatch) -> None:
    monkeypatch.setattr(
        shutil, "get_terminal_size", lambda default=(0, 0): SimpleNamespace(columns=200, lines=60)
    )

    assert check_terminal().status == OK


def test_check_terminal_does_not_complain_when_there_is_no_terminal(monkeypatch) -> None:
    monkeypatch.setattr(
        shutil, "get_terminal_size", lambda default=(0, 0): SimpleNamespace(columns=0, lines=0)
    )

    check = check_terminal()

    assert check.status == OK and "not attached to a terminal" in check.detail


def test_check_bandwidth_follows_the_platform_and_nettop(monkeypatch) -> None:
    monkeypatch.setattr(doctor.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/usr/bin/nettop")
    assert check_bandwidth().status == OK

    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    assert check_bandwidth().status == WARN

    monkeypatch.setattr(doctor.platform, "system", lambda: "Linux")
    check = check_bandwidth()
    assert check.status == WARN and "macOS-only" in check.detail


def test_check_notify_names_the_backend_it_found(monkeypatch) -> None:
    monkeypatch.setattr(doctor.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        doctor.shutil, "which", lambda name: "/opt/bin/x" if name == "terminal-notifier" else None
    )
    assert "terminal-notifier" in check_notify().detail

    monkeypatch.setattr(
        doctor.shutil, "which", lambda name: "/usr/bin/osascript" if name == "osascript" else None
    )
    assert "osascript" in check_notify().detail

    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    assert check_notify().status == WARN


def test_as_dict_gives_the_json_shape_of_a_check() -> None:
    check = Check("docker", WARN, "not found", "install Docker")

    assert as_dict(check) == {
        "name": "docker",
        "status": "warn",
        "detail": "not found",
        "hint": "install Docker",
    }
