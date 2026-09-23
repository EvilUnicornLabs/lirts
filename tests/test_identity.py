from __future__ import annotations

from pathlib import Path

import pytest

from lirts.identity import (
    ROLE_BACKEND,
    ROLE_CACHE,
    ROLE_DB,
    ROLE_FRONTEND,
    ROLE_PROXY,
    ROLE_QUEUE,
    ROLE_SYSTEM,
    ProbeKind,
    guess_project,
    guess_role_from_name,
    identify,
    role_label,
)
from lirts.models import HttpProbe, Identity, Listener
from lirts.tui.cells import service_text
from tests.conftest import make_container, make_listener, make_process


def test_vite_dev_server() -> None:
    lst = make_listener(
        port=5173,
        name="node",
        cmdline=[
            "node",
            f"{Path.home()}/Development/shop/node_modules/.bin/vite",
            "--port",
            "5173",
        ],
    )
    ident = identify(lst)
    assert ident.service == "Vite dev server"
    assert ident.role == ROLE_FRONTEND
    assert ident.confidence >= 0.95
    assert ident.project == "shop"
    assert any("vite" in r for r in ident.reasons)


def test_django_and_postgres() -> None:
    django = identify(
        make_listener(port=8000, name="python3.12", cmdline=["python", "manage.py", "runserver"])
    )
    assert django.service == "Django dev server" and django.role == ROLE_BACKEND
    pg = identify(make_listener(port=5432, name="postgres", cmdline=["postgres", "-D", "/data"]))
    assert pg.service == "PostgreSQL" and pg.role == ROLE_DB
    assert pg.confidence > 0.95  # corroborated by the well-known port


def test_container_image_wins_over_docker_proxy_process() -> None:
    c = make_container(
        name="app-redis-1", image="redis:7-alpine", host_ports=[6379], service="redis"
    )
    lst = make_listener(
        port=6379, name="com.docker.backend", cmdline=["com.docker.backend"], container=c
    )
    ident = identify(lst)
    assert ident.service == "Redis"
    assert ident.role == ROLE_CACHE
    assert ident.project == "app"
    assert ident.confidence >= 0.9


def test_compose_service_name_gives_role_and_own_confidence() -> None:
    c = make_container(
        name="proj-website-1",
        image="proj-website",
        host_ports=[3000],
        service="website",
        stack="proj",
    )
    lst = make_listener(
        port=3000, name="com.docker.backend", cmdline=["com.docker.backend"], container=c
    )
    ident = identify(lst)
    assert ident.service == "website"
    assert ident.role == ROLE_FRONTEND
    assert ident.confidence < 0.9
    assert "name suggests frontend" in ident.reasons[0]


def test_header_signal() -> None:
    lst = make_listener(
        port=4321,
        name="mystery",
        cmdline=["mystery"],
        http=HttpProbe(attempted=True, ok=True, status=200, server="nginx/1.25"),
    )
    ident = identify(lst)
    assert ident.service == "Nginx" and ident.role == ROLE_PROXY
    assert any("Server header" in r for r in ident.reasons)


def test_known_port_fallback_and_unknown() -> None:
    mysql = identify(make_listener(port=3306, name="weird", cmdline=["weird"]))
    assert mysql.service == "MySQL" and mysql.role == ROLE_DB and mysql.confidence < 0.5
    unknown = identify(make_listener(port=45678, name="weird", cmdline=["weird"]))
    assert unknown.service == "weird" and unknown.confidence <= 0.1


def test_aliases_by_port_and_substring() -> None:
    lst = make_listener(port=3000, name="node", cmdline=["node", "server.js"])
    assert identify(lst, aliases={3000: "Shop frontend"}).service == "Shop frontend"
    assert identify(lst, aliases={"server.js": "API"}).service == "API"
    assert identify(lst, aliases={"3000": "By string port"}).service == "By string port"
    ident = identify(lst, aliases={"nomatch": "x"})
    assert ident.service != "x"


def test_system_service_rule() -> None:
    ident = identify(
        make_listener(port=5000, name="ControlCenter", cmdline=["/System/Library/ControlCenter"])
    )
    assert ident.role == ROLE_SYSTEM


def test_guess_project(tmp_path) -> None:
    home = str(tmp_path)
    assert guess_project([f"{home}/Development/shop/api"], home) == "shop"
    assert guess_project([f"{home}/shop"], home) == "shop"
    assert (
        guess_project([f"{home}/Development/node_modules/x"], home) == "node_modules"
        or guess_project([f"{home}/node_modules/x"], home) is None
    )
    assert guess_project(["/usr/local/bin"], home) is None
    assert guess_project([None, ""], home) is None
    assert guess_project([f"{home}/.config/foo"], home) is None


def test_guess_role_from_name() -> None:
    assert guess_role_from_name("website") == ROLE_FRONTEND
    assert guess_role_from_name("gameloop") == ROLE_BACKEND
    assert guess_role_from_name("postgres-primary") == ROLE_DB
    assert guess_role_from_name("nothing-here") is None
    assert guess_role_from_name(None) is None


def test_role_label() -> None:
    assert role_label(ROLE_DB) == "database"
    assert role_label("weird") == "weird"


def test_unknown_docker_only_row() -> None:
    c = make_container(
        name="x-svc-1", image="ghcr.io/acme/svc:1", host_ports=[9000], service="svc", stack="x"
    )
    lst = make_listener(port=9000, processes=[], container=c)
    ident = identify(lst)
    assert ident.service == "svc"
    assert ident.role == ROLE_BACKEND
    assert ident.project == "x"


def test_multiple_processes_use_first_three() -> None:
    procs = [make_process(pid=i, name="httpd", cmdline=["httpd"]) for i in range(1, 12)]
    ident = identify(make_listener(port=8080, processes=procs))
    assert ident.service == "Apache httpd"


def _probed(port: int, name: str = "mystery", **probe: object) -> Listener:
    return make_listener(
        port=port,
        name=name,
        cmdline=[name],
        http=HttpProbe(attempted=True, ok=True, status=200, **probe),  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    ("label", "service", "role"),
    [
        ("Vite", "Vite dev server", ROLE_FRONTEND),
        ("Next.js", "Next.js dev server", ROLE_FRONTEND),
        ("Angular CLI", "Angular dev server", ROLE_FRONTEND),
        ("Django runserver", "Django runserver", ROLE_BACKEND),
        ("uvicorn", "FastAPI (uvicorn)", ROLE_BACKEND),
        ("Laravel", "Laravel dev server", ROLE_BACKEND),
    ],
    ids=["vite", "next", "angular", "django", "fastapi", "laravel"],
)
def test_a_dev_server_seen_by_the_probe_names_the_service(
    label: str, service: str, role: str
) -> None:
    ident = identify(_probed(45001, dev_server=label, kind=ProbeKind.HTML))

    assert ident.service == service
    assert ident.role == role
    assert ident.confidence == 0.9
    assert ident.reasons[0] == f"HTTP probe found {label}"


def test_an_html_answer_suggests_a_frontend_and_json_a_backend() -> None:
    page = identify(_probed(45002, name="node", kind=ProbeKind.HTML))
    api = identify(_probed(45003, name="node", kind=ProbeKind.JSON))

    assert page.role == ROLE_FRONTEND
    assert "answers with HTML" in page.reasons
    assert api.role == ROLE_BACKEND
    assert "answers with JSON" in api.reasons


def test_html_from_a_proxy_is_not_read_as_a_frontend() -> None:
    lst = make_listener(
        port=45004,
        name="nginx",
        cmdline=["nginx: master process"],
        http=HttpProbe(attempted=True, ok=True, status=200, kind=ProbeKind.HTML),
    )

    ident = identify(lst)

    assert ident.service == "Nginx" and ident.role == ROLE_PROXY
    assert "answers with HTML" not in ident.reasons


def test_an_api_doc_page_gives_a_role_but_no_name_to_go_with_it() -> None:
    ident = identify(_probed(45005, dev_server="OpenAPI docs", kind=ProbeKind.HTML))

    assert ident.role == ROLE_BACKEND
    assert ident.service == "mystery"  # the process name, not a name the probe invented
    assert ident.confidence < 0.5  # so the cell marks it as a guess
    assert ident.reasons == ["HTTP probe found OpenAPI docs"]


def test_a_probe_that_did_not_answer_says_nothing() -> None:
    lst = make_listener(port=45006, name="node", cmdline=["node", "server.js"])
    lst.http = HttpProbe(attempted=True, ok=False, error="timeout", kind=ProbeKind.HTML)

    ident = identify(lst)

    assert ident.service == "Node.js app"
    assert all("answers with" not in reason for reason in ident.reasons)


@pytest.mark.parametrize(
    ("name", "role"),
    [
        ("web", ROLE_FRONTEND),
        ("frontend", ROLE_FRONTEND),
        ("ui", ROLE_FRONTEND),
        ("client", ROLE_FRONTEND),
        ("app", ROLE_FRONTEND),
        ("site", ROLE_FRONTEND),
        ("www", ROLE_FRONTEND),
        ("storefront", ROLE_FRONTEND),
        ("api", ROLE_BACKEND),
        ("backend", ROLE_BACKEND),
        ("server", ROLE_BACKEND),
        ("graphql", ROLE_BACKEND),
        ("auth", ROLE_BACKEND),
        ("worker", ROLE_QUEUE),
        ("queue", ROLE_QUEUE),
        ("scheduler", ROLE_QUEUE),
        ("cron", ROLE_QUEUE),
        ("gateway", ROLE_PROXY),
    ],
)
def test_compose_service_names_map_to_roles(name: str, role: str) -> None:
    assert guess_role_from_name(name) == role


def test_a_compose_service_name_says_where_the_role_came_from() -> None:
    c = make_container(name="shop-web-1", image="shop/web:dev", host_ports=[3000], service="web")
    lst = make_listener(port=3000, name="com.docker.backend", container=c)

    ident = identify(lst)

    assert "compose service 'web'" in ident.reasons[0]


@pytest.mark.parametrize(
    ("name", "cmdline", "service", "role"),
    [
        ("node", ["node", "node_modules/.bin/vite"], "Vite dev server", ROLE_FRONTEND),
        ("node", ["node", "next", "dev"], "Next.js dev server", ROLE_FRONTEND),
        ("node", ["node", "nuxt", "dev"], "Nuxt dev server", ROLE_FRONTEND),
        ("node", ["node", "ng", "serve"], "Angular dev server", ROLE_FRONTEND),
        ("node", ["node", "react-scripts", "start"], "Webpack dev server", ROLE_FRONTEND),
        ("node", ["node", "webpack", "serve"], "Webpack dev server", ROLE_FRONTEND),
        ("node", ["node", "astro", "dev"], "Astro dev server", ROLE_FRONTEND),
        ("node", ["node", "svelte-kit", "dev"], "SvelteKit dev server", ROLE_FRONTEND),
        ("node", ["node", "parcel", "serve", "index.html"], "Parcel dev server", ROLE_FRONTEND),
        ("node", ["node", "nest", "start"], "NestJS", ROLE_BACKEND),
        ("node", ["node", "express-app.js"], "Express (Node.js)", ROLE_BACKEND),
        ("node", ["node", "fastify", "start"], "Fastify (Node.js)", ROLE_BACKEND),
        ("node", ["node", "koa", "start"], "Koa (Node.js)", ROLE_BACKEND),
        ("node", ["node", "hapi", "start"], "hapi (Node.js)", ROLE_BACKEND),
        ("node", ["tsx", "watch", "src/server.ts"], "Node.js app (dev)", ROLE_BACKEND),
        ("bun", ["bun", "run", "--bun", "vite"], "Vite dev server", ROLE_FRONTEND),
        ("deno", ["deno", "task", "parcel", "serve"], "Parcel dev server", ROLE_FRONTEND),
        ("python", ["python", "-m", "uvicorn", "app:app"], "ASGI app (uvicorn)", ROLE_BACKEND),
        ("python", ["gunicorn", "app:app"], "Gunicorn (WSGI)", ROLE_BACKEND),
        ("python", ["flask", "run"], "Flask dev server", ROLE_BACKEND),
        ("python", ["python", "manage.py", "runserver"], "Django dev server", ROLE_BACKEND),
        ("python", ["hypercorn", "app:app"], "ASGI app (uvicorn)", ROLE_BACKEND),
        ("python", ["daphne", "app.asgi:application"], "Daphne (ASGI)", ROLE_BACKEND),
    ],
)
def test_process_rules_name_the_framework(
    name: str, cmdline: list[str], service: str, role: str
) -> None:
    ident = identify(make_listener(port=45100, name=name, cmdline=cmdline))

    assert ident.service == service
    assert ident.role == role


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [(0.95, "Guess"), (0.7, "Guess"), (0.6, "Guess"), (0.5, "Guess"), (0.49, "Guess?")],
    ids=["sure", "high", "medium", "edge", "low"],
)
def test_the_service_cell_marks_a_low_confidence_name_as_a_guess(
    confidence: float, expected: str
) -> None:
    lst = make_listener(port=45200, identity=Identity("Guess", ROLE_BACKEND, confidence))

    cell = service_text(lst)

    assert cell.plain == expected


def test_the_service_cell_states_a_sure_name_in_bold_and_dims_a_guess() -> None:
    sure = make_listener(port=45201, identity=Identity("Redis", ROLE_CACHE, 0.95))
    medium = make_listener(port=45202, identity=Identity("Redis", ROLE_CACHE, 0.6))
    guess = make_listener(port=45203, identity=Identity("Redis", ROLE_CACHE, 0.3))

    assert service_text(sure).style == "bold"
    assert service_text(medium).style == ""
    assert service_text(guess).style == "dim italic"
