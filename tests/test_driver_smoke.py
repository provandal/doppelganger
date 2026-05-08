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
    sample = textwrap.dedent(
        """\
        150037591 256 1 34 2
        150038594 265 1 1 1
        150085642 256 1 34 3
        150086645 265 1 1 0
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
    assert pause_sent.is_pause is True

    resume_rcvd = events[3]
    assert resume_rcvd.event_type == 0
    assert resume_rcvd.is_pause is False


def test_pfc_parser_skips_malformed_lines(tmp_path):
    pfc = tmp_path / "pfc.txt"
    pfc.write_text(
        "# header comment\n"
        "150037591 256 1 34 2\n"
        "not enough cols\n"
        "150085642 256 1 nondigit 3\n"
        "150086645 265 1 1 0\n"
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


# ------------------------------------------------------------ counter aggregator

def test_aggregate_counters_empty_inputs_yield_empty_ports():
    result = aggregate_counters([], [])
    assert result["ports"] == []


def test_aggregate_counters_does_not_emit_a_totals_row():
    """Per the Stage 5a closing-test finding (2026-05-08): pre-aggregating
    fabric-wide totals leaks the asymmetry diagnostic. The agent should
    have to scan/sum per-port records to see whether ECN marks fired
    anywhere — that's investigative discipline the eval is designed to
    surface."""
    result = aggregate_counters([], [])
    assert "totals" not in result
    pfc = [PfcEvent(timestamp_ns=1, node_id=1, node_type=1, if_index=2, event_type=2)]
    ecn = [EcnMarkEvent(timestamp_ns=2, switch_id=3, if_index=4, q_index=0)]
    assert "totals" not in aggregate_counters(pfc, ecn)


def test_aggregate_counters_pfc_only_zero_fills_ecn_field():
    """A port with PFC events but no ECN marks must still expose
    ``ecn_marks_sent: 0``. The asymmetry diagnostic depends on the agent
    seeing both fields side-by-side; surfacing one as missing would let
    the agent draw conclusions from a half-payload."""
    pfc = [
        PfcEvent(timestamp_ns=1, node_id=256, node_type=1, if_index=34, event_type=2),
        PfcEvent(timestamp_ns=2, node_id=256, node_type=1, if_index=34, event_type=3),
    ]
    result = aggregate_counters(pfc, [])
    assert len(result["ports"]) == 1
    rec = result["ports"][0]
    assert rec["pfc_pause_sent"] == 1
    assert rec["pfc_resume_sent"] == 1
    assert rec["ecn_marks_sent"] == 0  # zero-filled, not missing
    assert "ecn_marks_sent" in rec


def test_aggregate_counters_ecn_only_zero_fills_pfc_fields():
    """Mirror of the PFC-only test: ECN-marked port must still expose
    all four PFC counter fields as 0."""
    ecn = [
        EcnMarkEvent(timestamp_ns=1, switch_id=256, if_index=17, q_index=3),
        EcnMarkEvent(timestamp_ns=2, switch_id=256, if_index=17, q_index=3),
        EcnMarkEvent(timestamp_ns=3, switch_id=256, if_index=17, q_index=3),
    ]
    result = aggregate_counters([], ecn)
    assert len(result["ports"]) == 1
    rec = result["ports"][0]
    assert rec["ecn_marks_sent"] == 3
    for field in ("pfc_pause_sent", "pfc_pause_rcvd", "pfc_resume_sent", "pfc_resume_rcvd"):
        assert rec[field] == 0
        assert field in rec


def test_aggregate_counters_combines_pfc_and_ecn_on_same_switch_different_ports():
    """Real-world case: PFC pauses fire on the ingress port from the
    sender; ECN marks fire on the egress port toward the receiver. They
    typically appear on different ports of the same switch. The aggregator
    emits one record per port, both classes always present."""
    pfc = [
        PfcEvent(timestamp_ns=1, node_id=256, node_type=1, if_index=34, event_type=2),
    ]
    ecn = [
        EcnMarkEvent(timestamp_ns=2, switch_id=256, if_index=17, q_index=3),
        EcnMarkEvent(timestamp_ns=3, switch_id=256, if_index=17, q_index=3),
    ]
    result = aggregate_counters(pfc, ecn)
    assert len(result["ports"]) == 2

    by_port = {r["if_index"]: r for r in result["ports"]}
    assert by_port[17]["ecn_marks_sent"] == 2
    assert by_port[17]["pfc_pause_sent"] == 0
    assert by_port[34]["pfc_pause_sent"] == 1
    assert by_port[34]["ecn_marks_sent"] == 0


def test_aggregate_counters_every_port_record_has_every_field():
    """The asymmetry diagnostic depends on the agent seeing both classes
    in *every* record. Loop over every port record and assert that all
    five counter fields are present and integer-valued, regardless of
    which classes the events actually populated."""
    pfc = [PfcEvent(timestamp_ns=1, node_id=200, node_type=0, if_index=1, event_type=1)]
    ecn = [EcnMarkEvent(timestamp_ns=2, switch_id=300, if_index=5, q_index=0)]
    result = aggregate_counters(pfc, ecn)
    required_fields = {
        "pfc_pause_sent", "pfc_pause_rcvd",
        "pfc_resume_sent", "pfc_resume_rcvd",
        "ecn_marks_sent",
    }
    for rec in result["ports"]:
        assert required_fields.issubset(rec.keys())
        for f in required_fields:
            assert isinstance(rec[f], int)


def test_aggregate_counters_breaks_pfc_event_types_into_correct_buckets():
    """Substrate's get_pfc encodes the event type as 0..3:
    0=resume_rcvd, 1=pause_rcvd, 2=pause_sent, 3=resume_sent."""
    pfc = [
        PfcEvent(timestamp_ns=1, node_id=10, node_type=0, if_index=2, event_type=0),
        PfcEvent(timestamp_ns=2, node_id=10, node_type=0, if_index=2, event_type=1),
        PfcEvent(timestamp_ns=3, node_id=10, node_type=0, if_index=2, event_type=2),
        PfcEvent(timestamp_ns=4, node_id=10, node_type=0, if_index=2, event_type=3),
    ]
    rec = aggregate_counters(pfc, [])["ports"][0]
    assert rec["pfc_resume_rcvd"] == 1
    assert rec["pfc_pause_rcvd"] == 1
    assert rec["pfc_pause_sent"] == 1
    assert rec["pfc_resume_sent"] == 1


def test_aggregate_counters_ports_sorted_stably_by_node_then_port():
    """Port records emit in (node_id, if_index) order so trace renderings
    and diffs are reproducible."""
    pfc = [
        PfcEvent(timestamp_ns=1, node_id=300, node_type=1, if_index=4, event_type=2),
        PfcEvent(timestamp_ns=2, node_id=200, node_type=1, if_index=8, event_type=2),
        PfcEvent(timestamp_ns=3, node_id=200, node_type=1, if_index=4, event_type=2),
    ]
    keys = [(r["node_id"], r["if_index"]) for r in aggregate_counters(pfc, [])["ports"]]
    assert keys == [(200, 4), (200, 8), (300, 4)]


# -------------------------------------------------------- counters.txt parser

def test_parse_counters_file_parses_well_formed_rows(tmp_path):
    counters = tmp_path / "counters.txt"
    counters.write_text(
        textwrap.dedent(
            """\
            128 1 67717 5959096 67717 5959096 0 0
            128 17 198108 215937720 198106 215935540 5 237620
            """
        )
    )
    rows = parse_counters_file(counters)
    assert len(rows) == 2
    assert rows[0] == CounterRollupRow(
        switch_id=128, if_index=1,
        rx_packets=67717, rx_bytes=5959096,
        tx_packets=67717, tx_bytes=5959096,
        drops=0, qlen_peak_bytes=0,
    )
    assert rows[1].drops == 5
    assert rows[1].qlen_peak_bytes == 237620


def test_parse_counters_file_skips_malformed_lines(tmp_path):
    counters = tmp_path / "counters.txt"
    counters.write_text(
        "header that should be ignored\n"
        "128 1 67717 5959096 67717 5959096 0 0\n"
        "256 4 too few\n"
        "256 5 1 2 3 4 5 6 7 8 9 too many\n"
        "256 6 abc def 1 2 3 4 5 6\n"
    )
    rows = parse_counters_file(counters)
    assert len(rows) == 1
    assert rows[0].switch_id == 128


def test_parse_counters_file_empty_file_returns_empty_list(tmp_path):
    counters = tmp_path / "counters.txt"
    counters.write_text("")
    assert parse_counters_file(counters) == []


# ------------------------------------------ aggregator: volumetric + topology

def test_aggregate_counters_volumetric_fields_default_zero_without_rollup():
    """Stage 5a backward-compat: callers passing only PFC + ECN events get
    the volumetric fields zero-filled. The structural-leak guarantee
    extends to the new counter classes."""
    pfc = [PfcEvent(timestamp_ns=1, node_id=128, node_type=1, if_index=2, event_type=2)]
    rec = aggregate_counters(pfc, [])["ports"][0]
    for f in ("rx_packets", "rx_bytes", "tx_packets", "tx_bytes",
              "drops", "qlen_peak_bytes"):
        assert rec[f] == 0


def test_aggregate_counters_rollup_populates_volumetric_fields():
    rollup = [
        CounterRollupRow(
            switch_id=128, if_index=17,
            rx_packets=198108, rx_bytes=215937720,
            tx_packets=198106, tx_bytes=215935540,
            drops=5, qlen_peak_bytes=237620,
        ),
    ]
    rec = aggregate_counters([], [], rollup_rows=rollup)["ports"][0]
    assert rec["node_id"] == 128
    assert rec["if_index"] == 17
    assert rec["rx_packets"] == 198108
    assert rec["rx_bytes"] == 215937720
    assert rec["tx_packets"] == 198106
    assert rec["drops"] == 5
    assert rec["qlen_peak_bytes"] == 237620
    # PFC and ECN remain zero because no events fed in
    assert rec["pfc_pause_sent"] == 0
    assert rec["ecn_marks_sent"] == 0


def test_aggregate_counters_rollup_and_events_combine_on_same_port():
    """The agent reads asymmetry across PFC, ECN, and volumetric on the
    same record. When all three sources reference the same (node_id,
    if_index), the aggregator must coalesce — not duplicate — into one
    record carrying every counter class."""
    pfc = [
        PfcEvent(timestamp_ns=1, node_id=128, node_type=1, if_index=17, event_type=2),
        PfcEvent(timestamp_ns=2, node_id=128, node_type=1, if_index=17, event_type=2),
    ]
    ecn = [EcnMarkEvent(timestamp_ns=3, switch_id=128, if_index=17, q_index=3)]
    rollup = [
        CounterRollupRow(
            switch_id=128, if_index=17,
            rx_packets=1000, rx_bytes=1_000_000,
            tx_packets=999, tx_bytes=999_000,
            drops=0, qlen_peak_bytes=42_000,
        ),
    ]
    result = aggregate_counters(pfc, ecn, rollup_rows=rollup)
    assert len(result["ports"]) == 1
    rec = result["ports"][0]
    assert rec["pfc_pause_sent"] == 2
    assert rec["ecn_marks_sent"] == 1
    assert rec["tx_packets"] == 999
    assert rec["qlen_peak_bytes"] == 42_000


def test_aggregate_counters_topology_zero_fills_all_switch_ports():
    """Production-shape: a topology with N switch ports must produce N
    records, even when only one port saw activity. Storm-vs-baseline
    detection requires the agent to find the anomalous port among
    zero-filled siblings — not against a 2-row payload that pre-aggregates
    asymmetry by omission. (Stage 5a-realistic, 2026-05-09.)"""
    topology = Topology(leaves=2, spines=1, hosts_per_leaf=4)
    # Leaf has hosts_per_leaf + spines = 5 ports each (×2 leaves = 10).
    # Spine has leaves = 2 ports (×1 spine = 2). Total = 12.
    rollup = [
        CounterRollupRow(
            switch_id=topology.first_leaf_id(), if_index=3,
            rx_packets=99, rx_bytes=99_000,
            tx_packets=99, tx_bytes=99_000,
            drops=0, qlen_peak_bytes=12_345,
        ),
    ]
    result = aggregate_counters([], [], rollup_rows=rollup, topology=topology)
    assert len(result["ports"]) == 12
    # The one storm port is preserved with its volumetric values intact.
    storm = next(
        r for r in result["ports"]
        if r["node_id"] == topology.first_leaf_id() and r["if_index"] == 3
    )
    assert storm["rx_packets"] == 99
    assert storm["qlen_peak_bytes"] == 12_345
    # Every other port reports zero across the volumetric fields.
    quiet = [r for r in result["ports"] if not (
        r["node_id"] == topology.first_leaf_id() and r["if_index"] == 3
    )]
    assert len(quiet) == 11
    for r in quiet:
        assert r["rx_packets"] == 0
        assert r["tx_bytes"] == 0
        assert r["drops"] == 0
        assert r["qlen_peak_bytes"] == 0


def test_aggregate_counters_topology_zero_fill_includes_spines():
    """Spine switches must appear in the enumerated port set with one
    if_index per leaf they connect to. Otherwise the agent could miss
    asymmetry that surfaces only on spine uplinks."""
    topology = Topology(leaves=4, spines=2, hosts_per_leaf=2)
    result = aggregate_counters([], [], topology=topology)
    spine_ids = {topology.first_spine_id(), topology.first_spine_id() + 1}
    spine_ports = [r for r in result["ports"] if r["node_id"] in spine_ids]
    assert len(spine_ports) == 2 * topology.leaves  # 2 spines × 4 ports
    # Each spine port appears with if_index 1..leaves
    by_spine = {}
    for r in spine_ports:
        by_spine.setdefault(r["node_id"], []).append(r["if_index"])
    for spine_id, indices in by_spine.items():
        assert sorted(indices) == [1, 2, 3, 4]


def test_aggregate_counters_topology_rollup_outside_topology_still_emitted():
    """If the rollup references a (switch_id, if_index) that the topology
    enumeration doesn't include, the row is still emitted as a record —
    substrate data is not silently dropped."""
    topology = Topology(leaves=1, spines=1, hosts_per_leaf=1)
    # leaf=2 ports, spine=1 port. Total enumerated = 3.
    rollup = [
        CounterRollupRow(
            switch_id=999, if_index=42,  # node_id outside topology
            rx_packets=7, rx_bytes=7_000,
            tx_packets=7, tx_bytes=7_000,
            drops=0, qlen_peak_bytes=0,
        ),
    ]
    result = aggregate_counters([], [], rollup_rows=rollup, topology=topology)
    assert len(result["ports"]) == 4  # 3 topology ports + 1 unexpected
    extra = [r for r in result["ports"] if r["node_id"] == 999]
    assert len(extra) == 1 and extra[0]["rx_packets"] == 7


def test_aggregate_counters_every_port_record_has_every_counter_class():
    """Asymmetry diagnostic depends on the agent seeing PFC + ECN +
    volumetric in *every* record. With the new volumetric fields added in
    Stage 5a-realistic, the structural enforcement extends to all 11
    counter fields, regardless of which classes happened to populate."""
    topology = Topology(leaves=1, spines=1, hosts_per_leaf=1)
    pfc = [PfcEvent(timestamp_ns=1, node_id=200, node_type=0, if_index=1, event_type=1)]
    ecn = [EcnMarkEvent(timestamp_ns=2, switch_id=300, if_index=5, q_index=0)]
    rollup = [
        CounterRollupRow(
            switch_id=300, if_index=5,
            rx_packets=1, rx_bytes=1, tx_packets=1, tx_bytes=1,
            drops=0, qlen_peak_bytes=0,
        ),
    ]
    result = aggregate_counters(pfc, ecn, rollup_rows=rollup, topology=topology)
    required = {
        "pfc_pause_sent", "pfc_pause_rcvd",
        "pfc_resume_sent", "pfc_resume_rcvd",
        "ecn_marks_sent",
        "rx_packets", "rx_bytes",
        "tx_packets", "tx_bytes",
        "drops", "qlen_peak_bytes",
    }
    for rec in result["ports"]:
        assert required.issubset(rec.keys())
        for f in required:
            assert isinstance(rec[f], int)


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
