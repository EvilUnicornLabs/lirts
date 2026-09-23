from __future__ import annotations

import subprocess
from pathlib import Path

from lirts.collectors import proxies
from lirts.collectors.proxies import (
    Route,
    discover_routes,
    httpd_routes_from_files,
    parse_httpd_config,
    parse_httpd_dump,
    parse_nginx,
)

NGINX_T = """
# configuration file /etc/nginx/nginx.conf:
http {
    upstream backend { server 127.0.0.1:8000; }
    server {
        listen 80;
        listen [::]:80;
        server_name shop.local www.shop.local;   # comment
        location / { proxy_pass http://127.0.0.1:5173; }
        location /api/ { proxy_pass http://backend; }
    }
    server {
        listen 127.0.0.1:8443 ssl;
        server_name static.local;
        root /srv/static;
    }
    server {
        listen 9000;
        location / { proxy_pass https://example.com; }
    }
}
"""

HTTPD_S = """VirtualHost configuration:
*:8080                 is a NameVirtualHost
         default server shop.local (/nonexistent/lirts-test/httpd-vhosts.conf:23)
         port 8080 namevhost shop.local (/nonexistent/lirts-test/httpd-vhosts.conf:23)
                 alias www.shop.local
         port 8080 namevhost api.local (/nonexistent/lirts-test/httpd-vhosts.conf:40)
ServerRoot: "/opt/homebrew/opt/httpd"
Main DocumentRoot: "/srv/www"
"""


def test_parse_nginx() -> None:
    routes = parse_nginx(NGINX_T)
    desc = [r.describe() for r in routes]
    assert "nginx :80 shop.local, www.shop.local → 127.0.0.1:5173" in desc
    assert "nginx :80 shop.local, www.shop.local /api/ → 127.0.0.1:8000" in desc
    assert "nginx :8443 static.local → files in /srv/static" in desc
    remote = next(r for r in routes if r.listen_port == 9000)
    assert remote.upstream == "example.com" and remote.upstream_port is None
    assert remote.describe() == "nginx :9000 (default server) → example.com"
    assert len([r for r in routes if r.listen_port == 80]) == 2  # both listen lines share port 80


def test_parse_httpd_dump_and_files(tmp_path: Path) -> None:
    routes, doc_root, files = parse_httpd_dump(HTTPD_S)
    assert doc_root == "/srv/www" and files == ["/nonexistent/lirts-test/httpd-vhosts.conf"]
    assert [r.names for r in routes] == [
        ["shop.local"],
        ["shop.local", "www.shop.local"],
        ["api.local"],
    ]
    # ProxyPass lines come from the config files
    main = tmp_path / "httpd.conf"
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "vhosts.conf").write_text(
        "<VirtualHost *:8080>\n  ServerName shop.local\n  ServerAlias www.shop.local\n"
        "  ProxyPass / http://localhost:5173/\n</VirtualHost>\n"
        "<VirtualHost *:8080>\n  ServerName api.local\n  ProxyPass /v1 http://127.0.0.1:8000/v1\n"
        '  DocumentRoot "/srv/api"\n</VirtualHost>\n'
    )
    main.write_text('Listen 8080\nDocumentRoot "/srv/www"\nInclude extra/*.conf\n')
    routes = httpd_routes_from_files(str(main), extra_files=[], root_dir=str(tmp_path))
    desc = [r.describe() for r in routes]
    assert "httpd :8080 shop.local, www.shop.local → 127.0.0.1:5173" in desc
    assert "httpd :8080 api.local /v1 → 127.0.0.1:8000" in desc
    # no vhosts at all: the main server is the route
    main.write_text('Listen 8080\nDocumentRoot "/srv/www"\n')
    only = httpd_routes_from_files(str(main), extra_files=[], root_dir=str(tmp_path))
    assert [r.describe() for r in only] == ["httpd :8080 (default server) → files in /srv/www"]


def test_discover_routes_uses_commands_only(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_which(name):
        return {"nginx": "/usr/sbin/nginx", "httpd": "/usr/sbin/httpd"}.get(name)

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[1] == "-T":
            return subprocess.CompletedProcess(cmd, 0, NGINX_T, "")
        if cmd[1] == "-S":
            return subprocess.CompletedProcess(cmd, 0, HTTPD_S, "")
        if cmd[1] == "-V":
            return subprocess.CompletedProcess(
                cmd,
                0,
                f' -D HTTPD_ROOT="{tmp_path}"\n -D SERVER_CONFIG_FILE="{tmp_path}/httpd.conf"\n',
                "",
            )
        return subprocess.CompletedProcess(cmd, 1, "", "")

    monkeypatch.setattr(proxies.shutil, "which", fake_which)
    monkeypatch.setattr(proxies.subprocess, "run", fake_run)
    (tmp_path / "httpd.conf").write_text("Listen 8080\nProxyPass / http://127.0.0.1:3000/\n")
    routes, notes = discover_routes()
    assert [c[1] for c in calls] == ["-T", "-S", "-V"]
    assert any(r.source == "nginx" for r in routes) and any(r.source == "httpd" for r in routes)
    httpd = [r for r in routes if r.source == "httpd"]
    assert httpd[0].upstream_port == 3000 and httpd[0].listen_port == 8080
    assert notes == ["nginx -T: 4 route(s)", "httpd -S: 1 route(s)"]


def test_routes_attach_to_listeners(tmp_path: Path) -> None:
    from lirts.config import DEFAULT_CONFIG
    from lirts.engine import Engine
    from lirts.models import Identity
    from tests.conftest import make_listener

    engine = Engine(dict(DEFAULT_CONFIG), state_root=tmp_path)
    engine.routes = [Route("nginx", 80, ["shop.local"], "127.0.0.1:5173", 5173)]
    engine._routes_loaded = True
    proxy = make_listener(port=80, name="nginx", identity=Identity("nginx", "proxy", 0.9))
    app_ = make_listener(
        port=5173, name="node", identity=Identity("Vite dev server", "frontend", 0.9)
    )
    engine._apply_routes([proxy, app_])
    assert proxy.proxy_targets == [5173] and proxy.hosts == ["shop.local"]
    assert app_.hosts == ["shop.local"] and proxy.proxy_chain and "5173" in proxy.proxy_chain
    engine.close()


HTTPD_CONF = """
Listen 8080
DocumentRoot "/var/www"
ProxyPass /api/ http://127.0.0.1:8000/
Include other.conf
<VirtualHost *:9090>
    ServerName blog.local
    ServerAlias www.blog.local
    DocumentRoot "/srv/blog"
    ProxyPass /app/ http://127.0.0.1:3000/
</VirtualHost>
"""


def test_parse_httpd_config_separates_the_main_server_from_the_virtual_hosts() -> None:
    listen, vhosts, main_passes, main_root, includes = parse_httpd_config(HTTPD_CONF)

    assert listen == [8080]
    assert main_root == "/var/www"
    assert main_passes == [("/api/", "http://127.0.0.1:8000/")]
    assert includes == ["other.conf"]
    assert len(vhosts) == 1
    assert vhosts[0]["ports"] == [9090]
    assert vhosts[0]["names"] == ["blog.local", "www.blog.local"]
    assert vhosts[0]["root"] == "/srv/blog"
    assert vhosts[0]["passes"] == [("/app/", "http://127.0.0.1:3000/")]
