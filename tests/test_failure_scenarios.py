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
    compile_scenario,
    compile_topology,
    compile_traffic,
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
