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


def asymmetric_path(
    *,
    leaves: int = 4,
    spines: int = 4,
    hosts_per_leaf: int = 4,
    slow_spine_index: int = 0,
    sim_duration_seconds: float = 0.2,
    flow_start_seconds: float = 0.05,
    flows_per_pair: int = 4,
    packets_per_flow: int = 5_000,
    priority_group: int = 3,
) -> Scenario:
    """Differential leaf↔spine link characteristics expose ECMP-hash variance.

    Topology has one degraded ("slow") spine with reduced bandwidth and
    increased delay. Flows are generated in source/destination pairs that
    span leaves; ECMP hashing distributes flows across spines, so flows
    landing on the slow spine experience materially worse FCT than flows
    landing on healthy spines. The agent's job: notice the bimodal FCT
    distribution and trace it to the slow spine via per-link counters.

    The substrate's topology.txt format supports per-link bandwidth and
    delay (verified against ``examples/PowerTCP/topology-256.txt`` and the
    parser at ``powertcp-evaluation-burst.cc:838``); this scenario uses
    that capability rather than scenario-runtime injection.
    """
    topology = Topology(
        leaves=leaves,
        spines=spines,
        hosts_per_leaf=hosts_per_leaf,
        slow_spine_indices=(slow_spine_index,),
    )
    num_hosts = topology.num_hosts

    # Generate flows from leaf 0 hosts to leaf 1 hosts (and back), enough
    # flows that ECMP distribution across spines becomes statistically
    # visible.
    flows: list[Flow] = []
    for src_offset in range(hosts_per_leaf):
        src = src_offset                               # leaf 0
        dst = hosts_per_leaf + src_offset              # leaf 1
        for k in range(flows_per_pair):
            flows.append(Flow(
                src=src,
                dst=dst,
                priority_group=priority_group,
                dst_port=10_000 + src_offset * flows_per_pair + k,
                packet_count=packets_per_flow,
                start_time_seconds=flow_start_seconds,
            ))

    return Scenario(
        name=f"asymmetric-path-{num_hosts}h",
        topology=_PLACEHOLDER_REF,
        custom_topology=topology,
        custom_traffic=TrafficPattern(
            flows=tuple(flows),
            name=f"leaf0-to-leaf1-{flows_per_pair}-flows-per-pair",
            description=(
                f"{hosts_per_leaf}×{flows_per_pair} = "
                f"{hosts_per_leaf * flows_per_pair} flows from leaf 0 to "
                f"leaf 1, ECMP-distributed across {spines} spines (one "
                f"of which is degraded)."
            ),
        ),
        sim_duration_seconds=sim_duration_seconds,
        intended_symptom=(
            f"Bimodal FCT distribution among otherwise-identical flows; "
            f"slower-mode flows correlate with hashing onto spine "
            f"{slow_spine_index}."
        ),
        root_cause=(
            f"Spine {slow_spine_index}'s leaf↔spine links are "
            f"asymmetrically degraded "
            f"(bandwidth {topology.slow_spine_link_bps:,} bps; delay "
            f"{topology.slow_spine_link_delay}). ECMP-hashed flows landing "
            f"on this spine experience worse performance with no "
            f"flow-side cause."
        ),
        difficulty="advanced",
    )


def hash_polarization(
    *,
    leaves: int = 4,
    spines: int = 4,
    hosts_per_leaf: int = 4,
    sim_duration_seconds: float = 0.2,
    flow_start_seconds: float = 0.05,
    packets_per_flow: int = 5_000,
    polarized_dst_port_count: int = 2,
    priority_group: int = 3,
) -> Scenario:
    """Flow population engineered to provoke ECMP hash imbalance.

    Most ECMP implementations hash on the 5-tuple
    (src_ip, dst_ip, proto, src_port, dst_port). This scenario clusters
    ``dst_port`` across a small ``polarized_dst_port_count`` set
    (default 2) for many flows; combined with the substrate's deterministic
    ECMP, the result is per-link counter imbalance — some leaf↔spine
    links carry far more traffic than others despite an identical
    full-mesh topology.

    This is the scenario-authorship variant of the failure class. The
    agent's job: notice that per-link counters are asymmetric across
    spines; trace the asymmetry to the dst_port distribution rather
    than to a topology problem (since none exists).

    Substrate ECMP hash details are implementation-defined; the exact
    polarization pattern depends on what the substrate hashes. This
    scenario produces *a* polarization-prone flow set, not a guaranteed
    pattern. Empirical verification against the substrate's per-link
    counters is part of Stage 1's outstanding investigation backlog
    (Doppelgänger v0.2 §10).
    """
    topology = Topology(leaves=leaves, spines=spines, hosts_per_leaf=hosts_per_leaf)
    num_hosts = topology.num_hosts

    # Each pair (leaf0_host_i, leaf1_host_i) gets several flows on a
    # small set of dst_ports, repeated to make the bias statistically
    # visible.
    flows: list[Flow] = []
    flow_count = 0
    repetitions_per_pair = 4
    for src_offset in range(hosts_per_leaf):
        src = src_offset                                # leaf 0
        dst = hosts_per_leaf + src_offset               # leaf 1
        for k in range(repetitions_per_pair):
            dst_port_offset = k % polarized_dst_port_count
            flows.append(Flow(
                src=src,
                dst=dst,
                priority_group=priority_group,
                dst_port=10_000 + dst_port_offset,
                packet_count=packets_per_flow,
                start_time_seconds=flow_start_seconds,
            ))
            flow_count += 1

    return Scenario(
        name=f"hash-polarization-{num_hosts}h",
        topology=_PLACEHOLDER_REF,
        custom_topology=topology,
        custom_traffic=TrafficPattern(
            flows=tuple(flows),
            name=(
                f"clustered-dst-port-{polarized_dst_port_count}-ports-"
                f"{flow_count}-flows"
            ),
            description=(
                f"{flow_count} flows from leaf 0 to leaf 1 with dst_port "
                f"clustered to {polarized_dst_port_count} values, expected "
                f"to provoke ECMP hash imbalance."
            ),
        ),
        sim_duration_seconds=sim_duration_seconds,
        intended_symptom=(
            f"Per-link counter asymmetry across leaf↔spine links: a "
            f"subset of links carries materially more traffic than "
            f"others despite a uniform topology and identical link "
            f"capacity."
        ),
        root_cause=(
            f"Flow-population bias: {flow_count} flows share a small "
            f"set of {polarized_dst_port_count} dst_ports, producing "
            f"ECMP hash collisions onto a subset of leaf↔spine links."
        ),
        difficulty="advanced",
    )


# -----------------------------------------------------------------------
# Note on link_flap (Doppelgänger v0.2 §5.2 failure class, NOT shipped here)
# -----------------------------------------------------------------------
#
# The substrate (provandal/ns3-datacenter at SHA bff3b9c...) has a
# LINK_DOWN config knob in config-burst.txt — three numbers `time_ns
# node_a node_b`. Reading powertcp-evaluation-burst.cc shows it parses
# these into link_down_time / link_down_A / link_down_B, logs them, and
# *never references them again*. The link-flap mechanism is not wired
# up in this substrate variant; setting LINK_DOWN to non-zero values
# produces no link transition.
#
# Shipping a `link_flap()` factory that emits LINK_DOWN with non-zero
# values would falsely claim functionality the substrate does not
# provide. This is left out deliberately. Adding link-flap support
# requires a substrate-side change (schedule a NetDevice::SetDown call
# at link_down_time) and is filed against Doppelgänger v0.2 §10's
# substrate-investigation backlog. The 2026-05-05 substrate fixes
# closed the pfc.txt / mix.tr / qlen.txt trace-output gaps but left
# the LINK_DOWN issue for a later fork-side commit.
# -----------------------------------------------------------------------


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
