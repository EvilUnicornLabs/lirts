"""Parsing of nginx configuration text into :class:`~lirts.collectors.proxies.Route` objects."""

from __future__ import annotations

import re
from collections.abc import Iterator
from enum import StrEnum
from typing import Any

from lirts.collectors.proxies_route import LOOPBACK_HOSTS, ProxyKind, Route


class _Token(StrEnum):
    """What the nginx tokenizer just read."""

    DIRECTIVE = "directive"
    OPEN = "open"
    CLOSE = "close"


def _nginx_tokens(text: str) -> Iterator[tuple[_Token, list[str]]]:
    """Yield the directives and block boundaries of nginx config text, in order."""
    words: list[str] = []
    buf = ""
    quote: str | None = None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
            else:
                buf += ch
        elif ch in "'\"":
            quote = ch
        elif ch == "#":
            while i < len(text) and text[i] != "\n":
                i += 1
        elif ch.isspace():
            if buf:
                words.append(buf)
                buf = ""
        elif ch == ";":
            if buf:
                words.append(buf)
                buf = ""
            if words:
                yield _Token.DIRECTIVE, words
            words = []
        elif ch == "{":
            if buf:
                words.append(buf)
                buf = ""
            yield _Token.OPEN, words
            words = []
        elif ch == "}":
            if buf:
                words.append(buf)
                buf = ""
            if words:
                yield _Token.DIRECTIVE, words
            words = []
            yield _Token.CLOSE, []
        else:
            buf += ch
        i += 1


def _port_of(spec: str) -> int | None:
    """Port from a listen / server spec such as 80, 127.0.0.1:8080, [::]:443, host:3000."""
    spec = spec.strip().rstrip("/")
    if spec.startswith("unix:"):
        return None
    if spec.isdigit():
        return int(spec)
    m = re.search(r":(\d+)$", spec)
    if m:
        return int(m.group(1))
    return None


def _upstream_target(url: str, upstreams: dict[str, str]) -> tuple[str, int | None]:
    """Resolve a proxy_pass / ProxyPass target to (text, local port or None)."""

    target = url.strip()
    scheme, _, rest = target.partition("://")
    if not rest:
        rest, scheme = target, ""
    hostport = rest.split("/", 1)[0]
    if hostport in upstreams:
        hostport = upstreams[hostport]
    host = (
        hostport.rsplit(":", 1)[0] if ":" in hostport and not hostport.startswith("[") else hostport
    )
    if hostport.startswith("["):
        host = hostport.split("]")[0] + "]"
    port = _port_of(hostport)
    if port is None and host:
        port = 443 if scheme == "https" else 80
    local = port if host in LOOPBACK_HOSTS else None
    return hostport, local


def parse_nginx(text: str) -> list[Route]:
    """Routes from ``nginx -T`` output (or any nginx config text)."""

    upstreams: dict[str, str] = {}
    servers: list[dict] = []
    stack: list[dict] = []
    current_upstream: str | None = None
    for kind, words in _nginx_tokens(text):
        if kind == _Token.OPEN:
            block: dict[str, Any] = {"kind": words[0] if words else "", "args": words[1:]}
            if block["kind"] == "server" and not any(b["kind"] == "upstream" for b in stack):
                block.update({"listen": [], "names": [], "passes": [], "root": None})
                servers.append(block)
            if block["kind"] == "upstream" and words[1:]:
                current_upstream = words[1]
            stack.append(block)
        elif kind == _Token.CLOSE:
            if stack:
                closed = stack.pop()
                if closed["kind"] == "upstream":
                    current_upstream = None
        else:
            name, args = words[0], words[1:]
            server = next((b for b in reversed(stack) if b["kind"] == "server"), None)
            if current_upstream and name == "server" and args:
                upstreams.setdefault(current_upstream, args[0])
            elif server is not None:
                if name == "listen" and args:
                    port = _port_of(args[0])
                    if port is not None and port not in server["listen"]:
                        server["listen"].append(port)
                elif name == "server_name":
                    server["names"].extend(a for a in args if a not in ("_", "''", '""'))
                elif name == "proxy_pass" and args:
                    location = next((b for b in reversed(stack) if b["kind"] == "location"), None)
                    path = location["args"][-1] if location and location["args"] else "/"
                    server["passes"].append((path, args[0]))
                elif name == "root" and args and server["root"] is None:
                    server["root"] = args[0]
    routes: list[Route] = []
    for server in servers:
        ports = server["listen"] or [80]
        for port in ports:
            if server["passes"]:
                for path, url in server["passes"]:
                    text_target, local = _upstream_target(url, upstreams)
                    routes.append(
                        Route(
                            ProxyKind.NGINX,
                            port,
                            list(server["names"]),
                            text_target,
                            local,
                            path,
                            server["root"],
                        )
                    )
            else:
                routes.append(
                    Route(
                        ProxyKind.NGINX,
                        port,
                        list(server["names"]),
                        None,
                        None,
                        "/",
                        server["root"],
                    )
                )
    return routes
