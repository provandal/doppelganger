"""Tests for the microburst and PFC-storm scenario factories.

These tests don't run the substrate; they verify the scenario shapes
produced by ``microburst()`` and ``pfc_storm()`` compile cleanly and
have the structural properties Doppelgänger v0.2 §5.2 specifies.
"""

from __future__ import annotations

import pytest

from doppelganger.scenarios import (
    OPEN_LOOP_PACKETS,
    Scenario,
    asymmetric_path,
    compile_scenario,
    compile_topology,
    compile_traffic,
    hash_polarization,
    microburst,
    pfc_storm,
)


# ----------------------------------------------------------- microburst

def test_microburst_returns_scenario_with_custom_topology_and_traffic():
    s = microburst(leaves=2, spines=4, hosts_per_leaf=8)
    assert isinstance(s, Scenario)
    assert s.custom_topology is not None
    assert s.custom_traffic is not None


def test_microburst_topology_dimensions_match_arguments():
    s = microburst(leaves=3, spines=2, hosts_per_leaf=4)
    topo = s.custom_topology
    assert topo.leaves == 3
    assert topo.spines == 2
    assert topo.hosts_per_leaf == 4
    assert topo.num_hosts == 12


def test_microburst_traffic_is_incast_to_host_zero():
    """Every flow in microburst targets host 0; no flow has host 0 as source."""
    s = microburst(leaves=2, spines=4, hosts_per_leaf=8)
    flows = s.custom_traffic.flows
    assert all(f.dst == 0 for f in flows)
    assert all(f.src != 0 for f in flows)
    # N hosts → N-1 senders (every host except 0)
    assert len(flows) == s.custom_topology.num_hosts - 1


def test_microburst_flows_share_start_time():
    """The 'synchronized' part: every flow starts at the same simulation time."""
    s = microburst(burst_start_seconds=0.07)
    starts = {f.start_time_seconds for f in s.custom_traffic.flows}
    assert starts == {0.07}


def test_microburst_flows_have_unique_dst_ports():
    """Substrate's port-tracking would collide if two flows share (src,dst,dport)."""
    s = microburst(leaves=2, spines=4, hosts_per_leaf=8)
    ports = [f.dst_port for f in s.custom_traffic.flows]
    assert len(ports) == len(set(ports))


def test_microburst_compiles_end_to_end(tmp_path):
    """The Scenario, its custom topology, and its custom traffic all compile."""
    s = microburst(leaves=2, spines=2, hosts_per_leaf=4)

    config_path = compile_scenario(s, tmp_path / "config-burst.txt")
    topo_path = compile_topology(s.custom_topology, tmp_path / "topology.txt")
    traffic_path = compile_traffic(s.custom_traffic, tmp_path / "flow.txt")

    assert config_path.exists()
    assert topo_path.exists()
    assert traffic_path.exists()

    # config-burst.txt must point its TOPOLOGY_FILE / FLOW_FILE at /traces/
    config_text = config_path.read_text(encoding="utf-8")
    assert "TOPOLOGY_FILE /traces/topology.txt" in config_text
    assert "FLOW_FILE /traces/flow.txt" in config_text


def test_microburst_packet_count_is_bounded():
    """Microburst flows are not open-loop; the burst clears within sim duration."""
    s = microburst(packets_per_flow=3000)
    for f in s.custom_traffic.flows:
        assert f.packet_count == 3000
        assert f.packet_count < OPEN_LOOP_PACKETS


# ----------------------------------------------------------- pfc_storm

def test_pfc_storm_returns_scenario_with_storm_and_victim_flows():
    s = pfc_storm(leaves=4, spines=4, hosts_per_leaf=4)
    assert s.custom_topology is not None
    assert s.custom_traffic is not None
    flows = s.custom_traffic.flows
    # Sources on every leaf except leaf 0; plus one victim flow.
    storm_source_count = (4 - 1) * 4   # 12 storm sources
    assert len(flows) == storm_source_count + 1


def test_pfc_storm_storm_flows_are_open_loop():
    """The 'persistent' part: storm flows use OPEN_LOOP_PACKETS."""
    s = pfc_storm(leaves=4, spines=4, hosts_per_leaf=4)
    storm_flows = s.custom_traffic.flows[:-1]  # all but last (victim)
    for f in storm_flows:
        assert f.packet_count == OPEN_LOOP_PACKETS


def test_pfc_storm_storm_targets_single_host():
    """All storm flows point at the same destination host."""
    s = pfc_storm(storm_target_host=0)
    storm_flows = s.custom_traffic.flows[:-1]
    assert all(f.dst == 0 for f in storm_flows)


def test_pfc_storm_storm_sources_skip_target_leaf():
    """Storm sources must not be on leaf 0 (where the target lives)."""
    s = pfc_storm(leaves=4, spines=4, hosts_per_leaf=4)
    hosts_per_leaf = s.custom_topology.hosts_per_leaf
    storm_flows = s.custom_traffic.flows[:-1]
    # Hosts on leaf 0 are 0 .. hosts_per_leaf-1
    target_leaf_hosts = set(range(hosts_per_leaf))
    sources = {f.src for f in storm_flows}
    assert sources.isdisjoint(target_leaf_hosts)


def test_pfc_storm_victim_starts_after_storm():
    """Victim flow must start later than the storm so the storm has time to develop."""
    s = pfc_storm(storm_start_seconds=0.05, victim_start_seconds=0.2)
    victim = s.custom_traffic.flows[-1]
    storm_starts = {f.start_time_seconds for f in s.custom_traffic.flows[:-1]}
    assert all(victim.start_time_seconds > t for t in storm_starts)


def test_pfc_storm_victim_endpoints_are_off_target_leaf():
    """The victim shares no endpoints with the storm (otherwise it's not a 'victim')."""
    s = pfc_storm(leaves=4, spines=4, hosts_per_leaf=4, storm_target_host=0)
    victim = s.custom_traffic.flows[-1]
    hosts_per_leaf = s.custom_topology.hosts_per_leaf
    target_leaf_hosts = set(range(hosts_per_leaf))
    assert victim.src not in target_leaf_hosts
    assert victim.dst not in target_leaf_hosts


def test_pfc_storm_compiles_end_to_end(tmp_path):
    s = pfc_storm(leaves=4, spines=2, hosts_per_leaf=4, sim_duration_seconds=0.5)
    config_path = compile_scenario(s, tmp_path / "config-burst.txt")
    topo_path = compile_topology(s.custom_topology, tmp_path / "topology.txt")
    traffic_path = compile_traffic(s.custom_traffic, tmp_path / "flow.txt")

    assert config_path.exists()
    assert topo_path.exists()
    assert traffic_path.exists()


def test_pfc_storm_rejects_target_outside_leaf_zero():
    with pytest.raises(ValueError, match="storm_target_host"):
        pfc_storm(leaves=4, spines=4, hosts_per_leaf=4, storm_target_host=10)


# --------------------------------- pfc_storm: layered background traffic

def test_pfc_storm_background_off_by_default():
    """Default behavior unchanged: zero background flows."""
    s = pfc_storm(leaves=4, spines=1, hosts_per_leaf=4)
    storm_source_count = (4 - 1) * 4
    assert len(s.custom_traffic.flows) == storm_source_count + 1  # storm + victim


def test_pfc_storm_background_pairs_emit_cross_leaf_flows():
    """Stage 5a-realistic. With background_pairs_per_leaf>0, generate
    bidirectional cross-leaf flows across every non-storm leaf so the
    fabric baseline shows ECN marks distributed across many ports."""
    s = pfc_storm(
        leaves=4, spines=1, hosts_per_leaf=4,
        background_pairs_per_leaf=2,
    )
    storm_source_count = (4 - 1) * 4
    # 3 non-storm leaves × 2 pairs × 2 directions = 12 background flows
    background_count = (4 - 1) * 2 * 2
    expected = storm_source_count + 1 + background_count
    assert len(s.custom_traffic.flows) == expected


def test_pfc_storm_background_avoids_leaf_zero():
    """Background traffic must never cross leaf 0 — that's where the
    storm target sits, and we want the storm signal isolated from
    background."""
    hosts_per_leaf = 4
    s = pfc_storm(
        leaves=4, spines=1, hosts_per_leaf=hosts_per_leaf,
        background_pairs_per_leaf=2,
    )
    storm_source_count = (4 - 1) * hosts_per_leaf
    background_flows = s.custom_traffic.flows[storm_source_count + 1:]
    leaf_zero_hosts = set(range(hosts_per_leaf))
    for f in background_flows:
        assert f.src not in leaf_zero_hosts, (
            f"background flow originates on leaf 0: {f}"
        )
        assert f.dst not in leaf_zero_hosts, (
            f"background flow targets leaf 0: {f}"
        )


def test_pfc_storm_background_starts_before_storm():
    """Background must be established before the storm fires so the
    fabric already shows baseline ECN activity when the storm begins."""
    storm_t = 0.05
    offset = 0.02
    s = pfc_storm(
        leaves=4, spines=1, hosts_per_leaf=4,
        storm_start_seconds=storm_t,
        background_pairs_per_leaf=2,
        background_start_offset_seconds=offset,
    )
    storm_source_count = (4 - 1) * 4
    background_flows = s.custom_traffic.flows[storm_source_count + 1:]
    assert background_flows  # sanity
    for f in background_flows:
        assert f.start_time_seconds <= storm_t - offset + 1e-9


def test_pfc_storm_background_uses_open_loop():
    """Background flows must run for the simulation duration so the
    baseline persists through the storm period."""
    s = pfc_storm(
        leaves=4, spines=1, hosts_per_leaf=4,
        background_pairs_per_leaf=2,
    )
    storm_source_count = (4 - 1) * 4
    background_flows = s.custom_traffic.flows[storm_source_count + 1:]
    for f in background_flows:
        assert f.packet_count == OPEN_LOOP_PACKETS


def test_pfc_storm_background_uses_distinct_dst_ports():
    """Each background flow must have a unique dst_port so the substrate's
    flow records don't collide. dst_ports are also distinct from storm
    (10_000+) and victim (20_000) ranges."""
    s = pfc_storm(
        leaves=4, spines=1, hosts_per_leaf=4,
        background_pairs_per_leaf=2,
    )
    storm_source_count = (4 - 1) * 4
    background_flows = s.custom_traffic.flows[storm_source_count + 1:]
    bg_ports = [f.dst_port for f in background_flows]
    assert len(bg_ports) == len(set(bg_ports))
    for p in bg_ports:
        assert p >= 30_000


def test_pfc_storm_background_disabled_when_too_few_leaves():
    """The cross-leaf rotation needs at least 2 non-storm leaves to
    create distinct src/dst pairs. With leaves<3 (one storm leaf + at
    most one other) the rotation collapses; emit no background rather
    than self-pair flows that contribute no useful signal."""
    s = pfc_storm(
        leaves=2, spines=1, hosts_per_leaf=4,
        background_pairs_per_leaf=2,
    )
    storm_source_count = (2 - 1) * 4
    # Only storm + victim — no background.
    assert len(s.custom_traffic.flows) == storm_source_count + 1


def test_pfc_storm_realistic_factory_emits_background():
    """The pfc-storm-realistic registry entry must produce background
    flows; otherwise the closing-test re-run measures the same fabric
    shape as the Stage 5a Stage 5a closing test trace."""
    from doppelganger.adapter.server import BUILTIN_SCENARIO_FACTORIES
    s = BUILTIN_SCENARIO_FACTORIES["pfc-storm-realistic"]()
    # 4 leaves default × 3 non-storm leaves × 2 pairs × 2 directions = 12 bg
    storm_source_count = (4 - 1) * 4
    expected_background = (4 - 1) * 2 * 2
    assert (
        len(s.custom_traffic.flows)
        == storm_source_count + 1 + expected_background
    )


# ----------------------------------------------------------- asymmetric_path

def test_asymmetric_path_marks_slow_spine_in_topology():
    s = asymmetric_path(spines=4, slow_spine_index=2)
    assert s.custom_topology.slow_spine_indices == (2,)


def test_asymmetric_path_compiles_with_degraded_links(tmp_path):
    """Compiled topology must have visibly different params for slow-spine links."""
    s = asymmetric_path(leaves=2, spines=2, hosts_per_leaf=2, slow_spine_index=0)

    topo_path = compile_topology(s.custom_topology, tmp_path / "topology.txt")
    text = topo_path.read_text(encoding="utf-8")
    # Slow spine has degraded bandwidth (10 Gbps default, vs 100 Gbps healthy)
    assert "10000000000.0" in text
    assert "100000000000.0" in text


def test_asymmetric_path_flow_set_spans_two_leaves():
    """Flows must run between hosts on different leaves so they actually traverse spines."""
    s = asymmetric_path(leaves=4, spines=4, hosts_per_leaf=4)
    hosts_per_leaf = s.custom_topology.hosts_per_leaf
    leaf_0_hosts = set(range(hosts_per_leaf))
    leaf_1_hosts = set(range(hosts_per_leaf, 2 * hosts_per_leaf))
    for f in s.custom_traffic.flows:
        assert f.src in leaf_0_hosts
        assert f.dst in leaf_1_hosts


def test_asymmetric_path_compiles_end_to_end(tmp_path):
    s = asymmetric_path()
    config = compile_scenario(s, tmp_path / "config-burst.txt")
    topo = compile_topology(s.custom_topology, tmp_path / "topology.txt")
    traffic = compile_traffic(s.custom_traffic, tmp_path / "flow.txt")
    assert config.exists() and topo.exists() and traffic.exists()


# --------------------------------------------------------- hash_polarization

def test_hash_polarization_clusters_dst_ports():
    """The polarization comes from clustered dst_ports."""
    s = hash_polarization(polarized_dst_port_count=2)
    ports = {f.dst_port for f in s.custom_traffic.flows}
    assert len(ports) == 2


def test_hash_polarization_uses_uniform_topology():
    """Topology has no slow spines — the imbalance comes from the flow set."""
    s = hash_polarization()
    assert s.custom_topology.slow_spine_indices == ()


def test_hash_polarization_flows_span_two_leaves():
    s = hash_polarization(leaves=4, hosts_per_leaf=4)
    hosts_per_leaf = s.custom_topology.hosts_per_leaf
    leaf_0_hosts = set(range(hosts_per_leaf))
    leaf_1_hosts = set(range(hosts_per_leaf, 2 * hosts_per_leaf))
    for f in s.custom_traffic.flows:
        assert f.src in leaf_0_hosts
        assert f.dst in leaf_1_hosts


def test_hash_polarization_compiles_end_to_end(tmp_path):
    s = hash_polarization()
    config = compile_scenario(s, tmp_path / "config-burst.txt")
    topo = compile_topology(s.custom_topology, tmp_path / "topology.txt")
    traffic = compile_traffic(s.custom_traffic, tmp_path / "flow.txt")
    assert config.exists() and topo.exists() and traffic.exists()


def test_hash_polarization_metadata_names_root_cause():
    """Eval-set authors should see the polarization framing in scenario metadata."""
    s = hash_polarization(polarized_dst_port_count=3)
    assert "polarization" in s.name.lower() or "ECMP" in s.root_cause
    assert "ECMP" in s.intended_symptom or "asymmetry" in s.intended_symptom.lower()
