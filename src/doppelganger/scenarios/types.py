"""Scenario data types.

A ``Scenario`` is the unit of work a Driver runs. v0.1 exposes the runtime
knobs that vary across the spike-validated scenario set; less-varied knobs
(rate-control timers, EWMA gain, etc.) are baked into the compiler's
template and can be promoted to fields here when a scenario needs to vary
them.

Field defaults are taken from the 2026-05-02 fork-spike's known-good
``config-burst.txt`` (see ``doppelganger/spike/traces/baseline/config.txt``)
so a ``Scenario(name="…", topology=SPIKE_BURST_256)`` with no other fields
set compiles to a config semantically identical to the spike baseline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from doppelganger.scenarios.topology import Topology
from doppelganger.scenarios.traffic import TrafficPattern


@dataclass(frozen=True)
class TopologyRef:
    """Reference to a substrate-bundled topology + flow file pair.

    v0.1 references substrate-bundled examples by their fixed paths inside
    the substrate image. v0.2 will support compiling custom Topology
    declarations to ``topology.txt`` and TrafficPattern declarations to
    ``flow.txt``; until then, scenarios pick from the substrate's pre-built
    examples.

    Paths are *relative to the substrate's NS-3 working directory*
    (``/opt/ns3-datacenter/simulator/ns-3.39`` in the substrate image), not
    absolute container paths.
    """

    name: str
    topology_path: str
    flow_path: str
    description: str = ""


# ECN threshold map entries: (link_speed_bps, threshold_or_probability).
EcnSpeedMap = Sequence[tuple[int, float]]


# Spike-validated defaults for the ECN threshold maps. Three entries each,
# one per common link speed (25 / 50 / 100 Gbps). KMAX/KMIN are integer
# byte thresholds; PMAX is a float marking probability.
_DEFAULT_KMAX: EcnSpeedMap = (
    (25_000_000_000, 400),
    (50_000_000_000, 800),
    (100_000_000_000, 1600),
)
_DEFAULT_KMIN: EcnSpeedMap = (
    (25_000_000_000, 100),
    (50_000_000_000, 200),
    (100_000_000_000, 400),
)
_DEFAULT_PMAX: EcnSpeedMap = (
    (25_000_000_000, 0.2),
    (50_000_000_000, 0.2),
    (100_000_000_000, 0.2),
)


@dataclass
class Scenario:
    """A scenario declaration the compiler turns into ``config-burst.txt``.

    Required:
        name: Short identifier; used for trace-dir naming and metadata.
        topology: Which substrate-bundled fabric + flow file pair to use.

    Knobs that vary across spike-validated scenarios:
        sim_duration_seconds: ``SIMULATOR_STOP_TIME`` in the config.
        link_error_rate: ``ERROR_RATE_PER_LINK`` — 0 baseline, >0 silent drops.
        cc_mode: ``CC_MODE`` — 3 = DCQCN (spike default).
        buffer_size: ``BUFFER_SIZE`` — switch buffer (in the substrate's
            unit; the spike used 4).
        kmax_map / kmin_map / pmax_map: ECN threshold maps.

    Eval ground-truth metadata:
        intended_symptom: What an investigator should observe.
        root_cause: What was actually injected (for eval scoring).
        difficulty: ``"basic" | "intermediate" | "advanced"``.
    """

    name: str
    topology: TopologyRef

    # Optional custom topology / traffic. When present, the Driver
    # compiles them into the per-run trace dir and the emitted
    # config-burst.txt's TOPOLOGY_FILE / FLOW_FILE point at the compiled
    # files (``/traces/topology.txt`` / ``/traces/flow.txt`` in the
    # substrate container). When None, the substrate-bundled paths from
    # ``topology`` (TopologyRef) are used unchanged.
    custom_topology: Optional[Topology] = None
    custom_traffic: Optional[TrafficPattern] = None

    # Knobs that vary scenario-to-scenario
    sim_duration_seconds: float = 0.2
    link_error_rate: float = 0.0
    cc_mode: int = 3
    buffer_size: int = 4

    # ENABLE_QCN gates the substrate's DCQCN rate-control reaction. False
    # disables QCN entirely (senders ignore congestion signals); True (the
    # spike-validated default) leaves DCQCN active. Used by PFC-storm
    # variants to demonstrate what happens when CC fails to react.
    enable_qcn: bool = True

    # Override the spike's MIN_RATE default (100Mb/s). Used by the PFC-storm
    # scenario to start senders at a rate high enough to saturate the
    # bottleneck immediately, since the spike's MIN_RATE was chosen for
    # baseline burst experiments where slow ramp was desirable. None
    # leaves the spike default in place.
    min_rate_override: Optional[str] = None

    kmax_map: EcnSpeedMap = field(default_factory=lambda: tuple(_DEFAULT_KMAX))
    kmin_map: EcnSpeedMap = field(default_factory=lambda: tuple(_DEFAULT_KMIN))
    pmax_map: EcnSpeedMap = field(default_factory=lambda: tuple(_DEFAULT_PMAX))

    # Ground-truth metadata for eval scoring
    intended_symptom: str = ""
    root_cause: str = ""
    difficulty: str = "intermediate"
