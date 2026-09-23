"""Local reverse-proxy routes: which host names nginx / httpd serve and where they go.

Discovery asks the servers themselves for their effective configuration
(``nginx -T``, ``httpd -S``): commands, not directory scans.  For httpd the
``ProxyPass`` lines are read from the main config file the binary names and
the files ``httpd -S`` points at.  Runs at start and when asked (``r`` in the
who-talks-to-whom screen, ``lirts routes``).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

from lirts.collectors.proxies_httpd import (
    httpd_routes_from_files,
    parse_httpd_config,
    parse_httpd_dump,
)
from lirts.collectors.proxies_nginx import parse_nginx
from lirts.collectors.proxies_route import LOOPBACK_HOSTS, ProxyKind, Route
from lirts.constants import PROXY_COMMAND_TIMEOUT

__all__ = [
    "LOOPBACK_HOSTS",
    "ProxyKind",
    "Route",
    "discover_routes",
    "httpd_routes_from_files",
    "parse_httpd_config",
    "parse_httpd_dump",
    "parse_nginx",
]


# ----- discovery -----------------------------------------------------------------------


def _run(cmd: list[str], timeout: float) -> tuple[bool, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return False, ""
    return proc.returncode == 0, proc.stdout + "\n" + proc.stderr


def discover_routes(timeout: float = PROXY_COMMAND_TIMEOUT) -> tuple[list[Route], list[str]]:
    """Routes of every local nginx / httpd found on PATH, plus notes about what was asked."""
    routes: list[Route] = []
    notes: list[str] = []
    nginx = shutil.which("nginx")
    if nginx:
        ok, out = _run([nginx, "-T"], timeout)
        if ok:
            nginx_routes = parse_nginx(out)
            routes.extend(nginx_routes)
            notes.append(f"nginx -T: {len(nginx_routes)} route(s)")
        else:
            notes.append("nginx -T failed (config error or permissions)")
    httpd = shutil.which("httpd") or shutil.which("apache2ctl") or shutil.which("apachectl")
    if httpd:
        ok, out = _run([httpd, "-S"], timeout)
        if ok:
            dump_routes, main_root, files = parse_httpd_dump(out)
            _, version = _run([httpd, "-V"], timeout)
            root_dir = None
            main_config = None
            for line in version.splitlines():
                m = re.search(r'HTTPD_ROOT="([^"]+)"', line)
                if m:
                    root_dir = m.group(1)
                m = re.search(r'SERVER_CONFIG_FILE="([^"]+)"', line)
                if m:
                    main_config = m.group(1)
            found: list[Route] = []
            if main_config:
                found = httpd_routes_from_files(main_config, extra_files=files, root_dir=root_dir)
            if not found:
                found = dump_routes
                for r in found:
                    r.doc_root = r.doc_root or main_root
            routes.extend(found)
            notes.append(f"{os.path.basename(httpd)} -S: {len(found)} route(s)")
        else:
            notes.append(f"{os.path.basename(httpd)} -S failed (config error or permissions)")
    if not nginx and not httpd:
        notes.append("no nginx or httpd on PATH")
    return routes, notes
