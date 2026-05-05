"""Built-in scenarios: spike-validated baselines and failure-injected variants.

The first two scenarios reproduce what the 2026-05-02 fork spike validated
end-to-end against the substrate's bundled topology + flow files
(``spike_burst_baseline`` and ``spike_burst_silent_drops``). The other
factories construct custom topology + traffic patterns and target the
remaining Stage 1 failure classes per Build Plan v0.3 §2.1: microburst
(synchronized incast) and PFC storm (sustained incast + victim flow).

Each builtin is a factory that returns a fresh Scenario instance so callers
can mutate fields without poisoning the next caller.
"""

from __future__ import annotations

from doppelganger.scenarios.topology import Topology
from doppelganger.scenarios.traffic import OPEN_LOOP_PACKETS, Flow, TrafficPattern
from doppelganger.scenarios.types import Scenario, TopologyRef


SPIKE_BURST_256 = TopologyRef(
    name="spike-burst-256",
    topology_path="examples/PowerTCP/topology-256.txt",
    flow_path="examples/PowerTCP/flow-burstExp-256.txt",
    description=(
        "256-host leaf-spine with the bundled burst flow pattern. "
        "What the 2026-05-02 fork spike validated end-to-end."
    ),
)


def spike_burst_baseline() -> Scenario:
    """Reproduces the spike's baseline run (no failure injection)."""
    return Scenario(
        name="spike-burst-baseline",
        topology=SPIKE_BURST_256,
        sim_duration_seconds=0.2,
        link_error_rate=0.0,
        intended_symptom="No anomalies — baseline reference run",
        root_cause="(none)",
        difficulty="basic",
    )


def spike_burst_silent_drops(rate: float = 0.001) -> Scenario:
    """Reproduces the spike's silent-drops scenario.

    The 2026-05-02 spike injected silent drops at 0.001 (0.1%) and surfaced
    the eval-discipline finding (Doppelgänger v0.2 §6.3): four flows did
    not complete; aggregate FCT statistics on completed flows reported the
    injected run as faster than baseline because the four absent flows were
    the slowest.
    """
    return Scenario(
        name=f"spike-burst-silent-drops-{rate:g}",
        topology=SPIKE_BURST_256,
        sim_duration_seconds=0.2,
        link_error_rate=rate,
        intended_symptom=(
            "A subset of flows fail to complete; surviving flows show "
            "elevated FCT tail and increased retransmissions."
        ),
        root_cause=f"Per-link silent drops at {rate:g} probability per packet.",
        difficulty="intermediate",
    )


# --------------------------------------------------- custom-topology scenarios

# Placeholder TopologyRef used as the ``topology`` field on scenarios that
# also set ``custom_topology``. The Driver ignores the bundled paths when
# custom_topology is set; the field still has to be populated for type
# safety.
_PLACEHOLDER_REF = TopologyRef(
    name="placeholder-overridden-by-custom-topology",
    topology_path="/dev/null",
    flow_path="/dev/null",
    description="Not used; scenario.custom_topology is set.",
)


def microburst(
    *,
    leaves: int = 2,
    spines: int = 4,
    hosts_per_leaf: int = 8,
    burst_start_seconds: float = 0.05,
    sim_duration_seconds: float = 0.2,
    packets_per_flow: int = 5_000,
    priority_group: int = 3,
) -> Scenario:
    """Synchronized incast: every host except 0 sends to host 0 at burst_start.

    The destination's host link (default 25 Gbps) becomes the bottleneck
    when N–1 senders pile in simultaneously. Leaf buffer pressure spikes
    at host 0's leaf; PFC pause frames propagate to source leaves; tail
    FCT shows the microburst signature.

    Bursts are bounded (``packets_per_flow``) so the storm clears within
    the simulation duration; this is the difference from
    :func:`pfc_storm` (open-loop senders with no bound).
    """
    topology = Topology(
        leaves=leaves, spines=spines, hosts_per_leaf=hosts_per_leaf,
    )
    num_hosts = topology.num_hosts

    flows = tuple(
        Flow(
            src=src,
            dst=0,
            priority_group=priority_group,
            dst_port=10_000 + src,
            packet_count=packets_per_flow,
            start_time_seconds=burst_start_seconds,
        )
        for src in range(1, num_hosts)
    )

    return Scenario(
        name=f"microburst-{num_hosts}h",
        topology=_PLACEHOLDER_REF,
        custom_topology=topology,
        custom_traffic=TrafficPattern(
            flows=flows,
            name=f"incast-to-host-0-{num_hosts - 1}-senders",
            description=(
                f"All {num_hosts - 1} non-target hosts send to host 0 "
                f"simultaneously at t={burst_start_seconds}s."
            ),
        ),
        sim_duration_seconds=sim_duration_seconds,
        intended_symptom=(
            f"Sharp tail-FCT spike on flows arriving at host 0's leaf at "
            f"t={burst_start_seconds}s; PFC pause counters spike on the "
            f"source leaves' uplinks; recovery within hundreds of microseconds."
        ),
        root_cause=(
            f"Synchronized incast: {num_hosts - 1} senders → 1 receiver at "
            f"t={burst_start_seconds}s overwhelms host 0's leaf-host link "
            f"buffer."
        ),
        difficulty="intermediate",
    )


def pfc_storm(
    *,
    leaves: int = 4,
    spines: int = 4,
    hosts_per_leaf: int = 4,
    sim_duration_seconds: float = 0.5,
    storm_start_seconds: float = 0.05,
    victim_start_seconds: float = 0.2,
    victim_packets: int = 5_000,
    storm_target_host: int = 0,
    priority_group: int = 3,
) -> Scenario:
    """Persistent congestion → PFC pause propagation → victim flow stalls.

    Two flow populations:

    * **Storm:** every host *not on the storm target's leaf* sends to
      ``storm_target_host`` (default host 0 on leaf 0) starting at
      ``storm_start_seconds``, open-loop (``OPEN_LOOP_PACKETS``). This
      permanently saturates the target's leaf-host link; PFC pause frames
      back up through the leaf, then through spines.
    * **Victim:** a single flow between two hosts on different non-target
      leaves, started later (``victim_start_seconds``). The victim's
      flow path crosses spines that are PFC-paused on their links to the
      storm-receiving leaf; the victim experiences elevated FCT or
      fails to complete.

    The pedagogical lesson is: a flow with no shared endpoints with the
    storm still gets dragged in, because PFC propagation makes congestion
    a fabric-level phenomenon, not a link-level one.
    """
    if storm_target_host >= hosts_per_leaf:
        # Constrain target to leaf 0 for simplicity in this v0.1 scenario.
        # Future expansion: parameterize the storm target's leaf.
        raise ValueError(
            f"storm_target_host {storm_target_host} must be < hosts_per_leaf "
            f"{hosts_per_leaf}; v0.1 places the target on leaf 0."
        )

    topology = Topology(
        leaves=leaves, spines=spines, hosts_per_leaf=hosts_per_leaf,
    )
    num_hosts = topology.num_hosts

    # Storm: every host whose leaf is not 0 sends to the target.
    storm_sources = [
        h for h in range(num_hosts)
        if h // hosts_per_leaf != 0
    ]
    storm_flows = tuple(
        Flow(
            src=src,
            dst=storm_target_host,
            priority_group=priority_group,
            dst_port=10_000 + i,
            packet_count=OPEN_LOOP_PACKETS,
            start_time_seconds=storm_start_seconds,
        )
        for i, src in enumerate(storm_sources)
    )

    # Victim: leaf 1's first host → leaf 2's first host (both off-target).
    # Their path is host → leaf 1 → spine → leaf 2 → host; spines are
    # PFC-paused on their links to leaf 0, which (depending on routing)
    # forces flow control to ripple through the shared spine.
    victim_src = hosts_per_leaf            # first host on leaf 1
    victim_dst = 2 * hosts_per_leaf        # first host on leaf 2
    victim_flow = Flow(
        src=victim_src,
        dst=victim_dst,
        priority_group=priority_group,
        dst_port=20_000,
        packet_count=victim_packets,
        start_time_seconds=victim_start_seconds,
    )

    return Scenario(
        name=f"pfc-storm-{num_hosts}h",
        topology=_PLACEHOLDER_REF,
        custom_topology=topology,
        custom_traffic=TrafficPattern(
            flows=storm_flows + (victim_flow,),
            name=f"storm-to-host-{storm_target_host}-victim-leaf1-to-leaf2",
            description=(
                f"Open-loop incast to host {storm_target_host} from all "
                f"{len(storm_sources)} off-leaf hosts; victim flow host "
                f"{victim_src} → host {victim_dst} starts at "
                f"t={victim_start_seconds}s."
            ),
        ),
        sim_duration_seconds=sim_duration_seconds,
        intended_symptom=(
            f"Victim flow (host {victim_src} → host {victim_dst}) shows "
            f"FCT far above its standalone time, or fails to complete; "
            f"PFC pause counters are elevated across all spines, not just "
            f"on storm-direct links."
        ),
        root_cause=(
            f"PFC pause propagation from host {storm_target_host}'s leaf "
            f"upstream through spines stalls flows that share transit "
            f"switches even when they share no endpoints with the storm."
        ),
        difficulty="advanced",
    )
