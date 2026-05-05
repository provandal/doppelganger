"""Topology declarations + compiler.

Compiles a leaf-spine ``Topology`` declaration into the substrate's
``topology.txt`` format. The substrate's parser reads four header fields
(``node_num switch_num tors link_num``) where ``tors`` is read but
unused (the parser overwrites it with ``switch_num`` immediately) — see
``examples/PowerTCP/powertcp-evaluation-burst.cc`` line 746–747 in the
pinned substrate fork.

v0.1 supports leaf-spine topologies with full-mesh leaf↔spine
connectivity. Half-Clos and other partial-mesh patterns can be added by
extending ``Topology`` with a custom uplink-mapping function; full mesh
is correct for the failure classes Stage 1 targets (silent drops, PFC
storm, microburst).

Node ID layout in the emitted file:

* Hosts use IDs ``0 .. (leaves * hosts_per_leaf - 1)``.
* Leaf switches use IDs ``(leaves * hosts_per_leaf) .. (leaves * hosts_per_leaf + leaves - 1)``.
* Spine switches use IDs immediately after the leaves.

This matches the substrate's bundled ``topology-256.txt`` convention
(hosts 0–255, leaves 256–271, spines 272–275 for the 256-host example).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class TopologyCompileError(ValueError):
    """Raised when a Topology declaration is invalid."""


@dataclass(frozen=True)
class Topology:
    """A leaf-spine topology declaration.

    Parameters
    ----------
    leaves:
        Number of leaf switches.
    spines:
        Number of spine switches. Each leaf connects to every spine
        (full mesh).
    hosts_per_leaf:
        Hosts attached to each leaf. Total hosts = leaves * hosts_per_leaf.
    host_link_bps:
        Host↔leaf link bandwidth in bits per second. Spike default: 25 Gbps.
    host_link_delay:
        Host↔leaf link delay as a substrate-formatted string
        (e.g., ``"1us"``). Spike default: ``"1us"``.
    spine_link_bps:
        Leaf↔spine link bandwidth in bits per second. Spike default: 100 Gbps.
    spine_link_delay:
        Leaf↔spine link delay as a substrate-formatted string. Spike default: ``"5us"``.
    error_rate:
        Per-link error rate at the topology level (scenario-level
        ``link_error_rate`` lives on the Scenario, not here).
        Default: ``0`` (substrate-format string).
    slow_spine_indices:
        Tuple of spine indices (0-based, where 0 is the first spine in
        ID order) whose leaf↔spine links use degraded parameters. Empty
        tuple = no asymmetry. Used by the asymmetric-path failure class
        to create per-spine-path performance differences that ECMP hashing
        exposes as flow-level FCT variance.
    slow_spine_link_bps:
        Bandwidth for links connecting a slow spine. Default 10 Gbps —
        materially below the 100 Gbps default for healthy spines, so
        ECMP-hashed flows landing on the slow spine see clear FCT
        degradation.
    slow_spine_link_delay:
        Delay for links connecting a slow spine. Default ``"50us"`` —
        10× the healthy spine delay.
    """

    leaves: int
    spines: int
    hosts_per_leaf: int
    host_link_bps: int = 25_000_000_000
    host_link_delay: str = "1us"
    spine_link_bps: int = 100_000_000_000
    spine_link_delay: str = "5us"
    error_rate: str = "0"
    slow_spine_indices: tuple[int, ...] = ()
    slow_spine_link_bps: int = 10_000_000_000
    slow_spine_link_delay: str = "50us"

    @property
    def num_hosts(self) -> int:
        return self.leaves * self.hosts_per_leaf

    @property
    def num_switches(self) -> int:
        return self.leaves + self.spines

    @property
    def num_nodes(self) -> int:
        return self.num_hosts + self.num_switches

    @property
    def num_links(self) -> int:
        return self.num_hosts + (self.leaves * self.spines)

    def first_leaf_id(self) -> int:
        return self.num_hosts

    def first_spine_id(self) -> int:
        return self.num_hosts + self.leaves


def compile_topology(topology: Topology, output_path: Path) -> Path:
    """Compile a Topology into a substrate ``topology.txt`` file.

    Raises
    ------
    TopologyCompileError
        If the Topology has invalid dimensions.
    """
    _validate(topology)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []

    # Header: node_num switch_num tors link_num
    # The substrate's parser reads 4 fields but overwrites tors=switch_num
    # immediately. We emit switch_num twice for clarity.
    lines.append(
        f"{topology.num_nodes} {topology.num_switches} "
        f"{topology.num_switches} {topology.num_links}"
    )

    # Switch IDs: leaves first, then spines
    first_leaf = topology.first_leaf_id()
    switch_ids = list(range(first_leaf, first_leaf + topology.num_switches))
    lines.append(" ".join(str(s) for s in switch_ids))

    # Host↔leaf links
    bw_host = _format_bps(topology.host_link_bps)
    for host_id in range(topology.num_hosts):
        leaf_id = first_leaf + host_id // topology.hosts_per_leaf
        lines.append(
            f"{host_id} {leaf_id} {bw_host} "
            f"{topology.host_link_delay} {topology.error_rate}"
        )

    # Leaf↔spine links: full mesh. Degraded ("slow") spines emit links
    # with downgraded bandwidth/delay so ECMP-hashed flows landing on
    # those spines see materially worse FCT.
    bw_spine = _format_bps(topology.spine_link_bps)
    bw_slow = _format_bps(topology.slow_spine_link_bps)
    first_spine = topology.first_spine_id()
    slow_spines = set(topology.slow_spine_indices)
    for leaf_offset in range(topology.leaves):
        leaf_id = first_leaf + leaf_offset
        for spine_offset in range(topology.spines):
            spine_id = first_spine + spine_offset
            if spine_offset in slow_spines:
                bw, delay = bw_slow, topology.slow_spine_link_delay
            else:
                bw, delay = bw_spine, topology.spine_link_delay
            lines.append(
                f"{leaf_id} {spine_id} {bw} "
                f"{delay} {topology.error_rate}"
            )

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def _validate(topology: Topology) -> None:
    if topology.leaves < 1:
        raise TopologyCompileError(f"leaves must be >=1, got {topology.leaves}")
    if topology.spines < 1:
        raise TopologyCompileError(f"spines must be >=1, got {topology.spines}")
    if topology.hosts_per_leaf < 1:
        raise TopologyCompileError(
            f"hosts_per_leaf must be >=1, got {topology.hosts_per_leaf}"
        )
    if topology.host_link_bps <= 0:
        raise TopologyCompileError(
            f"host_link_bps must be positive, got {topology.host_link_bps}"
        )
    if topology.spine_link_bps <= 0:
        raise TopologyCompileError(
            f"spine_link_bps must be positive, got {topology.spine_link_bps}"
        )
    if topology.slow_spine_link_bps <= 0:
        raise TopologyCompileError(
            f"slow_spine_link_bps must be positive, got "
            f"{topology.slow_spine_link_bps}"
        )
    for idx in topology.slow_spine_indices:
        if not 0 <= idx < topology.spines:
            raise TopologyCompileError(
                f"slow_spine_indices entry {idx} out of range "
                f"[0, {topology.spines})"
            )


def _format_bps(bps: int) -> str:
    """Substrate-format link bandwidth: ``25000000000.0`` (float-as-text)."""
    return f"{float(bps):.1f}"
