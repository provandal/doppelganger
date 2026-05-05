"""Tests for the Topology → topology.txt compiler."""

from __future__ import annotations

import pytest

from doppelganger.scenarios import Topology, TopologyCompileError, compile_topology


def _parse(text: str) -> dict:
    """Parse a topology.txt file into a structured dict for assertions."""
    lines = [line for line in text.splitlines() if line.strip()]
    header = lines[0].split()
    out = {
        "node_num": int(header[0]),
        "switch_num": int(header[1]),
        "tors": int(header[2]),
        "link_num": int(header[3]),
        "switch_ids": [int(x) for x in lines[1].split()],
        "links": [line.split() for line in lines[2:]],
    }
    return out


def test_compile_minimal_topology(tmp_path):
    """Smallest possible: 1 leaf, 1 spine, 1 host."""
    topo = Topology(leaves=1, spines=1, hosts_per_leaf=1)
    out = compile_topology(topo, tmp_path / "topology.txt")
    parsed = _parse(out.read_text(encoding="utf-8"))

    assert parsed["node_num"] == 3      # 1 host + 1 leaf + 1 spine
    assert parsed["switch_num"] == 2
    assert parsed["link_num"] == 2      # 1 host-leaf + 1 leaf-spine
    assert parsed["switch_ids"] == [1, 2]


def test_compile_spike_dimensions(tmp_path):
    """Topology with the spike's 256-host dimensions.

    Substrate bundled file uses half-Clos (32 leaf-spine links); we use
    full mesh, which gives 16*4=64 leaf-spine links. The substrate
    accepts both — the format is uniform.
    """
    topo = Topology(leaves=16, spines=4, hosts_per_leaf=16)
    out = compile_topology(topo, tmp_path / "topology.txt")
    parsed = _parse(out.read_text(encoding="utf-8"))

    assert parsed["node_num"] == 276    # 256 + 16 + 4
    assert parsed["switch_num"] == 20
    assert parsed["tors"] == 20
    assert parsed["link_num"] == 256 + 64  # hosts + (full mesh leaf-spine)
    # Switch ID range: leaves 256-271, spines 272-275
    assert parsed["switch_ids"] == list(range(256, 276))


def test_link_bandwidth_format_matches_substrate(tmp_path):
    """Bandwidth must be float-as-text; substrate's parser does ``>>`` into a string."""
    topo = Topology(leaves=2, spines=1, hosts_per_leaf=1)
    out = compile_topology(topo, tmp_path / "topology.txt")
    parsed = _parse(out.read_text(encoding="utf-8"))

    # First link: host 0 → leaf 2 at 25 Gbps
    first = parsed["links"][0]
    assert first[0] == "0"
    assert first[1] == "2"
    assert first[2] == "25000000000.0"     # float-as-text matches substrate format
    assert first[3] == "1us"
    assert first[4] == "0"


def test_full_mesh_leaf_spine_connectivity(tmp_path):
    """With L leaves and S spines, every leaf must connect to every spine."""
    topo = Topology(leaves=3, spines=2, hosts_per_leaf=2)
    out = compile_topology(topo, tmp_path / "topology.txt")
    parsed = _parse(out.read_text(encoding="utf-8"))

    # Leaves: 6, 7, 8; spines: 9, 10
    leaf_ids = [6, 7, 8]
    spine_ids = [9, 10]

    leaf_spine_links = [
        (int(link[0]), int(link[1]))
        for link in parsed["links"]
        if int(link[0]) in leaf_ids and int(link[1]) in spine_ids
    ]
    assert sorted(leaf_spine_links) == sorted(
        (l, s) for l in leaf_ids for s in spine_ids
    )


def test_host_to_leaf_assignment(tmp_path):
    """Hosts 0..H-1 connect to their leaf in groups of hosts_per_leaf."""
    topo = Topology(leaves=2, spines=1, hosts_per_leaf=3)
    out = compile_topology(topo, tmp_path / "topology.txt")
    parsed = _parse(out.read_text(encoding="utf-8"))

    # Host-to-leaf links (src is host, dst is leaf 6 or 7)
    host_links = {
        int(link[0]): int(link[1])
        for link in parsed["links"]
        if int(link[0]) < 6  # hosts are 0..5
    }
    assert host_links == {
        0: 6, 1: 6, 2: 6,   # first three hosts → leaf 6
        3: 7, 4: 7, 5: 7,   # next three → leaf 7
    }


def test_link_counts_match_header(tmp_path):
    """The header's link_num must equal the actual count of link lines emitted."""
    topo = Topology(leaves=4, spines=3, hosts_per_leaf=8)
    out = compile_topology(topo, tmp_path / "topology.txt")
    parsed = _parse(out.read_text(encoding="utf-8"))

    assert len(parsed["links"]) == parsed["link_num"]


@pytest.mark.parametrize("leaves,spines,hosts_per_leaf", [
    (0, 1, 1),
    (1, 0, 1),
    (1, 1, 0),
    (-1, 1, 1),
])
def test_invalid_dimensions_rejected(tmp_path, leaves, spines, hosts_per_leaf):
    bad = Topology(
        leaves=leaves, spines=spines, hosts_per_leaf=hosts_per_leaf
    )
    with pytest.raises(TopologyCompileError):
        compile_topology(bad, tmp_path / "topology.txt")


def test_zero_bandwidth_rejected(tmp_path):
    bad = Topology(leaves=1, spines=1, hosts_per_leaf=1, host_link_bps=0)
    with pytest.raises(TopologyCompileError, match="host_link_bps"):
        compile_topology(bad, tmp_path / "topology.txt")
