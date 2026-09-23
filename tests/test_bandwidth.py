from __future__ import annotations

from lirts.collectors.bandwidth import BandwidthSampler, parse_nettop_csv

SAMPLE = """,bytes_in,bytes_out,
apsd.362,12772,29344,
mDNSResponder.503,6548486,2870912,
weird line without pid,1,2,
node.1234,1000,2000,
"""


def test_parse_nettop_csv() -> None:
    parsed = parse_nettop_csv(SAMPLE)
    assert parsed[362] == (12772, 29344)
    assert parsed[1234] == (1000, 2000)
    assert len(parsed) == 3


def test_ingest_rates() -> None:
    sampler = BandwidthSampler(mode="off")
    assert sampler.available is False
    sampler.ingest({1: (1000, 500)}, now=10.0)
    assert sampler.rates() == {}
    sampler.ingest({1: (3000, 500), 2: (10, 10)}, now=12.0)
    assert sampler.rates() == {1: (1000.0, 0.0)}
    sampler.ingest({1: (100, 0)}, now=13.0)  # counter reset -> no rate, pid 2 forgotten
    assert sampler.rates() == {}
    assert 2 not in sampler._last


def test_start_when_disabled_is_noop() -> None:
    sampler = BandwidthSampler(mode="off")
    sampler.start()
    assert sampler._thread is None
    sampler.stop()


NETTOP_CONNS = """,bytes_in,bytes_out,
Google Chrome Helper.4740,5000,7000,
tcp4 127.0.0.1:55989<->127.0.0.1:5173,1200,300,
tcp6 ::1.55990<->::1.8082,50,20,
udp4 *:5353<->*:*,10,10,
node.1234,9,9,
tcp4 127.0.0.1:5173<->127.0.0.1:55989,300,1200,
"""

SS_CONNS = (
    'ESTAB 0 0 127.0.0.1:41234 127.0.0.1:8000 users:(("python3",pid=321,fd=5))\n'
    "\t cubic wscale:7,7 rto:204 bytes_sent:800 bytes_acked:800 bytes_received:4096 segs_out:9\n"
    'ESTAB 0 0 127.0.0.1:8000 127.0.0.1:41234 users:(("uvicorn",pid=99,fd=8))\n'
    "\t cubic bytes_acked:4096 bytes_received:800\n"
    "ESTAB 0 0 10.0.0.2:5555 10.0.0.9:22\n"
    "\t cubic bytes_acked:1 bytes_received:1\n"
)


def test_parse_nettop_connections() -> None:
    from lirts.collectors.bandwidth import parse_nettop_connections

    flows = parse_nettop_connections(NETTOP_CONNS)
    assert flows[(4740, 55989, 5173)] == (1200, 300)
    assert flows[(4740, 55990, 8082)] == (50, 20)
    assert flows[(1234, 5173, 55989)] == (300, 1200)
    assert len(flows) == 3  # the udp wildcard line has no ports


def test_parse_ss_connections() -> None:
    from lirts.collectors.bandwidth import parse_ss_connections

    flows = parse_ss_connections(SS_CONNS)
    assert flows == {(321, 41234, 8000): (4096, 800), (99, 8000, 41234): (800, 4096)}


def test_flow_rates_and_toggle() -> None:
    sampler = BandwidthSampler(mode="off", connections=True)
    key = (4740, 55989, 5173)
    sampler.ingest_flows({key: (1000, 100)}, now=10.0)
    assert sampler.flows()[key] == (0.0, 0.0, 1000, 100)
    sampler.ingest_flows({key: (3000, 100)}, now=12.0)
    assert sampler.flows()[key] == (1000.0, 0.0, 3000, 100)
    sampler.ingest_flows({}, now=13.0)
    assert sampler.flows() == {} and sampler._flow_last == {}


def test_flows_supported_follows_the_backend_that_was_found(monkeypatch) -> None:
    sampler = BandwidthSampler(mode="auto")
    sampler._nettop = None
    sampler._ss = None

    assert sampler.flows_supported is False

    sampler._nettop = "/usr/bin/nettop"
    assert sampler.flows_supported is True
