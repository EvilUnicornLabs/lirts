from __future__ import annotations

from lirts.filtering import Term, apply_filter, matches, parse_filter, term_matches
from lirts.identity import ROLE_BACKEND, ROLE_FRONTEND, ROLE_SYSTEM
from lirts.models import Identity, Insight
from tests.conftest import make_container, make_listener, make_process


def rows():
    web = make_listener(
        port=5173, identity=Identity("Vite dev server", ROLE_FRONTEND, 0.9, project="shop")
    )
    api = make_listener(
        port=8000, name="python", identity=Identity("FastAPI", ROLE_BACKEND, 0.9, project="shop")
    )
    api.insights.append(Insight("error", "port-conflict", "conflict"))
    db = make_listener(
        port=5432,
        container=make_container(
            name="shop-db-1", image="postgres:16", host_ports=[5432], stack="shop"
        ),
        processes=[make_process(pid=7, name="com.docker.backend")],
        identity=Identity("PostgreSQL", "db", 0.9, project="shop"),
    )
    sysd = make_listener(
        port=5353, protocol="UDP", state="BOUND", identity=Identity("mDNS", ROLE_SYSTEM, 0.9)
    )
    return [web, api, db, sysd]


def test_parse() -> None:
    assert parse_filter("status:error -system port:3000-3999 vite !src:docker") == [
        Term("status", "error"),
        Term(None, "system", True),
        Term("port", "3000-3999"),
        Term(None, "vite"),
        Term("src", "docker", True),
    ]
    assert parse_filter("http://x") == [Term(None, "http://x")]  # unknown key stays a plain word
    assert parse_filter("  ") == []


def test_apply_filter() -> None:
    items = rows()
    assert [x.port for x in apply_filter(items, "")] == [5173, 8000, 5432, 5353]
    assert [x.port for x in apply_filter(items, "status:error")] == [8000]
    assert [x.port for x in apply_filter(items, "src:docker")] == [5432]
    assert [x.port for x in apply_filter(items, "project:shop -src:docker")] == [5173, 8000]
    assert [x.port for x in apply_filter(items, "port:5000-6000")] == [5173, 5432, 5353]
    assert [x.port for x in apply_filter(items, "port:8000")] == [8000]
    assert [x.port for x in apply_filter(items, "proto:udp")] == [5353]
    assert [x.port for x in apply_filter(items, "role:frontend")] == [5173]
    assert [x.port for x in apply_filter(items, "image:postgres")] == [5432]
    assert [x.port for x in apply_filter(items, "container:db")] == [5432]
    assert [x.port for x in apply_filter(items, "pid:7")] == [5432]
    assert [x.port for x in apply_filter(items, "-mdns fastapi")] == [8000]
    assert apply_filter(items, "state:STOPPED") == []
    assert [x.port for x in apply_filter(items, "process:python")] == [8000]


def test_term_matches_exact_fields_and_substrings() -> None:
    web, api, db, _ = rows()

    assert term_matches(web, Term("role", "frontend")) is True
    assert term_matches(web, Term("role", "front")) is False  # role is an exact match
    assert term_matches(web, Term("service", "vite")) is True  # free fields match substrings
    assert term_matches(db, Term("container", "shop-db")) is True
    assert term_matches(api, Term("pid", str(api.pids[0]))) is True
    assert term_matches(api, Term("pid", "999999")) is False


def test_a_negated_term_inverts_the_match() -> None:
    web, _, db, _ = rows()

    assert term_matches(web, Term("src", "docker", negate=True)) is True
    assert term_matches(db, Term("src", "docker", negate=True)) is False


def test_the_new_field_is_left_to_the_app_which_knows_the_clock() -> None:
    web, *_ = rows()

    assert term_matches(web, Term("new", "yes")) is False


def test_matches_requires_every_term() -> None:
    web, api, *_ = rows()
    terms = [Term("role", "frontend"), Term("project", "shop")]

    assert matches(web, terms) is True
    assert matches(api, terms) is False
    assert matches(api, []) is True
