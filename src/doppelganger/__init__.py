"""Doppelgänger — the NS-3 Substrate Adapter for HarnessIT."""

from doppelganger.driver import (
    CompletionStatus,
    Driver,
    DriverError,
    PerFlowRecord,
    SimulationResult,
)
from doppelganger.eval import (
    ComparisonResult,
    FctDistribution,
    RunSummary,
    compare_runs,
    summarize_run,
)
from doppelganger.scenarios import (
    OPEN_LOOP_PACKETS,
    SPIKE_BURST_256,
    Flow,
    Scenario,
    ScenarioCompileError,
    Topology,
    TopologyCompileError,
    TopologyRef,
    TrafficCompileError,
    TrafficPattern,
    compile_scenario,
    compile_topology,
    compile_traffic,
    spike_burst_baseline,
    spike_burst_silent_drops,
)

__version__ = "0.1.0.dev0"

__all__ = [
    "Driver",
    "DriverError",
    "SimulationResult",
    "PerFlowRecord",
    "CompletionStatus",
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
    "ComparisonResult",
    "FctDistribution",
    "RunSummary",
    "compare_runs",
    "summarize_run",
]
