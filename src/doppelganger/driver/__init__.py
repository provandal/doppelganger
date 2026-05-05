"""Doppelgänger Driver — pure-Python wrapper around the NS-3 substrate.

The Driver compiles topology + scenario declarations into the substrate's
text configuration format, invokes the simulator binary as a subprocess, and
parses output trace files. It exposes parsed data through plain-Python methods,
testable in isolation without MCP scaffolding.

The Adapter (separate package, future commit) is a thin MCP server that imports
the Driver and registers MCP tools that delegate to Driver methods.

See `Doppelganger_Design_v0.2.md` §9.1 for the architectural framing.
"""

from doppelganger.driver.simulation import Driver, DriverError, SimulationResult
from doppelganger.driver.types import CompletionStatus, PerFlowRecord

__all__ = [
    "Driver",
    "DriverError",
    "SimulationResult",
    "PerFlowRecord",
    "CompletionStatus",
]
