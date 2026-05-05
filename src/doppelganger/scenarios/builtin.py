"""Built-in scenarios: spike-validated baselines and failure-injected variants.

These are the scenarios v0.1 of the Driver can run end-to-end against the
substrate without needing custom topology / flow files. Each builtin is a
factory that returns a fresh Scenario instance so callers can mutate without
poisoning the next caller.
"""

from __future__ import annotations

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
