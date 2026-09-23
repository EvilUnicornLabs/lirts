"""Parsing of ``httpd -S`` output and httpd configuration files into routes."""

from __future__ import annotations

import glob
import os
import re
from pathlib import Path

from lirts.collectors.proxies_nginx import _port_of, _upstream_target
from lirts.collectors.proxies_route import ProxyKind, Route

_VHOST_LINE = re.compile(
    r"^\s*(?:port (\d+) namevhost|default server|(\*|\S+?):(\d+)\s+is a NameVirtualHost)"
)
_NAMEVHOST = re.compile(r"^\s*port (\d+) namevhost (\S+)\s*\((\S+?):\d+\)")
_DEFAULT_SERVER = re.compile(r"^\s*default server (\S+)\s*\((\S+?):\d+\)")
_ALIAS = re.compile(r"^\s*alias (\S+)")
_DOCROOT = re.compile(r'^Main DocumentRoot:\s*"?([^"]+)"?')


def parse_httpd_dump(text: str) -> tuple[list[Route], str | None, list[str]]:
    """Routes, main DocumentRoot and referenced config files from ``httpd -S`` output."""

    routes: list[Route] = []
    files: list[str] = []
    doc_root: str | None = None
    current_port: int | None = None
    last: Route | None = None
    for line in text.splitlines():
        m = _DOCROOT.match(line)
        if m:
            doc_root = m.group(1).strip()
            continue
        m = re.match(r"^\s*(\*|\S+?):(\d+)\s+is a NameVirtualHost", line)
        if m:
            current_port = int(m.group(2))
            continue
        m = _NAMEVHOST.match(line)
        if m:
            port, name, cfg = int(m.group(1)), m.group(2), m.group(3)
            last = Route(ProxyKind.HTTPD, port, [name], config=cfg)
            routes.append(last)
            if cfg not in files:
                files.append(cfg)
            continue
        m = _DEFAULT_SERVER.match(line)
        if m and current_port is not None:
            name, cfg = m.group(1), m.group(2)
            last = Route(ProxyKind.HTTPD, current_port, [name], config=cfg)
            routes.append(last)
            if cfg not in files:
                files.append(cfg)
            continue
        m = _ALIAS.match(line)
        if m and last is not None:
            last.names.append(m.group(1))
    return routes, doc_root, files


_LISTEN = re.compile(r"^\s*Listen\s+(\S+)", re.I)
_VHOST_OPEN = re.compile(r"^\s*<VirtualHost\s+([^>]+)>", re.I)
_VHOST_CLOSE = re.compile(r"^\s*</VirtualHost>", re.I)
_SERVERNAME = re.compile(r"^\s*Server(Name|Alias)\s+(.+)", re.I)
_PROXYPASS = re.compile(r"^\s*ProxyPass(?:Match)?\s+(\S+)\s+(\S+)", re.I)
_DOCUMENTROOT = re.compile(r'^\s*DocumentRoot\s+"?([^"\s]+)"?', re.I)
_INCLUDE = re.compile(r"^\s*Include(?:Optional)?\s+\"?([^\"\s]+)\"?", re.I)


def parse_httpd_config(
    text: str,
) -> tuple[list[int], list[dict], list[tuple[str, str]], str | None, list[str]]:
    """(listen ports, vhosts, main ProxyPass pairs, main DocumentRoot, includes) from httpd config text."""
    listen: list[int] = []
    vhosts: list[dict] = []
    main_passes: list[tuple[str, str]] = []
    main_root: str | None = None
    includes: list[str] = []
    current: dict | None = None
    for line in text.splitlines():
        if _VHOST_CLOSE.match(line):
            current = None
            continue
        m = _VHOST_OPEN.match(line)
        if m:
            ports = [p for p in (_port_of(a) for a in m.group(1).split()) if p is not None]
            current = {"ports": ports, "names": [], "passes": [], "root": None}
            vhosts.append(current)
            continue
        m = _INCLUDE.match(line)
        if m:
            includes.append(m.group(1))
            continue
        m = _LISTEN.match(line)
        if m and current is None:
            port = _port_of(m.group(1))
            if port is not None and port not in listen:
                listen.append(port)
            continue
        m = _SERVERNAME.match(line)
        if m and current is not None:
            current["names"].extend(n.split(":")[0] for n in m.group(2).split())
            continue
        m = _PROXYPASS.match(line)
        if m:
            pair = (m.group(1), m.group(2))
            if current is not None:
                current["passes"].append(pair)
            else:
                main_passes.append(pair)
            continue
        m = _DOCUMENTROOT.match(line)
        if m:
            if current is not None:
                current["root"] = m.group(1)
            else:
                main_root = m.group(1)
    return listen, vhosts, main_passes, main_root, includes


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def httpd_routes_from_files(
    main_config: str, *, extra_files: list[str], root_dir: str | None
) -> list[Route]:
    """Routes from the main httpd config plus the files it includes (one level) and ``extra_files``."""

    texts: list[str] = []
    seen: set[str] = set()
    queue = [main_config, *extra_files]
    while queue:
        path = queue.pop(0)
        if not os.path.isabs(path) and root_dir:
            path = os.path.join(root_dir, path)
        if path in seen:
            continue
        seen.add(path)
        text = _read(path)
        if not text:
            continue
        texts.append(text)
        if path == main_config or path == os.path.join(root_dir or "", main_config):
            _, _, _, _, includes = parse_httpd_config(text)
            for inc in includes:
                pattern = inc if os.path.isabs(inc) else os.path.join(root_dir or "", inc)
                queue.extend(sorted(glob.glob(pattern)))
    if not texts:
        return []
    listen: list[int] = []
    vhosts: list[dict] = []
    main_passes: list[tuple[str, str]] = []
    main_root: str | None = None
    for text in texts:
        ports, found_vhosts, passes, root, _ = parse_httpd_config(text)
        listen.extend(p for p in ports if p not in listen)
        vhosts.extend(found_vhosts)
        main_passes.extend(passes)
        main_root = main_root or root
    routes: list[Route] = []
    for vh in vhosts:
        for port in vh["ports"] or listen or [80]:
            if vh["passes"]:
                for path, url in vh["passes"]:
                    target, local = _upstream_target(url, {})
                    routes.append(
                        Route(
                            ProxyKind.HTTPD,
                            port,
                            list(vh["names"]),
                            target,
                            local,
                            path,
                            vh["root"],
                        )
                    )
            else:
                routes.append(
                    Route(ProxyKind.HTTPD, port, list(vh["names"]), None, None, "/", vh["root"])
                )
    if not vhosts:
        for port in listen or [80]:
            if main_passes:
                for path, url in main_passes:
                    target, local = _upstream_target(url, {})
                    routes.append(Route(ProxyKind.HTTPD, port, [], target, local, path, main_root))
            else:
                routes.append(Route(ProxyKind.HTTPD, port, [], None, None, "/", main_root))
    return routes
