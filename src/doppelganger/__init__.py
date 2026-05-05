"""Doppelgänger — the NS-3 Substrate Adapter for HarnessIT."""

from doppelganger.driver import (
    CompletionStatus,
    Driver,
    DriverError,
    PerFlowRecord,
    SimulationResult,
)
from doppelganger.scenarios import (
    SPIKE_BURST_256,
    Scenario,
    ScenarioCompileError,
    TopologyRef,
    compile_scenario,
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
    "SPIKE_BURST_256",
    "spike_burst_baseline",
    "spike_burst_silent_drops",
]
