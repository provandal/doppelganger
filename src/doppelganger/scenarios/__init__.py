"""Scenario authoring + compilation for the Doppelgänger substrate.

A *Scenario* is a Python declaration of what the substrate should simulate:
which fabric (TopologyRef), which traffic pattern, simulation duration,
congestion-control settings, ECN/PFC tuning, failure injection, and
ground-truth metadata.

The compiler takes a Scenario and produces a ``config-burst.txt`` file the
substrate consumes. v0.1 of the compiler emits config-burst.txt only;
TopologyRef references substrate-bundled topology and flow files at fixed
paths inside the substrate image. Compiling custom topology and flow files
(turning a ``Topology`` Python declaration into ``topology.txt``, and a
``TrafficPattern`` declaration into ``flow.txt``) is a future commit.

See ``Doppelganger_Design_v0.2.md`` §5.3 (scenario authorship) and §9.1
(Driver/Adapter split) for the architectural framing.
"""

from doppelganger.scenarios.builtin import (
    SPIKE_BURST_256,
    asymmetric_path,
    hash_polarization,
    microburst,
    pfc_storm,
    spike_burst_baseline,
    spike_burst_silent_drops,
)
from doppelganger.scenarios.compiler import (
    ScenarioCompileError,
    compile_scenario,
)
from doppelganger.scenarios.topology import (
    Topology,
    TopologyCompileError,
    compile_topology,
)
from doppelganger.scenarios.traffic import (
    OPEN_LOOP_PACKETS,
    Flow,
    TrafficCompileError,
    TrafficPattern,
    compile_traffic,
)
from doppelganger.scenarios.types import Scenario, TopologyRef

__all__ = [
    "Scenario",
    "TopologyRef",
    "compile_scenario",
    "ScenarioCompileError",
    "Topology",
    "compile_topology",
    "TopologyCompileError",
    "Flow",
    "TrafficPattern",
    "compile_traffic",
    "TrafficCompileError",
    "OPEN_LOOP_PACKETS",
    "SPIKE_BURST_256",
    "spike_burst_baseline",
    "spike_burst_silent_drops",
    "microburst",
    "pfc_storm",
    "asymmetric_path",
    "hash_polarization",
]
