from __future__ import annotations

import time

from lirts.config import DEFAULT_CONFIG
from lirts.config_view import ConfigView
from lirts.identity import (
    ROLE_BACKEND,
    ROLE_DB,
    ROLE_DOCKER,
    ROLE_FRONTEND,
    ROLE_PROXY,
    ROLE_SYSTEM,
    ROLE_TOOL,
)
from lirts.insights import (
    analyse,
    blast_radius,
    build_chain,
    classify_activity,
    detect_proxies,
    explain,
    format_duration,
    format_rate,
    infer_architecture,
    spark,
)
from lirts.models import (
    ACTIVITY_ACTIVE,
    ACTIVITY_HOT,
    ACTIVITY_IDLE,
    HttpProbe,
    Identity,
    Insight,
    Snapshot,
)
from tests.conftest import make_container, make_listener, make_process

CFG = ConfigView(DEFAULT_CONFIG)


def codes(lst):
    return {i.code: i for i in lst.insights}


def test_format_helpers() -> None:
    assert format_duration(None) == "-"
    assert format_duration(5) == "5s"
    assert format_duration(120) == "2m"
    assert format_duration(3600 * 3 + 60 * 5) == "3h 05m"
    assert format_duration(86400 * 2 + 3600) == "2d 1h"
    assert format_rate(None) == "-"
    assert format_rate(10) == "10 B/s"
    assert format_rate(2048) == "2.0 KB/s"
    assert format_rate(3 * 1024**2) == "3.0 MB/s"


def test_classify_activity() -> None:
    idle = make_listener(processes=[make_process(cpu_percent=0.0)])
    assert classify_activity(idle)[0] == ACTIVITY_IDLE
    active = make_listener(processes=[make_process(cpu_percent=5.0)])
    assert classify_activity(active)[0] == ACTIVITY_ACTIVE
    hot = make_listener(processes=[make_process(cpu_percent=60.0, inbound_connections=30)])
    assert classify_activity(hot)[0] == ACTIVITY_HOT
    busy_net = make_listener(bytes_in_rate=3 * 1024**2, bytes_out_rate=0.0)
    assert classify_activity(busy_net)[0] == ACTIVITY_HOT


def test_detect_proxies_and_chain() -> None:
    nginx = make_listener(
        port=443,
        name="nginx",
        processes=[
            make_process(pid=1, name="nginx", remote_conns=["127.0.0.1:9000", "10.0.0.1:5432"])
        ],
        identity=Identity("Nginx", ROLE_PROXY, 0.9),
    )
    php = make_listener(
        port=9000,
        name="php-fpm",
        processes=[make_process(pid=2, name="php-fpm", remote_conns=["127.0.0.1:5432"])],
        identity=Identity("PHP-FPM", ROLE_BACKEND, 0.9),
    )
    pg = make_listener(port=5432, name="postgres", identity=Identity("PostgreSQL", ROLE_DB, 0.9))
    detect_proxies([nginx, php, pg])
    assert nginx.proxy_targets == [9000]
    assert nginx.proxy_chain == "443 → Nginx → 9000 → PHP-FPM"
    assert php.proxy_targets == []  # not a proxy role


def test_conflict_between_local_and_docker() -> None:
    c = make_container(name="app-db-1", image="postgres:16", host_ports=[5432])
    lst = make_listener(
        port=5432,
        container=c,
        processes=[
            make_process(pid=1, name="postgres", addresses=["127.0.0.1"], age=100),
            make_process(pid=2, name="com.docker.backend", addresses=["0.0.0.0"], age=50),
        ],
    )
    analyse(lst, cfg=CFG)
    conflict = codes(lst)["port-conflict"]
    assert conflict.level == "error"
    assert "container app-db-1" in conflict.message
    assert "127.0.0.1" in conflict.message
    assert conflict.action == "kill:1"
    assert lst.status == "error"


def test_shared_port_info_and_zombie() -> None:
    lst = make_listener(
        processes=[
            make_process(pid=1, name="httpd", ppid=0),
            make_process(pid=2, name="httpd", ppid=1),
        ]
    )
    lst.shared_reason = "2 httpd workers sharing one socket (prefork/cluster)"
    analyse(lst, cfg=CFG)
    assert codes(lst)["shared-port"].level == "info"
    assert lst.status == "healthy"
    z = make_listener(processes=[make_process(pid=3, status="zombie")])
    analyse(z, cfg=CFG)
    assert codes(z)["zombie"].level == "error"


def test_container_health_and_restarts() -> None:
    c = make_container(health="unhealthy", restart_count=5)
    lst = make_listener(port=8080, container=c, processes=[make_process(name="com.docker.backend")])
    analyse(lst, cfg=CFG)
    assert "container-unhealthy" in codes(lst)
    assert "container-restarts" in codes(lst)
    lst.restarts_last_hour = 4
    analyse(lst, cfg=CFG)
    assert "restarts" in codes(lst)


def test_http_insights_only_for_web_roles() -> None:
    web = make_listener(
        identity=Identity("Vite dev server", ROLE_FRONTEND, 0.9),
        http=HttpProbe(attempted=True, ok=False, error="connection refused"),
    )
    analyse(web, cfg=CFG)
    assert "unreachable" in codes(web)
    tool = make_listener(
        identity=Identity("Cursor", ROLE_TOOL, 0.9),
        http=HttpProbe(attempted=True, ok=False, error="timeout"),
    )
    analyse(tool, cfg=CFG)
    assert "unreachable" not in codes(tool)
    not_http = make_listener(
        identity=Identity("API", ROLE_BACKEND, 0.9),
        http=HttpProbe(attempted=True, ok=False, error="not HTTP"),
    )
    analyse(not_http, cfg=CFG)
    assert "unreachable" not in codes(not_http)
    failing = make_listener(
        identity=Identity("API", ROLE_BACKEND, 0.9),
        http=HttpProbe(attempted=True, ok=True, status=503, latency_ms=2000),
    )
    analyse(failing, cfg=CFG)
    assert "http-5xx" in codes(failing)
    assert codes(failing)["latency"].level == "warning"


def test_resource_thresholds() -> None:
    lst = make_listener(processes=[make_process(cpu_percent=90.0, memory_mb=600.0)])
    analyse(lst, cfg=CFG)
    assert codes(lst)["cpu-high"].level == "warning"
    assert codes(lst)["mem-elevated"].level == "info"


def test_stale_detection() -> None:
    old = make_listener(
        identity=Identity("Vite dev server", ROLE_FRONTEND, 0.95),
        processes=[make_process(age=8 * 3600, cpu_percent=0.0)],
    )
    old.activity = ACTIVITY_IDLE
    analyse(old, cfg=CFG)
    assert codes(old)["stale"].action == f"kill:{old.pid}"
    fresh = make_listener(
        identity=Identity("Vite dev server", ROLE_FRONTEND, 0.95), processes=[make_process(age=60)]
    )
    analyse(fresh, cfg=CFG)
    assert "stale" not in codes(fresh)
    app = make_listener(
        identity=Identity("Cursor", ROLE_TOOL, 0.9), processes=[make_process(age=8 * 3600)]
    )
    analyse(app, cfg=CFG)
    assert "stale" not in codes(app)
    recently_active = make_listener(
        identity=Identity("API", ROLE_BACKEND, 0.9), processes=[make_process(age=8 * 3600)]
    )
    recently_active.last_active = time.time() - 60
    analyse(recently_active, cfg=CFG)
    assert "stale" not in codes(recently_active)


def test_blast_radius() -> None:
    backend = make_listener(
        port=9000,
        processes=[make_process(pid=5, name="php-fpm", inbound_connections=3)],
        identity=Identity("PHP-FPM", ROLE_BACKEND, 0.9),
    )
    proxy = make_listener(
        port=443,
        processes=[make_process(pid=6, name="nginx")],
        identity=Identity("Nginx", ROLE_PROXY, 0.9),
    )
    proxy.proxy_targets = [9000]
    other = make_listener(port=9001, processes=[make_process(pid=5, name="php-fpm")])
    snap = Snapshot(listeners=[backend, proxy, other])
    impacts = blast_radius(backend, snap)
    assert any("3 active connection" in i for i in impacts)
    assert any("also serves port(s) 9001" in i for i in impacts)
    assert any("Nginx on 443 proxies" in i for i in impacts)
    proxy_impacts = blast_radius(proxy, snap)
    assert any("backends on 9000" in i for i in proxy_impacts)
    docker_proxy = make_listener(
        port=6379,
        processes=[make_process(pid=7, name="com.docker.backend")],
        identity=Identity("Redis", ROLE_DOCKER, 0.9),
    )
    assert any(
        "Docker port proxy" in i
        for i in blast_radius(docker_proxy, Snapshot(listeners=[docker_proxy]))
    )
    empty = make_listener(port=1, processes=[], container=make_container())
    assert "use stop (x)" in blast_radius(empty, Snapshot(listeners=[empty]))[0]


def test_explain_and_architecture() -> None:
    web = make_listener(
        port=5173, identity=Identity("Vite dev server", ROLE_FRONTEND, 0.9, project="shop")
    )
    api = make_listener(port=8000, identity=Identity("FastAPI", ROLE_BACKEND, 0.9))
    db = make_listener(port=5432, identity=Identity("PostgreSQL", ROLE_DB, 0.9))
    proxy = make_listener(port=443, identity=Identity("Nginx", ROLE_PROXY, 0.9))
    proxy.proxy_chain = "443 → Nginx → 8000 → FastAPI"
    sysd = make_listener(port=5353, identity=Identity("mDNS", ROLE_SYSTEM, 0.9))
    api.insights.append(
        __import__("lirts.models", fromlist=["Insight"]).Insight(
            "warning", "stale", "No activity for 7h", "kill it", "kill:1"
        )
    )
    snap = Snapshot(listeners=[web, api, db, proxy, sysd])
    arch = infer_architecture(snap)
    assert arch is not None and "Vite dev server (frontend)" in arch and "fronted by Nginx" in arch
    text = explain(snap, {"missing": ["Redis on 6379/TCP"], "added": [], "changed": []})
    assert "5 TCP listener(s)" in text
    assert "Vite dev server on 5173 [shop]" in text
    assert "Proxy chains" in text
    assert "Warnings (1)" in text and "kill it" in text
    assert "missing: Redis on 6379/TCP" in text
    assert "system service" in text
    assert "No warnings" in explain(Snapshot(listeners=[web]))
    assert infer_architecture(Snapshot()) is None


def test_event_kinds_and_keys() -> None:
    from lirts.models import Event

    assert Event(1, "info", "[+] API started on 3000/TCP", 3000).kind == "started"
    assert Event(1, "info", "[+] API back on 3000/TCP", 3000).kind == "back"
    assert Event(1, "warning", "[-] API stopped on 3000/TCP", 3000).kind == "stopped"
    assert (
        Event(1, "info", "[~] API on 3000/TCP restarted (PID [1] → [2])", 3000).kind == "restarted"
    )
    assert (
        Event(1, "warning", "[~] API on 3000/TCP replaced (PID [1] → [2])", 3000).kind == "replaced"
    )
    assert Event(1, "warning", "[~] API on 3000/TCP degraded: slow", 3000).kind == "degraded"
    assert Event(1, "error", "[!] port conflict on 5432/TCP", 5432).kind == "conflict"
    assert Event(1, "error", "[!] API on 3000/TCP is failing: zombie", 3000).kind == "failing"
    assert Event(1, "info", "[✓] API on 3000/TCP recovered", 3000).kind == "recovered"
    assert Event(1, "info", "something else", None).kind == "other"
    assert Event(1, "info", "[!] port conflict on 5432/TCP", 5432).key == "5432/TCP"
    assert Event(1, "info", "[-] x stopped on 53/UDP", 53).key == "53/UDP"
    assert Event(1, "info", "no key here", None).key is None


def test_event_insights_ongoing_and_recurring() -> None:
    from lirts.insights import event_insights
    from lirts.models import Event

    now = 100_000.0
    api = make_listener(port=3000, identity=Identity("API", ROLE_BACKEND, 0.9))
    api.insights.append(
        Insight("error", "port-conflict", "Port conflict: a; b", "stop one of them")
    )
    web = make_listener(port=5173, identity=Identity("Vite", ROLE_FRONTEND, 0.9))
    snap = Snapshot(listeners=[api, web])
    events = [
        Event(now - 7200, "error", "[!] port conflict on 3000/TCP", 3000),
        Event(now - 3000, "info", "[~] Vite on 5173/TCP restarted (PID [1] → [2])", 5173),
        Event(now - 2000, "info", "[~] Vite on 5173/TCP restarted (PID [2] → [3])", 5173),
        Event(now - 1000, "info", "[~] Vite on 5173/TCP restarted (PID [3] → [4])", 5173),
        Event(now - 500, "warning", "[-] Old stopped on 9000/TCP", 9000),
        Event(now - 90_000, "warning", "[-] Old stopped on 9000/TCP", 9000),  # outside 24h window
    ]
    items = event_insights(events, snapshot=snap, now=now)
    levels = {m: (lvl, sug) for lvl, m, sug in items}
    ongoing = next(m for m in levels if m.startswith("ongoing"))
    assert "for 2h" in ongoing and "API on 3000/TCP" in ongoing and "Port conflict" in ongoing
    assert levels[ongoing] == ("error", "stop one of them")
    recurring = next(m for m in levels if m.startswith("recurring"))
    assert "Vite on 5173/TCP restarted 3× in the last hour" in recurring
    assert "crash loop" in (levels[recurring][1] or "")
    assert not any("9000" in m for m in levels)  # a single event in the window is not recurring
    assert event_insights([], snapshot=Snapshot(listeners=[web]), now=now) == []


def test_explain_sections_titles() -> None:
    from lirts.insights import explain_sections

    web = make_listener(
        port=3000, name="node", identity=Identity("Vite dev server", ROLE_FRONTEND, 0.9)
    )
    snap = Snapshot(listeners=[web])
    titles = [t for t, _ in explain_sections(snap)]
    assert titles[0] == "Summary" and "Warnings" in titles and "Events" not in titles
    titles = [t for t, _ in explain_sections(snap, {"missing": ["x"], "added": [], "changed": []})]
    assert "Changes" in titles


def test_spark_scales_the_values_to_the_highest_one() -> None:
    assert spark([0, 1, 2, 3, 4, 5, 6, 7]) == "▁▂▃▄▅▆▇█"
    assert spark([5, 5, 5]) == "███"
    assert spark([0, 0, 0]) == "▁▁▁"
    assert spark([]) == ""
    assert spark([1, 2, 3, 4], width=2) == "▆█"  # only the last two samples are drawn


def test_build_chain_follows_the_proxy_targets_and_stops_at_a_loop() -> None:
    nginx = make_listener(port=80, identity=Identity("Nginx", ROLE_PROXY, 0.95))
    api = make_listener(port=8000, identity=Identity("FastAPI", ROLE_BACKEND, 0.9))
    web = make_listener(port=5173, identity=Identity("Vite", ROLE_FRONTEND, 0.9))
    nginx.proxy_targets = [8000, 5173]
    api.proxy_targets = [80]
    by_port = {80: nginx, 8000: api, 5173: web}

    chain = build_chain(nginx, by_port=by_port)

    assert chain == "80 → Nginx → 8000 → FastAPI (+1 more)"
    assert build_chain(web, by_port=by_port) == "5173 → Vite"
