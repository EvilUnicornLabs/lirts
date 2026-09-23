"""What a web response and a compose service name reveal about a listener.

Data plus the two pure functions that read it: :func:`probe_kind` classifies a
``Content-Type`` header and :func:`detect_web_signal` names the framework behind
a response from its headers and, for HTML, the first bytes of its body.
:mod:`lirts.identity` turns both into candidates; :mod:`lirts.collectors.http`
fills the probe fields.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from lirts.identity_rules import (
    ROLE_BACKEND,
    ROLE_CACHE,
    ROLE_DB,
    ROLE_FRONTEND,
    ROLE_PROXY,
    ROLE_QUEUE,
    ROLE_TOOL,
)


class ProbeKind(StrEnum):
    """What the probed port answered with, as far as its content type says."""

    HTML = "html"
    JSON = "json"
    TEXT = "text"
    OTHER = "other"


_HTML_TYPES = {"text/html", "application/xhtml+xml"}


def probe_kind(content_type: str | None) -> ProbeKind:
    """The kind of a response from its ``Content-Type`` header."""
    if not content_type:
        return ProbeKind.OTHER
    media = content_type.split(";", 1)[0].strip().lower()
    if media in _HTML_TYPES:
        return ProbeKind.HTML
    if media.endswith("json"):
        return ProbeKind.JSON
    if media.startswith("text/"):
        return ProbeKind.TEXT
    return ProbeKind.OTHER


@dataclass(frozen=True, slots=True)
class WebSignal:
    """One framework the probe can recognise, and what it says about the row.

    ``service`` is the name identity gives the row; an empty one means the
    signal only argues for a role, leaving the naming to a stronger signal.
    ``header`` matches the ``name: value`` lines of the response, ``body`` the
    first bytes of an HTML body; an empty pattern is never tried.
    """

    label: str
    service: str
    role: str
    confidence: float
    header: str
    body: str


# Hot-reloading dev servers and the frameworks behind them, most specific first.
WEB_SIGNALS: list[WebSignal] = [
    WebSignal("Vite", "Vite dev server", ROLE_FRONTEND, 0.9, r"\bvite\b", r"/@vite/"),
    WebSignal("Next.js", "Next.js dev server", ROLE_FRONTEND, 0.9, r"next\.js", r"/_next/"),
    WebSignal("Nuxt", "Nuxt dev server", ROLE_FRONTEND, 0.9, r"\bnuxt\b", r"/_nuxt/"),
    WebSignal(
        "Astro", "Astro dev server", ROLE_FRONTEND, 0.9, r"\bastro\b", r"astro-island|/@astro"
    ),
    WebSignal(
        "Angular CLI", "Angular dev server", ROLE_FRONTEND, 0.9, r"", r"ng-version|/@angular"
    ),
    WebSignal("SvelteKit", "SvelteKit dev server", ROLE_FRONTEND, 0.9, r"", r"/_app/"),
    WebSignal("Create React App", "React dev server", ROLE_FRONTEND, 0.9, r"", r"react-refresh"),
    WebSignal(
        "Parcel",
        "Parcel dev server",
        ROLE_FRONTEND,
        0.9,
        r"",
        r"__parcel|parcel-hmr|parcel-require",
    ),
    WebSignal(
        "webpack dev server",
        "Webpack dev server",
        ROLE_FRONTEND,
        0.9,
        r"webpack-dev-server",
        r"__webpack_hmr|sockjs-node|webpack-dev-server",
    ),
    WebSignal(
        "Django runserver",
        "Django runserver",
        ROLE_BACKEND,
        0.9,
        r"wsgiserver",
        r"djangoproject\.com|csrfmiddlewaretoken|DisallowedHost",
    ),
    WebSignal("Werkzeug", "Flask dev server", ROLE_BACKEND, 0.9, r"werkzeug", r"\bwerkzeug\b"),
    WebSignal("uvicorn", "FastAPI (uvicorn)", ROLE_BACKEND, 0.9, r"\buvicorn\b", r""),
    WebSignal("Puma", "Rails (Puma)", ROLE_BACKEND, 0.9, r"x-runtime|\bpuma\b", r""),
    WebSignal(
        "Phoenix", "Phoenix (Elixir)", ROLE_BACKEND, 0.9, r"\bphoenix\b", r"/phoenix/live_reload"
    ),
    WebSignal(
        "Laravel", "Laravel dev server", ROLE_BACKEND, 0.9, r"laravel_session", r"laravel[_-]"
    ),
    # No framework named, but the page is an API's own documentation.
    WebSignal("OpenAPI docs", "", ROLE_BACKEND, 0.8, r"", r"swagger-ui|/openapi\.json|redoc"),
]

WEB_SIGNAL_BY_LABEL: dict[str, WebSignal] = {s.label: s for s in WEB_SIGNALS}

_compiled_header_signals = [(re.compile(s.header, re.I), s.label) for s in WEB_SIGNALS if s.header]
_compiled_body_signals = [(re.compile(s.body, re.I), s.label) for s in WEB_SIGNALS if s.body]


def detect_web_signal(headers: str, body: str = "") -> str | None:
    """The label of the first framework the headers, then the body, give away."""
    for pattern, label in _compiled_header_signals:
        if pattern.search(headers):
            return label
    if not body:
        return None
    for pattern, label in _compiled_body_signals:
        if pattern.search(body):
            return label
    return None


# (compose service / container name regex, role) — what a name alone suggests.
NAME_ROLE_RULES: list[tuple[str, str]] = [
    (
        r"postgres|pgsql|mysql|maria|mongo|database|\bdb\b|_db$|-db$|clickhouse|influx|elastic|search|minio|storage",
        ROLE_DB,
    ),
    (r"redis|valkey|cache|memcache", ROLE_CACHE),
    (r"nginx|proxy|gateway|ingress|traefik|caddy|haproxy|envoy|lb$", ROLE_PROXY),
    (
        r"queue|rabbit|kafka|nats|broker|mqtt|\bmq\b|celery|worker|scheduler|\bcron\b",
        ROLE_QUEUE,
    ),
    (
        r"mail|smtp|otel|collector|jaeger|grafana|prometheus|adminer|pgadmin|phpmyadmin|mailpit|mailhog|docs",
        ROLE_TOOL,
    ),
    (
        r"front|web|website|site|\bui\b|client|www|app$|spa|dashboard|admin|portal|storybook|vite|next|nuxt",
        ROLE_FRONTEND,
    ),
    (
        r"api|backend|back|server|service|svc|core|engine|gameloop|players|auth|graphql|rest|grpc|bff|cms|bot|sim",
        ROLE_BACKEND,
    ),
]

# (process-name regex, command-line regex or None, service, role, confidence).
# Tried before the general rules, so a framework is named before "Node.js app" is.
WEB_PROCESS_RULES: list[tuple[str, str | None, str, str, float]] = [
    (r"^node|^bun|^deno", r"\bparcel\b", "Parcel dev server", ROLE_FRONTEND, 0.9),
    (r"^node|^bun|^deno", r"\bfastify\b", "Fastify (Node.js)", ROLE_BACKEND, 0.85),
    (r"^node|^bun|^deno", r"\bkoa\b", "Koa (Node.js)", ROLE_BACKEND, 0.85),
    (r"^node|^bun|^deno", r"\bhapi\b", "hapi (Node.js)", ROLE_BACKEND, 0.85),
    (r"^python|^daphne", r"\bdaphne\b", "Daphne (ASGI)", ROLE_BACKEND, 0.9),
]
