"""Tests for the Doppelgänger MCP Adapter.

The Adapter is intentionally thin — most of its logic is "delegate to
Driver and wrap in the response envelope." Tests verify:

* Tool registration: the expected tools exist on the FastMCP server.
* Envelope shape: the response-envelope helper produces the contract
  fields per Doppelgänger v0.2 §2.3.
* Built-in scenario factory registry: all factories produce valid
  Scenario objects.

End-to-end MCP-protocol tests against an MCP client are out of scope
for this commit; they belong with HarnessIT's first integration step
where a real client exists to issue requests.
"""

from __future__ import annotations

import pytest

from doppelganger.adapter import (
    BUILTIN_SCENARIO_FACTORIES,
    build_server,
    envelope,
)
from doppelganger.scenarios.types import Scenario


# ----------------------------------------------------------- envelope shape

def test_envelope_returns_required_fields():
    e = envelope("payload", source="test.source")
    assert e["data"] == "payload"
    assert e["source"] == "test.source"
    assert e["confidence"] == "high"
    assert e["staleness_class"] == "fresh"
    assert e["observed_at_ns"] is None


def test_envelope_passes_through_observed_at_and_staleness():
    e = envelope([], source="trace", observed_at_ns=12_345, staleness_class="stale")
    assert e["observed_at_ns"] == 12_345
    assert e["staleness_class"] == "stale"


def test_envelope_confidence_always_high():
    """Doppelgänger v0.2 §2.3: confidence is always 'high' for simulated data."""
    e = envelope({}, source="anywhere")
    assert e["confidence"] == "high"


# --------------------------------------------------- scenario registry

def test_factory_registry_has_all_named_scenarios():
    expected = {
        "spike-burst-baseline", "spike-burst-silent-drops",
        "microburst", "pfc-storm",
        "asymmetric-path", "hash-polarization",
    }
    assert set(BUILTIN_SCENARIO_FACTORIES) == expected


def test_each_factory_produces_a_scenario():
    """Every factory in the registry returns a fresh Scenario instance."""
    for name, factory in BUILTIN_SCENARIO_FACTORIES.items():
        scenario = factory()
        assert isinstance(scenario, Scenario), (
            f"factory {name} returned {type(scenario).__name__}"
        )
        assert scenario.name, f"factory {name} returned a scenario with empty name"


def test_factories_return_fresh_instances():
    """Calling the same factory twice must not return the same object."""
    s1 = BUILTIN_SCENARIO_FACTORIES["microburst"]()
    s2 = BUILTIN_SCENARIO_FACTORIES["microburst"]()
    assert s1 is not s2


# --------------------------------------------------------- server setup

def test_build_server_returns_fastmcp_instance():
    server = build_server()
    # FastMCP is the public type; we verify by attribute rather than isinstance
    # since the import path may evolve in the upstream mcp package.
    assert hasattr(server, "run")
    assert hasattr(server, "tool")


def test_server_registers_expected_tools():
    """The server must expose list_scenarios, run_scenario, and compare_runs."""
    server = build_server()
    # FastMCP exposes registered tools via list_tools (async); we go through
    # the tool manager directly for sync access in tests.
    tool_names = set(server._tool_manager._tools.keys())  # type: ignore[attr-defined]
    assert "list_scenarios" in tool_names
    assert "run_scenario" in tool_names
    assert "compare_runs" in tool_names


def test_server_takes_custom_name():
    server = build_server(server_name="custom-test-name")
    assert server.name == "custom-test-name"
