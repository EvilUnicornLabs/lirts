"""Proxy routes: asking nginx / httpd what they serve, and applying that to listeners."""

from __future__ import annotations

from lirts.collectors.proxies import discover_routes
from lirts.engine.base import EngineBase
from lirts.insights import build_chain
from lirts.models import Listener, ListenerState


class RoutesMixin(EngineBase):
    """Asks nginx / httpd what they serve and applies it to the live listeners."""

    def reload_routes_on_next_refresh(self) -> None:
        """Forget the routes so the next refresh asks nginx / httpd again."""
        self._routes_loaded = False

    def refresh_routes(self) -> None:
        """Ask nginx / httpd for their virtual hosts (a command each; run at start and on request)."""
        self._routes_loaded = True
        if not self.proxies_enabled:
            self.routes, self.route_notes = [], []
            return
        self.routes, self.route_notes = discover_routes()

    def _apply_routes(self, listeners: list[Listener]) -> None:
        """Proxy targets and domain names from the configured routes onto the live listeners."""
        if not self.routes or not self.proxies_enabled:
            return
        by_port: dict[int, Listener] = {}
        for lst in listeners:
            if lst.protocol == "TCP" and lst.state == ListenerState.LISTEN:
                by_port.setdefault(lst.port, lst)
        for route in self.routes:
            proxy = by_port.get(route.listen_port)
            if proxy is not None:
                for name in route.names:
                    if name not in proxy.hosts:
                        proxy.hosts.append(name)
            if route.upstream_port and route.upstream_port in by_port and proxy is not None:
                if (
                    route.upstream_port not in proxy.proxy_targets
                    and route.upstream_port != proxy.port
                ):
                    proxy.proxy_targets.append(route.upstream_port)
                upstream = by_port[route.upstream_port]
                for name in route.names:
                    if name not in upstream.hosts:
                        upstream.hosts.append(name)
        for lst in listeners:
            if lst.proxy_targets:
                lst.proxy_chain = build_chain(lst, by_port=by_port)
