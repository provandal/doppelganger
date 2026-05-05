"""Doppelgänger Adapter — thin MCP server importing the Driver.

Per Doppelgänger v0.2 §1.4 / §9.1, every Substrate Adapter has a Driver
(pure-Python wrapper around the substrate) and an Adapter shell (MCP
server delegating to the Driver). v0.1 of the Adapter exposes three
tools:

* ``list_scenarios`` — names of built-in scenarios available to run.
* ``run_scenario`` — run a named scenario; return summary + flow count +
  trace directory location.
* ``compare_runs`` — re-parse two completed runs' ``fct.txt`` files and
  return the comparison findings.

The Adapter does not currently expose parameterized scenario authoring
(microburst/pfc_storm with custom args). v0.2 of the Adapter will accept
serialized Scenario JSON; for v0.1 the canonical scenario set is the
default-parameter instances exposed by name.

Run the server on stdio:

    python -m doppelganger.adapter

Or programmatically::

    from doppelganger.adapter import build_server
    server = build_server()
    server.run()  # blocks; reads/writes MCP messages on stdio
"""

from doppelganger.adapter.server import (
    BUILTIN_SCENARIO_FACTORIES,
    build_server,
    envelope,
)

__all__ = ["build_server", "envelope", "BUILTIN_SCENARIO_FACTORIES"]
