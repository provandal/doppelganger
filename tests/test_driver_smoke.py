"""Driver smoke tests.

Three layers:

1. **Parser unit test.** Synthetic ``fct.txt`` content; no Docker required.
2. **Driver error-path test.** Drive the image-missing case; no Docker required.
3. **End-to-end scenario test.** Gated on the substrate image being built
   locally; runs the spike-burst scenario and asserts at least one flow record
   comes back. Marked ``requires_substrate``; auto-skipped if not present.
"""

from __future__ import annotations

import textwrap

import pytest

from doppelganger.driver import Driver, DriverError
from doppelganger.driver.counters import aggregate_counters
from doppelganger.driver.parsers.counters import parse_counters_file
from doppelganger.driver.parsers.ecn import parse_ecn_file
from doppelganger.driver.parsers.fct import parse_fct_file
from doppelganger.driver.parsers.pfc import parse_pfc_file
from doppelganger.driver.types import (
    CompletionStatus,
    CounterRollupRow,
    EcnMarkEvent,
    PfcEvent,
)
from doppelganger.scenarios import (
    SPIKE_BURST_256,
    Scenario,
    spike_burst_baseline,
)
from doppelganger.scenarios.topology import Topology
from doppelganger.scenarios.types import TopologyRef


# --------------------------------------------------------------------- parser

def test_fct_parser_parses_well_formed_lines(tmp_path):
    sample = textwrap.dedent(
        """\
        0a000001 0a000002 49152 50000 4096 1000 12500 10000
        0a000001 0a000003 49153 50000 8192 1500 25000 20000
        """
    )
    fct = tmp_path / "fct.txt"
    fct.write_text(sample)

    records = parse_fct_file(fct)

    assert len(records) == 2
    first = records[0]
    assert first.sip == "0a000001"
    assert first.dip == "0a000002"
    assert first.sport == 49152
    assert first.dport == 50000
    assert first.actual_size_bytes == 4096
    assert first.fct_ns == 12500
    assert first.standalone_fct_ns == 10000
    assert first.status is CompletionStatus.COMPLETED
    assert first.slowdown == pytest.approx(1.25)


def test_fct_parser_skips_malformed_lines(tmp_path):
    sample = textwrap.dedent(
        """\
        # this is a header comment, not a record
        0a000001 0a000002 49152 50000 4096 1000 12500 10000
        not enough columns
        0a000001 0a000003 49153 50000 nondigit 1500 25000 20000
        0a000001 0a000004 49154 50000 4096 2000 11000 10000
        """
    )
    fct = tmp_path / "fct.txt"
    fct.write_text(sample)

    records = parse_fct_file(fct)

    # The two well-formed lines survive; comment / short / non-digit lines drop.
    assert len(records) == 2


def test_fct_parser_handles_empty_file(tmp_path):
    fct = tmp_path / "fct.txt"
    fct.write_text("")
    assert parse_fct_file(fct) == []


def test_pfc_parser_parses_well_formed_lines(tmp_path):
    """Six-column pfc.txt format includes q_index (per-priority PFC,
    SONiC alignment, 2026-05-10)."""
    sample = textwrap.dedent(
        """\
        150037591 256 1 34 2 3
        150038594 265 1 1 1 3
        150085642 256 1 34 3 3
        150086645 265 1 1 0 3
        """
    )
    pfc = tmp_path / "pfc.txt"
    pfc.write_text(sample)

    events = parse_pfc_file(pfc)

    assert len(events) == 4
    pause_sent = events[0]
    assert pause_sent.timestamp_ns == 150037591
    assert pause_sent.node_id == 256
    assert pause_sent.node_type == 1  # switch
    assert pause_sent.if_index == 34
    assert pause_sent.event_type == 2
    assert pause_sent.q_index == 3
    assert pause_sent.is_pause is True

    resume_rcvd = events[3]
    assert resume_rcvd.event_type == 0
    assert resume_rcvd.q_index == 3
    assert resume_rcvd.is_pause is False


def test_pfc_parser_skips_malformed_lines(tmp_path):
    """Old 5-column format is now treated as malformed (skipped)."""
    pfc = tmp_path / "pfc.txt"
    pfc.write_text(
        "# header comment\n"
        "150037591 256 1 34 2 3\n"        # well-formed, 6 cols
        "not enough cols\n"
        "150037591 256 1 34 2\n"          # old 5-col format → skipped
        "150085642 256 1 nondigit 3 3\n"
        "150086645 265 1 1 0 7\n"         # well-formed, 6 cols
    )
    assert len(parse_pfc_file(pfc)) == 2


def test_pfc_parser_handles_empty_file(tmp_path):
    """Empty pfc.txt is a valid state — DCQCN-controlled incast produces zero
    PFC events when ECN marking is operational. Must not raise."""
    pfc = tmp_path / "pfc.txt"
    pfc.write_text("")
    assert parse_pfc_file(pfc) == []


def test_ecn_parser_parses_well_formed_lines(tmp_path):
    sample = textwrap.dedent(
        """\
        150005846 256 17 3
        150008978 256 17 3
        150434010 256 17 3
        """
    )
    ecn = tmp_path / "ecn.txt"
    ecn.write_text(sample)

    events = parse_ecn_file(ecn)

    assert len(events) == 3
    first = events[0]
    assert first.timestamp_ns == 150005846
    assert first.switch_id == 256
    assert first.if_index == 17
    assert first.q_index == 3


def test_ecn_parser_skips_malformed_lines(tmp_path):
    ecn = tmp_path / "ecn.txt"
    ecn.write_text(
        "# comment\n"
        "150005846 256 17 3\n"
        "too few\n"
        "150008978 256 nondigit 3\n"
        "150434010 256 17 3\n"
    )
    assert len(parse_ecn_file(ecn)) == 2


def test_ecn_parser_handles_empty_file(tmp_path):
    """Empty ecn.txt is the *diagnostic* state for `pfc_storm(ecn_misconfigured
    =True)` — KMIN above buffer capacity means ShouldSendCN always returns
    false and no marks are emitted. Must not raise; downstream tools read
    the empty count as observed-zero, not as missing data."""
    ecn = tmp_path / "ecn.txt"
    ecn.write_text("")
    assert parse_ecn_file(ecn) == []


# ----------------------------------- aggregator: SONiC-shaped per-queue records


PORT_TOP_LEVEL_FIELDS = {
    "node_id", "if_index", "node_type",
    "oper_status", "admin_status",
    "speed_bps", "mtu_bytes",
    "queues",
}
QUEUE_FIELDS = {
    "q_index",
    "rx_packets", "rx_bytes", "tx_packets", "tx_bytes",
    "dropped_packets", "qlen_peak_bytes", "pg_watermark_bytes",
    "pfc_pause_sent", "pfc_pause_rcvd",
    "pfc_resume_sent", "pfc_resume_rcvd",
    "ecn_marks_sent",
}


def test_aggregate_counters_empty_inputs_yield_empty_ports():
    result = aggregate_counters([], [])
    assert result["ports"] == []


def test_aggregate_counters_does_not_emit_aggregates():
    """Per Erik's call (2026-05-10): port-level aggregates are themselves
    a kind of tool use. Forcing the agent to sum across queues to get
    "total port PFC count" or "total port throughput" preserves Stage 5b's
    measurement of *naked* model behavior. No "totals" key, no
    port-level rx/tx/pfc/ecn fields outside the per-queue array."""
    pfc = [PfcEvent(timestamp_ns=1, node_id=1, node_type=1,
                    if_index=2, event_type=2, q_index=3)]
    ecn = [EcnMarkEvent(timestamp_ns=2, switch_id=3, if_index=4, q_index=3)]
    result = aggregate_counters(pfc, ecn)
    assert "totals" not in result
    for rec in result["ports"]:
        # Top-level fields are interface state + queues array only.
        assert set(rec.keys()) == PORT_TOP_LEVEL_FIELDS
        # Specifically, no port-level pre-aggregated counter fields.
        for forbidden in ("pfc_pause_sent", "ecn_marks_sent",
                          "rx_packets", "tx_packets",
                          "rx_bytes", "tx_bytes",
                          "drops", "dropped_packets",
                          "qlen_peak_bytes", "pg_watermark_bytes"):
            assert forbidden not in rec, (
                f"port-level field {forbidden!r} leaks pre-aggregation; "
                f"every counter must live inside the per-queue records"
            )


def test_aggregate_counters_pfc_lives_on_per_queue_record():
    """Per-priority PFC: a pause on q=3 populates the q=3 record only,
    leaves the other 7 queues' PFC fields at zero. SONiC-shape: PFC is
    keyed by 802.1p priority, so per-queue is the natural granularity."""
    pfc = [
        PfcEvent(timestamp_ns=1, node_id=256, node_type=1,
                 if_index=34, event_type=2, q_index=3),
        PfcEvent(timestamp_ns=2, node_id=256, node_type=1,
                 if_index=34, event_type=3, q_index=3),
    ]
    result = aggregate_counters(pfc, [])
    assert len(result["ports"]) == 1
    rec = result["ports"][0]
    assert len(rec["queues"]) == 8
    q3 = rec["queues"][3]
    assert q3["pfc_pause_sent"] == 1
    assert q3["pfc_resume_sent"] == 1
    assert q3["ecn_marks_sent"] == 0
    for q in (0, 1, 2, 4, 5, 6, 7):
        assert rec["queues"][q]["pfc_pause_sent"] == 0
        assert rec["queues"][q]["pfc_resume_sent"] == 0


def test_aggregate_counters_ecn_lives_on_per_queue_record():
    """Per-priority ECN: marks fire on the egress queue's priority. A
    burst of 3 marks on q=3 lands on the q=3 record, not on a port-level
    ecn_marks_sent field."""
    ecn = [
        EcnMarkEvent(timestamp_ns=1, switch_id=256, if_index=17, q_index=3),
        EcnMarkEvent(timestamp_ns=2, switch_id=256, if_index=17, q_index=3),
        EcnMarkEvent(timestamp_ns=3, switch_id=256, if_index=17, q_index=3),
    ]
    result = aggregate_counters([], ecn)
    assert len(result["ports"]) == 1
    rec = result["ports"][0]
    q3 = rec["queues"][3]
    assert q3["ecn_marks_sent"] == 3
    for q in (0, 1, 2, 4, 5, 6, 7):
        assert rec["queues"][q]["ecn_marks_sent"] == 0


def test_aggregate_counters_combines_pfc_and_ecn_on_same_switch_different_ports():
    """PFC pauses fire on the ingress port; ECN marks on the egress port.
    Different ports of the same switch — aggregator emits one record per
    port, both PFC and ECN classes always present per queue (zero-filled
    where no events fired)."""
    pfc = [
        PfcEvent(timestamp_ns=1, node_id=256, node_type=1,
                 if_index=34, event_type=2, q_index=3),
    ]
    ecn = [
        EcnMarkEvent(timestamp_ns=2, switch_id=256, if_index=17, q_index=3),
        EcnMarkEvent(timestamp_ns=3, switch_id=256, if_index=17, q_index=3),
    ]
    result = aggregate_counters(pfc, ecn)
    assert len(result["ports"]) == 2
    by_port = {r["if_index"]: r for r in result["ports"]}
    assert by_port[17]["queues"][3]["ecn_marks_sent"] == 2
    assert by_port[17]["queues"][3]["pfc_pause_sent"] == 0
    assert by_port[34]["queues"][3]["pfc_pause_sent"] == 1
    assert by_port[34]["queues"][3]["ecn_marks_sent"] == 0


def test_aggregate_counters_every_port_has_8_queues_with_every_field():
    """Structural leak guard, SONiC-shape edition. Every port carries
    a queues array of length 8; every queue carries every counter
    field; every value is an int (zero is data, not absence)."""
    pfc = [PfcEvent(timestamp_ns=1, node_id=200, node_type=0,
                    if_index=1, event_type=1, q_index=0)]
    ecn = [EcnMarkEvent(timestamp_ns=2, switch_id=300, if_index=5, q_index=0)]
    result = aggregate_counters(pfc, ecn)
    for rec in result["ports"]:
        assert PORT_TOP_LEVEL_FIELDS.issubset(rec.keys())
        assert len(rec["queues"]) == 8
        for q_index, q in enumerate(rec["queues"]):
            assert q["q_index"] == q_index
            assert QUEUE_FIELDS.issubset(q.keys()), (
                f"queue {q_index} missing fields: "
                f"{QUEUE_FIELDS - q.keys()}"
            )
            for f in QUEUE_FIELDS:
                assert isinstance(q[f], int)


def test_aggregate_counters_breaks_pfc_event_types_into_correct_buckets():
    """Substrate's get_pfc encodes the event type as 0..3:
    0=resume_rcvd, 1=pause_rcvd, 2=pause_sent, 3=resume_sent. Each
    lands in its own field of the per-queue record."""
    pfc = [
        PfcEvent(timestamp_ns=1, node_id=10, node_type=0,
                 if_index=2, event_type=0, q_index=3),
        PfcEvent(timestamp_ns=2, node_id=10, node_type=0,
                 if_index=2, event_type=1, q_index=3),
        PfcEvent(timestamp_ns=3, node_id=10, node_type=0,
                 if_index=2, event_type=2, q_index=3),
        PfcEvent(timestamp_ns=4, node_id=10, node_type=0,
                 if_index=2, event_type=3, q_index=3),
    ]
    q = aggregate_counters(pfc, [])["ports"][0]["queues"][3]
    assert q["pfc_resume_rcvd"] == 1
    assert q["pfc_pause_rcvd"] == 1
    assert q["pfc_pause_sent"] == 1
    assert q["pfc_resume_sent"] == 1


def test_aggregate_counters_ports_sorted_stably_by_node_then_port():
    """Port records emit in (node_id, if_index) order so trace renderings
    and diffs are reproducible."""
    pfc = [
        PfcEvent(timestamp_ns=1, node_id=300, node_type=1,
                 if_index=4, event_type=2, q_index=3),
        PfcEvent(timestamp_ns=2, node_id=200, node_type=1,
                 if_index=8, event_type=2, q_index=3),
        PfcEvent(timestamp_ns=3, node_id=200, node_type=1,
                 if_index=4, event_type=2, q_index=3),
    ]
    keys = [(r["node_id"], r["if_index"])
            for r in aggregate_counters(pfc, [])["ports"]]
    assert keys == [(200, 4), (200, 8), (300, 4)]


# -------------------------------------------------------- counters.txt parser

def test_parse_counters_file_parses_well_formed_rows(tmp_path):
    """Ten-column per-(switch, port, queue) format with rx/tx
    packets+bytes, drops, egress qlen peak, and ingress PG watermark."""
    counters = tmp_path / "counters.txt"
    counters.write_text(
        textwrap.dedent(
            """\
            128 1 3 67717 5959096 67717 5959096 0 0 3270
            128 17 3 198108 215937720 198106 215935540 5 237620 0
            """
        )
    )
    rows = parse_counters_file(counters)
    assert len(rows) == 2
    assert rows[0] == CounterRollupRow(
        switch_id=128, if_index=1, q_index=3,
        rx_packets=67717, rx_bytes=5959096,
        tx_packets=67717, tx_bytes=5959096,
        dropped_packets=0, qlen_peak_bytes=0,
        pg_watermark_bytes=3270,
    )
    assert rows[1].dropped_packets == 5
    assert rows[1].qlen_peak_bytes == 237620
    assert rows[1].pg_watermark_bytes == 0


def test_parse_counters_file_skips_malformed_lines(tmp_path):
    counters = tmp_path / "counters.txt"
    counters.write_text(
        "header that should be ignored\n"
        "128 1 3 67717 5959096 67717 5959096 0 0 3270\n"
        "256 4 too few\n"
        "256 5 1 2 3 4 5 6 7 8 9 too many\n"
        "256 6 abc def 1 2 3 4 5 6 7\n"
    )
    rows = parse_counters_file(counters)
    assert len(rows) == 1
    assert rows[0].switch_id == 128


def test_parse_counters_file_empty_file_returns_empty_list(tmp_path):
    counters = tmp_path / "counters.txt"
    counters.write_text("")
    assert parse_counters_file(counters) == []


# ------------------------------------------ aggregator: rollup + topology + state

def test_aggregate_counters_rollup_populates_per_queue_volumetric():
    """A counters.txt rollup row populates the matching (port, queue)
    record's rx/tx/dropped/qlen_peak/pg_watermark fields."""
    rollup = [
        CounterRollupRow(
            switch_id=128, if_index=17, q_index=3,
            rx_packets=198108, rx_bytes=215937720,
            tx_packets=198106, tx_bytes=215935540,
            dropped_packets=5, qlen_peak_bytes=237620,
            pg_watermark_bytes=0,
        ),
    ]
    rec = aggregate_counters([], [], rollup_rows=rollup)["ports"][0]
    assert rec["node_id"] == 128
    assert rec["if_index"] == 17
    q = rec["queues"][3]
    assert q["rx_packets"] == 198108
    assert q["rx_bytes"] == 215937720
    assert q["tx_packets"] == 198106
    assert q["dropped_packets"] == 5
    assert q["qlen_peak_bytes"] == 237620
    assert q["pg_watermark_bytes"] == 0
    # PFC and ECN remain zero on every queue because no events fed in.
    for queue in rec["queues"]:
        assert queue["pfc_pause_sent"] == 0
        assert queue["ecn_marks_sent"] == 0


def test_aggregate_counters_rollup_and_events_combine_on_same_port_queue():
    """Triple-source coalesce: PFC + ECN events + counters rollup all
    referencing the same (switch, port, queue) merge into one queue
    record carrying every counter class."""
    pfc = [
        PfcEvent(timestamp_ns=1, node_id=128, node_type=1,
                 if_index=17, event_type=2, q_index=3),
        PfcEvent(timestamp_ns=2, node_id=128, node_type=1,
                 if_index=17, event_type=2, q_index=3),
    ]
    ecn = [EcnMarkEvent(timestamp_ns=3, switch_id=128, if_index=17, q_index=3)]
    rollup = [
        CounterRollupRow(
            switch_id=128, if_index=17, q_index=3,
            rx_packets=1000, rx_bytes=1_000_000,
            tx_packets=999, tx_bytes=999_000,
            dropped_packets=0, qlen_peak_bytes=42_000,
            pg_watermark_bytes=8_000,
        ),
    ]
    result = aggregate_counters(pfc, ecn, rollup_rows=rollup)
    assert len(result["ports"]) == 1
    q = result["ports"][0]["queues"][3]
    assert q["pfc_pause_sent"] == 2
    assert q["ecn_marks_sent"] == 1
    assert q["tx_packets"] == 999
    assert q["qlen_peak_bytes"] == 42_000
    assert q["pg_watermark_bytes"] == 8_000


def test_aggregate_counters_topology_zero_fills_all_switch_ports_with_8_queues():
    """Production-shape: every port the topology declares appears in the
    output with all 8 queues zero-filled. The agent has to find the
    storm port AND the storm queue among many enumerated zeroes —
    asymmetry is *relative*, not absolute."""
    topology = Topology(leaves=2, spines=1, hosts_per_leaf=4)
    # Leaf has hosts_per_leaf + spines = 5 ports each (×2 leaves = 10).
    # Spine has leaves = 2 ports (×1 spine = 2). Total = 12 ports.
    rollup = [
        CounterRollupRow(
            switch_id=topology.first_leaf_id(), if_index=3, q_index=3,
            rx_packets=99, rx_bytes=99_000,
            tx_packets=99, tx_bytes=99_000,
            dropped_packets=0, qlen_peak_bytes=12_345,
            pg_watermark_bytes=4_000,
        ),
    ]
    result = aggregate_counters([], [], rollup_rows=rollup, topology=topology)
    assert len(result["ports"]) == 12
    # Every port has 8 queues regardless of activity
    for rec in result["ports"]:
        assert len(rec["queues"]) == 8
    # The one storm queue is preserved with its values intact
    storm_port = next(
        r for r in result["ports"]
        if r["node_id"] == topology.first_leaf_id() and r["if_index"] == 3
    )
    assert storm_port["queues"][3]["rx_packets"] == 99
    assert storm_port["queues"][3]["qlen_peak_bytes"] == 12_345
    assert storm_port["queues"][3]["pg_watermark_bytes"] == 4_000
    # Other queues on the storm port are zero
    for q in (0, 1, 2, 4, 5, 6, 7):
        assert storm_port["queues"][q]["rx_packets"] == 0
    # Other ports report zero across all queues
    quiet_ports = [r for r in result["ports"]
                   if not (r["node_id"] == topology.first_leaf_id() and r["if_index"] == 3)]
    assert len(quiet_ports) == 11
    for r in quiet_ports:
        for q in r["queues"]:
            assert q["rx_packets"] == 0
            assert q["tx_bytes"] == 0
            assert q["dropped_packets"] == 0
            assert q["qlen_peak_bytes"] == 0
            assert q["pg_watermark_bytes"] == 0


def test_aggregate_counters_topology_zero_fill_includes_spines():
    """Spine switches must appear in the enumerated port set with one
    if_index per leaf they connect to, all with 8 zero-filled queues."""
    topology = Topology(leaves=4, spines=2, hosts_per_leaf=2)
    result = aggregate_counters([], [], topology=topology)
    spine_ids = {topology.first_spine_id(), topology.first_spine_id() + 1}
    spine_ports = [r for r in result["ports"] if r["node_id"] in spine_ids]
    assert len(spine_ports) == 2 * topology.leaves
    by_spine = {}
    for r in spine_ports:
        by_spine.setdefault(r["node_id"], []).append(r["if_index"])
    for spine_id, indices in by_spine.items():
        assert sorted(indices) == [1, 2, 3, 4]
    for r in spine_ports:
        assert len(r["queues"]) == 8


def test_aggregate_counters_topology_rollup_outside_topology_still_emitted():
    """If the rollup references a (switch_id, if_index) that the
    topology enumeration doesn't include, the row is still emitted as
    a record — substrate data is not silently dropped."""
    topology = Topology(leaves=1, spines=1, hosts_per_leaf=1)
    # leaf=2 ports, spine=1 port. Total enumerated = 3.
    rollup = [
        CounterRollupRow(
            switch_id=999, if_index=42, q_index=3,
            rx_packets=7, rx_bytes=7_000,
            tx_packets=7, tx_bytes=7_000,
            dropped_packets=0, qlen_peak_bytes=0,
            pg_watermark_bytes=0,
        ),
    ]
    result = aggregate_counters([], [], rollup_rows=rollup, topology=topology)
    assert len(result["ports"]) == 4
    extra = [r for r in result["ports"] if r["node_id"] == 999]
    assert len(extra) == 1
    assert extra[0]["queues"][3]["rx_packets"] == 7


def test_aggregate_counters_topology_populates_interface_state():
    """Per-port interface state matches SONiC's `show interfaces status`:
    oper_status, admin_status, speed_bps, mtu_bytes. Speeds come from
    the Topology (host_link_bps for downlinks, spine_link_bps for
    uplinks); MTU is the substrate's fixed PACKET_PAYLOAD_SIZE."""
    topology = Topology(
        leaves=2, spines=1, hosts_per_leaf=2,
        host_link_bps=25_000_000_000,
        spine_link_bps=100_000_000_000,
    )
    result = aggregate_counters([], [], topology=topology)
    for rec in result["ports"]:
        assert rec["oper_status"] == "up"
        assert rec["admin_status"] == "up"
        assert rec["mtu_bytes"] == 1000  # SUBSTRATE_FIXED_MTU_BYTES
        assert rec["speed_bps"] in {25_000_000_000, 100_000_000_000}
    # Leaf 0's downlinks (host-facing if_index 1..hosts_per_leaf) are
    # 25Gbps; its uplinks (if_index hosts_per_leaf+1..hosts_per_leaf+spines)
    # are 100Gbps. Spine downlinks are also 100Gbps.
    leaf0 = topology.first_leaf_id()
    spine0 = topology.first_spine_id()
    leaf0_ports = sorted(
        (r for r in result["ports"] if r["node_id"] == leaf0),
        key=lambda r: r["if_index"],
    )
    assert leaf0_ports[0]["speed_bps"] == 25_000_000_000  # host downlink
    assert leaf0_ports[1]["speed_bps"] == 25_000_000_000  # host downlink
    assert leaf0_ports[2]["speed_bps"] == 100_000_000_000  # spine uplink
    spine0_ports = [r for r in result["ports"] if r["node_id"] == spine0]
    for r in spine0_ports:
        assert r["speed_bps"] == 100_000_000_000


def test_aggregate_counters_pfc_event_with_invalid_q_index_is_ignored():
    """PFC event with q_index outside [0, 7] is dropped silently rather
    than indexing into a nonexistent queue. Prevents one malformed
    substrate row from corrupting the response shape."""
    pfc = [
        PfcEvent(timestamp_ns=1, node_id=10, node_type=0,
                 if_index=2, event_type=2, q_index=99),
    ]
    result = aggregate_counters(pfc, [])
    assert result["ports"] == []  # no port created from invalid q_index


# ----------------------------------------------------------------- driver api

def test_driver_lists_builtin_scenarios():
    driver = Driver(substrate_image="doesnt-matter-for-this-test")
    scenarios = driver.list_scenarios()
    assert "spike-burst" in scenarios


def test_driver_rejects_unknown_scenario(tmp_path):
    driver = Driver(
        substrate_image="doesnt-matter-for-this-test",
        traces_root=tmp_path,
    )
    with pytest.raises(DriverError, match="Unknown scenario"):
        driver.run_scenario("not-a-real-scenario")


def test_driver_raises_when_image_missing(tmp_path):
    """If the substrate image isn't built locally, run_scenario raises clearly."""
    driver = Driver(
        substrate_image="doppelganger-substrate-definitely-does-not-exist",
        traces_root=tmp_path,
    )
    with pytest.raises(DriverError) as exc_info:
        driver.run_scenario("spike-burst")
    msg = str(exc_info.value)
    assert "not found locally" in msg or "docker CLI not found" in msg


def test_driver_rejects_non_string_non_scenario_input(tmp_path):
    driver = Driver(
        substrate_image="bogus-image",
        traces_root=tmp_path,
    )
    with pytest.raises(DriverError, match="must be a str or Scenario"):
        driver.run_scenario(42)  # type: ignore[arg-type]


def test_driver_passes_algorithm_matching_scenario_cc_mode(tmp_path):
    """The Driver must emit --algorithm=<cc_mode> so the substrate's silent
    override (cc_mode = algorithm at line 717) becomes a no-op.

    Default Scenario cc_mode is 3; this test also covers a custom value.
    """
    driver = Driver(traces_root=tmp_path)
    default = spike_burst_baseline()
    _, sim_cmd, _, _ = driver._prepare_run(default, run_id="default-cc")
    assert "--algorithm=3" in sim_cmd

    custom_cc = Scenario(
        name="custom-cc",
        topology=SPIKE_BURST_256,
        cc_mode=8,
    )
    _, sim_cmd, _, _ = driver._prepare_run(custom_cc, run_id="custom-cc")
    assert "--algorithm=8" in sim_cmd


def test_driver_does_not_pass_algorithm_for_builtin_string_scenario(tmp_path):
    """The built-in 'spike-burst' string scenario uses the substrate's bundled
    config-burst.txt; CC_MODE there is 3 which matches the cmd-line default.
    Don't add --algorithm to that path; leave the existing behavior alone.
    """
    driver = Driver(traces_root=tmp_path)
    _, sim_cmd, _, _ = driver._prepare_run("spike-burst", run_id="builtin")
    assert "--algorithm" not in sim_cmd


def test_driver_compiles_scenario_before_image_check(tmp_path):
    """Driver should compile the scenario into trace_dir before checking the image.

    Means a fresh trace dir + compiled config-burst.txt exist on disk even
    if the substrate image isn't built — useful for inspecting what would
    have run, and confirms the compile-and-stage path works without Docker.
    """
    driver = Driver(
        substrate_image="doppelganger-substrate-definitely-does-not-exist",
        traces_root=tmp_path,
    )
    scenario = spike_burst_baseline()

    with pytest.raises(DriverError, match="not found locally"):
        driver.run_scenario(scenario, run_id="compile-only")

    expected_config = tmp_path / "compile-only" / "config-burst.txt"
    assert expected_config.exists()
    text = expected_config.read_text(encoding="utf-8")
    assert text.startswith("ENABLE_QCN 1\n")
    assert "TOPOLOGY_FILE examples/PowerTCP/topology-256.txt" in text


# ---------------------------------------------------------- end-to-end (gated)

@pytest.mark.requires_substrate
def test_driver_runs_spike_burst_end_to_end(tmp_path, substrate_available):
    """Full Driver round-trip: build image → run scenario → parse flows.

    Requires the doppelganger-substrate image to be built locally:

        docker build -t doppelganger-substrate -f docker/substrate.Dockerfile .

    Auto-skipped otherwise so CI without Docker still passes the rest.
    """
    if not substrate_available:
        pytest.skip("doppelganger-substrate image not built locally")

    driver = Driver(traces_root=tmp_path)
    result = driver.run_scenario("spike-burst", run_id="smoke")

    assert result.scenario == "spike-burst"
    assert result.trace_dir == tmp_path / "smoke"
    assert result.compiled_config_path is None  # built-in path doesn't compile
    assert (result.trace_dir / "fct.txt").exists(), "substrate did not produce fct.txt"
    assert len(result.flows) > 0, "expected at least one completed flow"
    assert all(r.status is CompletionStatus.COMPLETED for r in result.flows)
    assert all(r.fct_ns is not None and r.fct_ns > 0 for r in result.flows)


@pytest.mark.requires_substrate
def test_driver_runs_compiled_scenario_end_to_end(tmp_path, substrate_available):
    """Full Driver round-trip via Scenario object.

    Verifies the compile-and-stage path: Driver compiles ``spike_burst_baseline()``
    into ``trace_dir/config-burst.txt``, the substrate runs against the
    compiled config (not the bundled one), and flows come back.
    """
    if not substrate_available:
        pytest.skip("doppelganger-substrate image not built locally")

    driver = Driver(traces_root=tmp_path)
    result = driver.run_scenario(spike_burst_baseline(), run_id="smoke-scenario")

    assert result.scenario == "spike-burst-baseline"
    assert result.compiled_config_path == tmp_path / "smoke-scenario" / "config-burst.txt"
    assert result.compiled_config_path.exists()
    assert (result.trace_dir / "fct.txt").exists()
    assert len(result.flows) > 0
    assert all(r.status is CompletionStatus.COMPLETED for r in result.flows)
