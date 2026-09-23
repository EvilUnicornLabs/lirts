"""The processes and containers the demo machine is seeded with.

Pure data: :func:`demo_processes` and :func:`demo_containers` take the world's
start time and return the objects :class:`lirts.demo.machine.DemoMachine` puts
into its state.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lirts.collectors.origin import Origin
from lirts.identity_rules_web import ProbeKind
from lirts.models import ContainerInfo, MountInfo

DOCKER_PID = 886
CHROME_PID = 4740
# The pretend login shell that started the local dev servers; only the one on 8090 lost it.
SHELL_PID = 700


@dataclass
class DemoProcess:
    """One pretend process of the demo machine."""

    pid: int
    name: str
    cmdline: list[str]
    ports: list[tuple[int, str]]  # (port, protocol)
    cwd: str | None = None
    user: str = "dev"
    cpu: float = 0.5
    mem: float = 60.0
    threads: int = 8
    created: float = 0.0
    addresses: list[str] = field(default_factory=lambda: ["127.0.0.1", "::1"])
    origin: Origin | None = None
    inbound: int = 0
    remotes: list[str] = field(default_factory=list)
    orphan: bool = False  # parent shell gone: the demo's one leftover dev server
    spiky: bool = False
    rate_in: float = 0.0  # B/s baseline
    rate_out: float = 0.0
    factor: float = 1.0  # traffic multiplier the timeline wiggles


def demo_processes(t0: float) -> list[DemoProcess]:
    """Local dev processes plus the Docker proxy that owns every published port."""
    dev = Origin(shell="zsh", shell_pid=911, terminal="Ghostty", tty="ttys003", git_branch="main")
    code = Origin(
        shell="zsh", shell_pid=912, terminal="Cursor", tty="ttys007", git_branch="feature/cart"
    )
    launchd = Origin(supervisor="launchd")
    published = [
        (3000, "TCP"),
        (8000, "TCP"),
        (5432, "TCP"),
        (6379, "TCP"),
        (8025, "TCP"),
        (1025, "TCP"),
        (8080, "TCP"),
        (3306, "TCP"),
        (9100, "TCP"),
    ]
    return [
        DemoProcess(
            3001,
            "node",
            ["node", "admin/server.js"],
            [(3001, "TCP")],
            cwd="/Users/dev/code/shop",
            cpu=0.2,
            mem=95,
            origin=code,
            created=t0 - 3600 * 9,
        ),
        DemoProcess(
            5173,
            "node",
            ["node", "node_modules/.bin/vite", "--host"],
            [(5173, "TCP")],
            cwd="/Users/dev/code/shop",
            cpu=1.5,
            mem=180,
            origin=dev,
            inbound=3,
            rate_in=4200,
            rate_out=38000,
            created=t0 - 5400,
        ),
        DemoProcess(
            8001,
            "python",
            ["python", "-m", "uvicorn", "app:app", "--port", "8001"],
            [(8001, "TCP")],
            cwd="/Users/dev/code/shop/api",
            cpu=3.0,
            mem=140,
            origin=dev,
            inbound=2,
            spiky=True,
            rate_in=9000,
            rate_out=22000,
            remotes=["127.0.0.1:5432", "127.0.0.1:6379"],
            created=t0 - 5000,
        ),
        DemoProcess(
            5432,
            "postgres",
            [
                "/opt/homebrew/opt/postgresql@16/bin/postgres",
                "-D",
                "/opt/homebrew/var/postgresql@16",
            ],
            [(5432, "TCP")],
            cwd="/",
            user="dev",
            cpu=0.3,
            mem=48,
            origin=launchd,
            addresses=["127.0.0.1"],
            created=t0 - 86400 * 2,
        ),
        DemoProcess(
            8090,
            "python",
            ["python3", "-m", "http.server", "8090"],
            [(8090, "TCP")],
            cwd="/Users/dev/code/docs",
            cpu=0.0,
            mem=22,
            origin=dev,
            created=t0 - 3600 * 26,
            orphan=True,
        ),
        DemoProcess(
            80,
            "nginx",
            ["nginx: master process nginx"],
            [(80, "TCP")],
            cwd="/",
            user="root",
            cpu=0.1,
            mem=12,
            origin=launchd,
            addresses=["0.0.0.0"],
            remotes=["127.0.0.1:5173", "127.0.0.1:8001"],
            created=t0 - 86400,
        ),
        DemoProcess(
            5353,
            "mDNSResponder",
            ["/usr/sbin/mDNSResponder"],
            [(5353, "UDP")],
            user="_mdnsresponder",
            cpu=0.1,
            mem=9,
            origin=launchd,
            addresses=["0.0.0.0"],
            created=t0 - 86400 * 3,
        ),
        DemoProcess(
            647,
            "rapportd",
            ["/usr/libexec/rapportd"],
            [(61033, "TCP")],
            user="dev",
            cpu=0.0,
            mem=14,
            origin=launchd,
            addresses=["0.0.0.0"],
            created=t0 - 86400 * 3,
        ),
        DemoProcess(
            9090,
            "kubectl",
            ["kubectl", "port-forward", "-n", "monitoring", "svc/prometheus", "9090:9090"],
            [(9090, "TCP")],
            cwd="/Users/dev/code/infra",
            cpu=0.0,
            mem=30,
            origin=dev,
            created=t0 - 900,
        ),
        DemoProcess(
            DOCKER_PID,
            "com.docker.backend",
            ["/Applications/Docker.app/Contents/MacOS/com.docker.backend"],
            published,
            user="dev",
            cpu=2.0,
            mem=900,
            origin=launchd,
            addresses=["::"],
            created=t0 - 86400,
        ),
    ]


@dataclass(frozen=True, slots=True)
class DemoResponse:
    """What a pretend port answers with, beyond its status and ``Server`` header."""

    content_type: str = "text/html"
    kind: ProbeKind = ProbeKind.HTML
    dev_server: str | None = None


# Ports that answer with something other than a plain HTML page, per port.
DEMO_RESPONSES: dict[int, DemoResponse] = {
    3001: DemoResponse("application/json", ProbeKind.JSON),
    5173: DemoResponse(dev_server="Vite"),
    8000: DemoResponse("application/json", ProbeKind.JSON),
    8001: DemoResponse("application/json", ProbeKind.JSON),
}

DEFAULT_DEMO_RESPONSE = DemoResponse()


def demo_response(port: int) -> DemoResponse:
    """What the demo probe learns from the body and headers of ``port``."""
    return DEMO_RESPONSES.get(port, DEFAULT_DEMO_RESPONSE)


def demo_container(
    t0: float,
    name: str,
    image: str,
    stack: str,
    service: str,
    ports: dict[int, str],
    health: str | None = None,
    status: str = "running",
    restarts: int = 0,
    started: float | None = None,
    env: dict[str, str] | None = None,
) -> ContainerInfo:
    cid = f"{abs(hash(name)) % 10**12:012x}"
    c = ContainerInfo(
        id=cid + "0" * (64 - len(cid)),
        short_id=cid[:12],
        name=name,
        image=image,
        status=status,
        health=health,
        stack=stack,
        service=service,
        working_dir=f"/Users/dev/code/{stack}",
        started_at=started or (t0 - 3600 * 30),
        restart_count=restarts,
        host_ports=sorted(ports),
        port_map=dict(ports),
        env=env
        or {
            "NODE_ENV": "development",
            "DATABASE_URL": "postgres://shop:secret@db/shop",
            "API_TOKEN": "sk-demo",
        },
        mounts=[
            MountInfo("bind", f"/Users/dev/code/{stack}", "/app"),
            MountInfo("volume", f"{stack}_data", "/var/lib/data", name=f"{stack}_data"),
        ],
        labels={"com.docker.compose.project": stack, "com.docker.compose.service": service},
        cpu_percent=0.4,
        memory_mb=64.0,
        memory_limit_mb=2048.0,
        net_rx_rate=0.0,
        net_tx_rate=0.0,
        net_rx_bytes=0,
        net_tx_bytes=0,
        pids_current=7,
    )
    return c


def demo_containers(t0: float) -> list[ContainerInfo]:
    """Three compose stacks, one unhealthy database and one crash-looping worker."""
    return [
        demo_container(
            t0, "shop-web-1", "shop/web:dev", "shop", "web", {3000: "3000/TCP"}, health="healthy"
        ),
        demo_container(
            t0, "shop-api-1", "shop/api:dev", "shop", "api", {8000: "8000/TCP"}, health="healthy"
        ),
        demo_container(
            t0, "shop-db-1", "postgres:16", "shop", "db", {5432: "5432/TCP"}, health="healthy"
        ),
        demo_container(t0, "shop-redis-1", "redis:7", "shop", "redis", {6379: "6379/TCP"}),
        demo_container(
            t0,
            "shop-mailhog-1",
            "mailhog/mailhog",
            "shop",
            "mailhog",
            {8025: "8025/TCP", 1025: "1025/TCP"},
        ),
        demo_container(
            t0, "blog-wordpress-1", "wordpress:6", "blog", "wordpress", {8080: "80/TCP"}
        ),
        demo_container(
            t0, "blog-mysql-1", "mysql:8", "blog", "mysql", {3306: "3306/TCP"}, health="healthy"
        ),
        demo_container(
            t0,
            "jobs-worker-1",
            "shop/worker:dev",
            "jobs",
            "worker",
            {9100: "9100/TCP"},
            restarts=4,
            started=t0 - 40,
        ),
    ]
