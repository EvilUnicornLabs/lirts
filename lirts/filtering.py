"""Filter syntax for the table and `lirts list --filter`.

A filter is a list of space-separated terms.  A plain word matches anywhere in
the row's searchable text.  ``key:value`` restricts the match to one field, and
a leading ``-`` negates a term::

    status:error project:laahustaja src:docker -system
    port:3000-3999 role:frontend
"""

from __future__ import annotations

from dataclasses import dataclass

from lirts.models import Listener

FIELDS: dict[str, str] = {
    "port": "port number, or a range like 3000-3999",
    "proto": "TCP or UDP",
    "state": "LISTEN, BOUND, STOPPED",
    "src": "local or docker",
    "project": "project / compose stack",
    "stack": "alias of project",
    "service": "identified service name",
    "role": "frontend, backend, db, cache, proxy, queue, tool, system, tunnel",
    "process": "process name",
    "pid": "process id",
    "container": "container name",
    "image": "container image",
    "activity": "idle, active, hot",
    "status": "healthy, warning, error, unknown",
    "new": "yes: started within the highlight window (needs the app's clock)",
}


@dataclass(frozen=True)
class Term:
    """One term of a filter: an optional field, the text to match and whether it is negated."""

    key: str | None
    value: str
    negate: bool = False


def parse_filter(text: str) -> list[Term]:
    """Split filter text into terms; unknown ``key:`` prefixes stay part of the text."""
    terms: list[Term] = []
    for raw in text.split():
        negate = raw.startswith("-") or raw.startswith("!")
        body = raw[1:] if negate else raw
        if not body:
            continue
        key: str | None = None
        value = body
        if ":" in body:
            head, _, tail = body.partition(":")
            if head.lower() in FIELDS and tail:
                key, value = head.lower(), tail
        terms.append(Term(key, value.lower(), negate))
    return terms


def _field(listener: Listener, key: str) -> str:
    if key == "port":
        return str(listener.port)
    if key == "proto":
        return listener.protocol
    if key == "state":
        return listener.state
    if key == "src":
        return listener.source
    if key in ("project", "stack"):
        return listener.identity.project or (
            listener.container.stack if listener.container and listener.container.stack else ""
        )
    if key == "service":
        return listener.identity.service
    if key == "role":
        return listener.identity.role
    if key == "process":
        return listener.name
    if key == "pid":
        return " ".join(str(p) for p in listener.pids)
    if key == "container":
        return listener.container.name if listener.container else ""
    if key == "image":
        return listener.container.image if listener.container else ""
    if key == "activity":
        return listener.activity
    if key == "status":
        return listener.status
    return ""


def _match_port(listener: Listener, value: str) -> bool:
    if "-" in value:
        lo, _, hi = value.partition("-")
        try:
            return int(lo) <= listener.port <= int(hi)
        except ValueError:
            return False
    try:
        return listener.port == int(value)
    except ValueError:
        return False


def term_matches(listener: Listener, term: Term) -> bool:
    """Whether one term matches a row (``new:`` is always False; the app resolves it)."""
    if term.key is None:
        hit = term.value in listener.search_blob()
    elif term.key == "port":
        hit = _match_port(listener, term.value)
    elif term.key == "pid":
        hit = term.value in _field(listener, "pid").split()
    elif term.key == "new":
        hit = False  # resolved by the app, which knows the highlight window
    else:
        field = _field(listener, term.key).lower()
        hit = (
            (field == term.value)
            if term.key in ("src", "proto", "state", "status", "activity", "role")
            else (term.value in field)
        )
    return not hit if term.negate else hit


def matches(listener: Listener, terms: list[Term]) -> bool:
    """Whether a row matches every term."""
    return all(term_matches(listener, t) for t in terms)


def apply_filter(listeners: list[Listener], text: str) -> list[Listener]:
    """The rows matching ``text``; empty filter text keeps them all."""
    terms = parse_filter(text)
    if not terms:
        return list(listeners)
    return [x for x in listeners if matches(x, terms)]
