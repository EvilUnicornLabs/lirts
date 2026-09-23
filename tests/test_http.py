from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import pytest
from aiohttp import web

from lirts.collectors.http import HttpProber
from lirts.identity import ROLE_DB
from lirts.identity_rules_web import ProbeKind, detect_web_signal, probe_kind
from lirts.models import Identity
from tests.conftest import make_listener, make_process


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
async def http_server():
    async def head_not_allowed(request):
        return web.Response(status=405)

    async def index(request):
        return web.Response(
            text="hi", headers={"Server": "TestServer/1.0", "X-Powered-By": "Express"}
        )

    app = web.Application()
    app.router.add_get("/", index, allow_head=False)
    app.router.add_route("HEAD", "/", head_not_allowed)
    runner = web.AppRunner(app)
    await runner.setup()
    port = free_port()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    yield port
    await runner.cleanup()


async def test_probe_success_with_get_fallback(http_server: int) -> None:
    prober = HttpProber(interval=100.0)
    lst = make_listener(port=http_server, processes=[make_process(addresses=["127.0.0.1"])])
    await prober.enrich([lst])
    assert lst.http.attempted and lst.http.ok
    assert lst.http.status == 200
    assert lst.http.server == "TestServer/1.0"
    assert lst.http.powered_by == "Express"
    assert lst.http.scheme == "http"
    assert lst.http.latency_ms is not None and lst.http.latency_ms >= 0
    first = lst.http
    # cached within the interval
    lst2 = make_listener(port=http_server, processes=[make_process(addresses=["127.0.0.1"])])
    lst2.processes[0].pid = lst.processes[0].pid
    await prober.enrich([lst2])
    assert lst2.http is first


async def test_probe_connection_refused() -> None:
    prober = HttpProber(timeout=1.0)
    lst = make_listener(port=free_port())
    await prober.enrich([lst])
    assert lst.http.attempted and not lst.http.ok
    assert "refused" in (lst.http.error or "")


async def test_probe_non_http_is_marked_and_skipped() -> None:
    async def garbage(reader, writer):
        writer.write(b"ERR not http\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(garbage, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        prober = HttpProber(timeout=1.0)
        lst = make_listener(port=port, processes=[make_process(addresses=["127.0.0.1"])])
        await prober.enrich([lst])
        assert lst.http.error == "not HTTP"
        assert prober.should_probe(lst) is False
        # subsequent cycles keep the cached verdict instead of forgetting it
        await prober.enrich([lst])
        assert lst.http.error == "not HTTP"
    finally:
        server.close()
        await server.wait_closed()


def test_policy() -> None:
    prober = HttpProber(skip_ports=[5432], force_ports=[5433])
    assert prober.should_probe(make_listener(port=5432)) is False
    assert prober.should_probe(make_listener(port=5433)) is True
    assert prober.should_probe(make_listener(port=53, protocol="UDP")) is False
    db = make_listener(port=7777, identity=Identity("PostgreSQL", ROLE_DB, 0.95))
    assert prober.should_probe(db) is False
    low_conf_db = make_listener(port=7778, identity=Identity("MySQL", ROLE_DB, 0.35))
    assert prober.should_probe(low_conf_db) is True
    assert HttpProber(enabled=False).should_probe(make_listener()) is False


def test_target_host() -> None:
    assert (
        HttpProber.target_host(make_listener(processes=[make_process(addresses=["::1"])]))
        == "[::1]"
    )
    assert (
        HttpProber.target_host(make_listener(processes=[make_process(addresses=["::"])]))
        == "127.0.0.1"
    )
    assert (
        HttpProber.target_host(make_listener(processes=[make_process(addresses=["192.168.1.5"])]))
        == "192.168.1.5"
    )
    assert HttpProber.target_host(make_listener(processes=[])) == "127.0.0.1"


async def test_disabled_prober_is_noop() -> None:
    lst = make_listener()
    await HttpProber(enabled=False).enrich([lst])
    assert lst.http.attempted is False


def test_mark_non_http_stops_a_listener_from_being_probed_again() -> None:
    prober = HttpProber()
    listener = make_listener(port=4000)

    assert prober.should_probe(listener) is True
    prober.mark_non_http(prober._cache_key(listener))
    assert prober.should_probe(listener) is False


def test_a_confidently_identified_database_is_never_probed() -> None:
    prober = HttpProber()
    database = make_listener(
        port=5432, identity=Identity("PostgreSQL", ROLE_DB, 0.95), name="postgres"
    )
    guess = make_listener(port=5433, identity=Identity("PostgreSQL?", ROLE_DB, 0.4), name="x")

    assert prober.should_probe(database) is False
    assert prober.should_probe(guess) is True


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"Server": "vite"}, "Vite"),
        ({"X-Powered-By": "Next.js"}, "Next.js"),
        ({"Server": "nuxt"}, "Nuxt"),
        ({"Server": "astro"}, "Astro"),
        ({"Server": "webpack-dev-server"}, "webpack dev server"),
        ({"Server": "WSGIServer/0.2 CPython/3.12.1"}, "Django runserver"),
        ({"Server": "Werkzeug/3.0.1 Python/3.12.1"}, "Werkzeug"),
        ({"Server": "uvicorn"}, "uvicorn"),
        ({"X-Runtime": "0.021"}, "Puma"),
        ({"Server": "Phoenix"}, "Phoenix"),
        ({"Set-Cookie": "laravel_session=eyJpdiI6; path=/"}, "Laravel"),
        ({"Server": "nginx/1.25"}, None),
    ],
    ids=[
        "vite",
        "next",
        "nuxt",
        "astro",
        "webpack",
        "django",
        "flask",
        "fastapi",
        "rails",
        "phoenix",
        "laravel",
        "plain-nginx",
    ],
)
def test_dev_servers_recognised_in_the_response_headers(
    headers: dict[str, str], expected: str | None
) -> None:
    lines = "\n".join(f"{name}: {value}" for name, value in headers.items())

    assert detect_web_signal(lines) == expected


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('<script type="module" src="/@vite/client"></script>', "Vite"),
        ('<script src="/_next/static/chunks/main.js"></script>', "Next.js"),
        ('<link rel="modulepreload" href="/_nuxt/entry.js">', "Nuxt"),
        ("<astro-island uid='1'></astro-island>", "Astro"),
        ('<app-root ng-version="17.0.2"></app-root>', "Angular CLI"),
        ('<link href="/_app/immutable/assets/0.css">', "SvelteKit"),
        ('<script src="/static/js/react-refresh.js"></script>', "Create React App"),
        ("<script>__parcel_hmr_reload__()</script>", "Parcel"),
        ('<script src="/__webpack_hmr"></script>', "webpack dev server"),
        ("<p>docs at djangoproject.com</p>", "Django runserver"),
        ("<title>werkzeug debugger</title>", "Werkzeug"),
        ("<p>phx- socket at /phoenix/live_reload/socket</p>", "Phoenix"),
        ('<input name="laravel_token">', "Laravel"),
        ('<script>const url = "/openapi.json"</script>', "OpenAPI docs"),
        ("<h1>Just a page</h1>", None),
    ],
    ids=[
        "vite",
        "next",
        "nuxt",
        "astro",
        "angular",
        "sveltekit",
        "create-react-app",
        "parcel",
        "webpack",
        "django",
        "flask",
        "phoenix",
        "laravel",
        "openapi",
        "plain-html",
    ],
)
def test_dev_servers_recognised_in_an_html_body(body: str, expected: str | None) -> None:
    assert detect_web_signal("", body) == expected


def test_a_header_signal_beats_a_body_signal() -> None:
    assert detect_web_signal("Server: uvicorn", '<script src="/@vite/client">') == "uvicorn"


@pytest.mark.parametrize(
    ("content_type", "expected"),
    [
        ("text/html; charset=utf-8", ProbeKind.HTML),
        ("application/xhtml+xml", ProbeKind.HTML),
        ("application/json", ProbeKind.JSON),
        ("application/problem+json", ProbeKind.JSON),
        ("text/plain", ProbeKind.TEXT),
        ("image/png", ProbeKind.OTHER),
        (None, ProbeKind.OTHER),
    ],
    ids=["html", "xhtml", "json", "problem-json", "text", "image", "missing"],
)
def test_probe_kind_from_the_content_type(content_type: str | None, expected: ProbeKind) -> None:
    assert probe_kind(content_type) == expected


@asynccontextmanager
async def serve(handler: Callable[[web.Request], object]) -> AsyncIterator[int]:
    """A throwaway loopback server answering every method on ``/`` with ``handler``."""
    app = web.Application()
    app.router.add_route("*", "/", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = free_port()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    try:
        yield port
    finally:
        await runner.cleanup()


async def test_an_html_page_costs_one_extra_get_and_names_the_dev_server() -> None:
    requests: list[str] = []

    async def page(request: web.Request) -> web.Response:
        requests.append(request.method)
        return web.Response(
            text='<html><script type="module" src="/@vite/client"></script></html>',
            content_type="text/html",
        )

    async with serve(page) as port:
        listener = make_listener(port=port, processes=[make_process(addresses=["127.0.0.1"])])
        await HttpProber(interval=100.0).enrich([listener])

    assert listener.http.kind == ProbeKind.HTML
    assert listener.http.content_type == "text/html; charset=utf-8"
    assert listener.http.dev_server == "Vite"
    assert requests == ["HEAD", "GET"]


async def test_a_json_answer_is_classified_without_reading_a_body() -> None:
    requests: list[str] = []

    async def api(request: web.Request) -> web.Response:
        requests.append(request.method)
        return web.json_response({"ok": True})

    async with serve(api) as port:
        listener = make_listener(port=port, processes=[make_process(addresses=["127.0.0.1"])])
        await HttpProber(interval=100.0).enrich([listener])

    assert listener.http.kind == ProbeKind.JSON
    assert listener.http.dev_server is None
    assert requests == ["HEAD"]


async def test_a_dev_server_in_the_headers_needs_no_body_at_all() -> None:
    requests: list[str] = []

    async def page(request: web.Request) -> web.Response:
        requests.append(request.method)
        return web.Response(
            text="<html></html>", content_type="text/html", headers={"Server": "uvicorn"}
        )

    async with serve(page) as port:
        listener = make_listener(port=port, processes=[make_process(addresses=["127.0.0.1"])])
        await HttpProber(interval=100.0).enrich([listener])

    assert listener.http.dev_server == "uvicorn"
    assert requests == ["HEAD"]


async def test_only_the_first_64_kb_of_a_page_is_read() -> None:
    async def huge(request: web.Request) -> web.Response:
        filler = "<p>padding</p>" * 6000  # well past the 64 KB cap
        return web.Response(
            text=f"<html>{filler}<script src='/@vite/client'></script></html>",
            content_type="text/html",
        )

    async with serve(huge) as port:
        listener = make_listener(port=port, processes=[make_process(addresses=["127.0.0.1"])])
        await HttpProber(interval=100.0).enrich([listener])

    assert listener.http.kind == ProbeKind.HTML
    assert listener.http.dev_server is None


async def test_a_server_that_rejects_head_is_read_from_the_one_get_it_allows() -> None:
    requests: list[str] = []

    async def picky(request: web.Request) -> web.Response:
        requests.append(request.method)
        if request.method == "HEAD":
            return web.Response(status=405)
        return web.Response(
            text='<html><script src="/_next/static/chunk.js"></script></html>',
            content_type="text/html",
        )

    async with serve(picky) as port:
        listener = make_listener(port=port, processes=[make_process(addresses=["127.0.0.1"])])
        await HttpProber(interval=100.0).enrich([listener])

    assert listener.http.status == 200
    assert listener.http.dev_server == "Next.js"
    assert requests == ["HEAD", "GET"]
