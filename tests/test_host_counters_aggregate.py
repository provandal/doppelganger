"""Tests for ``aggregate_host_counters`` (drops_per_million derivation).

Hermetic — exercises the aggregator directly against synthetic input. The
substrate-side roundtrip (does the substrate actually emit
host_counters.txt and counters.txt in the shapes the aggregator
consumes, on the if_indices the aggregator assumes) is covered by the
gated end-to-end tests in test_adapter.py.

Why these tests exist:

* ``drops_per_million`` is the load-bearing new field for the
  silent-drops Stage 5b skill: the variance-pass (2026-05-12) data
  showed agents reaching CORRECT only when they computed this ratio
  before committing. Surfacing it as a substrate-derived field is
  Option D from HANDOFF_NEXT_SESSION.md. The leak rule (None when
  denominator is 0, not 0 as a placeholder) is the same instinct as
  PerFlowRecord.sport — distinguish "unknown" from "measured zero."
* The leaf-port if_index mapping (host H attached to leaf L has
  L's host-facing if_index = (H % hosts_per_leaf) + 1) is a contract
  on the substrate's install order. ``driver/counters.py:_switch_port_keys``
  already relies on it; this test makes the contract explicit for the
  host-side path.
"""

from __future__ import annotations

from doppelganger.driver.host_counters import aggregate_host_counters
from doppelganger.driver.parsers.host_counters import HostCounterRow
from doppelganger.driver.types import CounterRollupRow
from doppelganger.scenarios.topology import Topology


def _make_rollup(
    switch_id: int, if_index: int, tx_packets_per_queue: list[int]
) -> list[CounterRollupRow]:
    """Build CounterRollupRow rows for one port with the given per-queue tx counts."""
    return [
        CounterRollupRow(
            switch_id=switch_id,
            if_index=if_index,
            q_index=q_index,
            rx_packets=0,
            rx_bytes=0,
            tx_packets=tx,
            tx_bytes=0,
            dropped_packets=0,
            qlen_peak_bytes=0,
            pg_watermark_bytes=0,
        )
        for q_index, tx in enumerate(tx_packets_per_queue)
    ]


def _toy_topology(leaves: int = 2, spines: int = 1, hosts_per_leaf: int = 2) -> Topology:
    return Topology(leaves=leaves, spines=spines, hosts_per_leaf=hosts_per_leaf)


def test_drops_per_million_computed_from_leaf_tx() -> None:
    """Host 0 sees 10 drops; leaf port saw 1,000,000 tx — rate is 10.0."""
    topo = _toy_topology()
    # Topology: 2 leaves × 2 hosts/leaf × 1 spine. first_leaf_id = 4.
    # Host 0 -> leaf 4, leaf's host-facing if_index = 1.
    rollup = _make_rollup(switch_id=4, if_index=1, tx_packets_per_queue=[1_000_000, 0, 0, 0, 0, 0, 0, 0])
    rows = [HostCounterRow(host_id=0, if_index=1, drop_packets=10)]

    records = aggregate_host_counters(rows, rollup, topo)
    host0 = next(r for r in records if r["host_id"] == 0)
    assert host0["drop_packets"] == 10
    assert host0["drops_per_million"] == 10.0


def test_drops_per_million_sums_across_queues() -> None:
    """Leaf-port tx is summed across all 8 queues for the denominator."""
    topo = _toy_topology()
    # 8 queues each carrying 125,000 tx_packets = 1,000,000 total.
    rollup = _make_rollup(
        switch_id=4, if_index=1,
        tx_packets_per_queue=[125_000] * 8,
    )
    rows = [HostCounterRow(host_id=0, if_index=1, drop_packets=50)]

    records = aggregate_host_counters(rows, rollup, topo)
    host0 = next(r for r in records if r["host_id"] == 0)
    assert host0["drops_per_million"] == 50.0


def test_drops_per_million_none_when_denominator_zero() -> None:
    """No tx packets to this host this run → drops_per_million is None,
    not 0. Leak rule: a sentinel for 'cannot compute' must be
    distinguishable from a real measurement of zero."""
    topo = _toy_topology()
    rollup: list[CounterRollupRow] = []  # no counter data at all
    rows = [HostCounterRow(host_id=0, if_index=1, drop_packets=7)]

    records = aggregate_host_counters(rows, rollup, topo)
    host0 = next(r for r in records if r["host_id"] == 0)
    assert host0["drop_packets"] == 7
    assert host0["drops_per_million"] is None


def test_drops_per_million_none_when_topology_missing() -> None:
    """Without topology the leaf-port mapping is unknown, so
    drops_per_million cannot be honestly computed. None, not 0."""
    rows = [HostCounterRow(host_id=0, if_index=1, drop_packets=7)]
    rollup = _make_rollup(switch_id=4, if_index=1, tx_packets_per_queue=[1_000_000] + [0] * 7)

    records = aggregate_host_counters(rows, rollup, topology=None)
    assert len(records) == 1
    assert records[0]["host_id"] == 0
    assert records[0]["drop_packets"] == 7
    assert records[0]["drops_per_million"] is None


def test_zero_drops_with_real_tx_gives_zero_rate() -> None:
    """A host that observed traffic but no drops has rate 0.0, NOT
    None. The None sentinel is reserved for 'cannot compute,' not for
    'computed and the answer was 0.'"""
    topo = _toy_topology()
    rollup = _make_rollup(switch_id=4, if_index=1, tx_packets_per_queue=[1_000_000] + [0] * 7)
    rows: list[HostCounterRow] = []  # no drops observed

    records = aggregate_host_counters(rows, rollup, topo)
    host0 = next(r for r in records if r["host_id"] == 0)
    assert host0["drop_packets"] == 0
    assert host0["drops_per_million"] == 0.0


def test_topology_aware_zero_fill_preserved() -> None:
    """Topology-aware path still emits one row per declared host with
    drop_packets zero-filled. drops_per_million on zero-traffic hosts
    is None (denominator is 0)."""
    topo = _toy_topology()  # 4 hosts total
    rows: list[HostCounterRow] = []
    rollup: list[CounterRollupRow] = []

    records = aggregate_host_counters(rows, rollup, topo)
    assert [r["host_id"] for r in records] == [0, 1, 2, 3]
    for r in records:
        assert r["drop_packets"] == 0
        assert r["drops_per_million"] is None
        assert r["if_index"] == 1


def test_leaf_port_mapping_pinned_for_multi_host_leaf() -> None:
    """Contract pin: host H attached to leaf L has L's host-facing
    if_index = (H % hosts_per_leaf) + 1. This is the contract the
    substrate's install order produces — host links are installed on
    each leaf in host_id order, before leaf-to-spine uplinks, with NS-3
    assigning if_index sequentially from 1 (loopback is 0). Pinning
    here so a future substrate change that reorders link installation
    is caught as a test failure rather than as a silently-wrong rate."""
    topo = _toy_topology(leaves=2, spines=1, hosts_per_leaf=4)
    # first_leaf_id = 8 (4 hosts/leaf × 2 leaves). first_spine_id = 10.
    assert topo.first_leaf_id() == 8

    # Each host gets a unique drop count so we can disambiguate.
    rows = [
        HostCounterRow(host_id=h, if_index=1, drop_packets=100 + h)
        for h in range(8)
    ]
    # Each leaf has 4 host-facing ports (if_index 1..4) followed by 1
    # uplink (if_index 5). Give each host's leaf port a unique tx_packets
    # count derived from (host_id + 1) * 1_000_000 so the per-host
    # denominator is determinate.
    rollup: list[CounterRollupRow] = []
    for host_id in range(8):
        leaf_id = topo.first_leaf_id() + host_id // topo.hosts_per_leaf
        leaf_port_if = (host_id % topo.hosts_per_leaf) + 1
        rollup.extend(_make_rollup(
            switch_id=leaf_id,
            if_index=leaf_port_if,
            tx_packets_per_queue=[(host_id + 1) * 1_000_000] + [0] * 7,
        ))

    records = aggregate_host_counters(rows, rollup, topo)
    assert len(records) == 8
    for rec in records:
        h = rec["host_id"]
        # drops_per_million = (100 + h) / ((h + 1) * 1_000_000) * 1e6
        #                   = (100 + h) / (h + 1)
        expected_rate = (100 + h) / (h + 1)
        assert rec["drops_per_million"] == expected_rate, (
            f"host_id={h}: rate mismatch suggests leaf-port mapping drift. "
            f"Got {rec['drops_per_million']}, expected {expected_rate}."
        )


def test_records_sorted_by_host_id() -> None:
    """Output ordering is deterministic for diff-friendliness."""
    topo = _toy_topology()
    rollup = _make_rollup(switch_id=4, if_index=1, tx_packets_per_queue=[1_000_000] + [0] * 7)
    rows = [
        HostCounterRow(host_id=2, if_index=1, drop_packets=20),
        HostCounterRow(host_id=0, if_index=1, drop_packets=10),
        HostCounterRow(host_id=1, if_index=1, drop_packets=15),
    ]

    records = aggregate_host_counters(rows, rollup, topo)
    assert [r["host_id"] for r in records] == [0, 1, 2, 3]


def test_bundled_topology_shadow_matches_topology_256() -> None:
    """The SPIKE_BURST_256_TOPOLOGY shadow is correct for the bundled
    topology-256.txt: 8 leaves × 32 hosts/leaf = 256 hosts. This is the
    only static topology shadow Doppelgänger ships; the dimensions are
    pinned here so a drift in topology-256.txt or in the shadow
    surfaces as a test failure rather than as silently wrong
    drops_per_million values."""
    from doppelganger.scenarios.builtin import SPIKE_BURST_256_TOPOLOGY

    assert SPIKE_BURST_256_TOPOLOGY.leaves == 8
    assert SPIKE_BURST_256_TOPOLOGY.hosts_per_leaf == 32
    assert SPIKE_BURST_256_TOPOLOGY.num_hosts == 256
    # first_leaf_id must equal num_hosts (substrate convention: hosts
    # come first, then leaves). The bundled topology has switch IDs
    # starting at 256, matching this.
    assert SPIKE_BURST_256_TOPOLOGY.first_leaf_id() == 256


def test_drops_per_million_with_bundled_topology_shadow() -> None:
    """End-to-end check: using SPIKE_BURST_256_TOPOLOGY, host 16 lands
    on leaf 256 (not leaf 257) because hosts_per_leaf=32, not 16. The
    leaf's host-facing if_index for host 16 is (16 % 32) + 1 = 17. This
    is the mapping the silent-drops verify run needs."""
    from doppelganger.scenarios.builtin import SPIKE_BURST_256_TOPOLOGY

    topo = SPIKE_BURST_256_TOPOLOGY
    # Host 16 → leaf 256, if_index 17. Give that port 160_287 tx_packets
    # (matches the observed counter from the 2026-05-13 verify-D2 k3 trace).
    rollup = _make_rollup(
        switch_id=256, if_index=17,
        tx_packets_per_queue=[0, 0, 0, 160_287, 0, 0, 0, 0],
    )
    rows = [HostCounterRow(host_id=16, if_index=1, drop_packets=160)]

    records = aggregate_host_counters(rows, rollup, topo)
    host16 = next(r for r in records if r["host_id"] == 16)
    assert host16["drop_packets"] == 160
    # 160 / 160287 * 1e6 ≈ 998.21 — matches link_error_rate=0.001 exactly.
    assert host16["drops_per_million"] is not None
    assert 990 < host16["drops_per_million"] < 1010


def test_drops_per_million_handles_fractional_rate() -> None:
    """Non-integer rates serialize as floats."""
    topo = _toy_topology()
    rollup = _make_rollup(switch_id=4, if_index=1, tx_packets_per_queue=[3_000_000] + [0] * 7)
    rows = [HostCounterRow(host_id=0, if_index=1, drop_packets=1)]

    records = aggregate_host_counters(rows, rollup, topo)
    host0 = next(r for r in records if r["host_id"] == 0)
    # 1 drop in 3M tx = 1/3 per million
    assert host0["drops_per_million"] is not None
    assert abs(host0["drops_per_million"] - (1 / 3)) < 1e-9
