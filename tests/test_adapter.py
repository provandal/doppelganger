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
from doppelganger.adapter.server import _scenario_to_topology_payload
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
    """The server must expose list_scenarios, run_scenario, get_topology, compare_runs."""
    server = build_server()
    # FastMCP exposes registered tools via list_tools (async); we go through
    # the tool manager directly for sync access in tests.
    tool_names = set(server._tool_manager._tools.keys())  # type: ignore[attr-defined]
    assert "list_scenarios" in tool_names
    assert "run_scenario" in tool_names
    assert "get_topology" in tool_names
    assert "compare_runs" in tool_names


def test_server_takes_custom_name():
    server = build_server(server_name="custom-test-name")
    assert server.name == "custom-test-name"


# --------------------------------------------------- get_topology payload

def test_get_topology_microburst_returns_full_structure():
    """Microburst has a custom_topology, so get_topology should return
    the full structural payload — leaves, spines, switches, links, CC mode."""
    scenario = BUILTIN_SCENARIO_FACTORIES["microburst"]()
    payload = _scenario_to_topology_payload(scenario, "microburst")

    assert payload["shape"] == "leaf-spine"
    assert payload["leaves"] == 2
    assert payload["spines"] == 4
    assert payload["hosts_per_leaf"] == 8
    assert payload["total_hosts"] == 16

    # Per-leaf host_ids should partition all 16 hosts contiguously
    leaves = payload["leaf_switches"]
    assert len(leaves) == 2
    assert leaves[0]["host_ids"] == list(range(0, 8))
    assert leaves[1]["host_ids"] == list(range(8, 16))
    # Leaf node IDs come right after the hosts
    assert leaves[0]["node_id"] == 16
    assert leaves[1]["node_id"] == 17

    spines = payload["spine_switches"]
    assert len(spines) == 4
    assert [s["node_id"] for s in spines] == [18, 19, 20, 21]

    assert payload["host_link"]["bps"] == 25_000_000_000
    assert payload["spine_link"]["bps"] == 100_000_000_000
    assert payload["asymmetry"]["present"] is False
    assert payload["asymmetry"]["slow_spine_indices"] == []

    cc = payload["congestion_control"]
    assert cc["cc_mode"] == 3
    assert cc["name"] == "DCQCN"
    assert cc["qcn_enabled"] is True


def test_get_topology_pfc_storm_reflects_task8_defaults():
    """pfc_storm() default is spines=1 (post-Task #8). get_topology must
    reflect that without hardcoding 'leaf-spine 2x4'."""
    scenario = BUILTIN_SCENARIO_FACTORIES["pfc-storm"]()
    payload = _scenario_to_topology_payload(scenario, "pfc-storm")

    assert payload["spines"] == 1, (
        "Task #8 dropped pfc_storm default to spines=1; get_topology must "
        "report it honestly rather than assuming a multi-spine fabric."
    )
    assert payload["leaves"] >= 1
    assert len(payload["spine_switches"]) == 1


def test_get_topology_asymmetric_path_surfaces_slow_spine():
    """asymmetric_path() configures slow_spine_indices; the asymmetry
    block must surface that for the agent to reason about ECMP-driven
    FCT variance."""
    scenario = BUILTIN_SCENARIO_FACTORIES["asymmetric-path"]()
    payload = _scenario_to_topology_payload(scenario, "asymmetric-path")

    asymmetry = payload["asymmetry"]
    assert asymmetry["present"] is True
    assert len(asymmetry["slow_spine_indices"]) >= 1
    assert asymmetry["slow_link_bps"] is not None
    assert asymmetry["slow_link_delay"] is not None


def test_get_topology_substrate_bundled_scenario_returns_degraded_payload():
    """spike-burst-baseline pins to a substrate-bundled topology file;
    structured fields aren't introspected. The payload must be honest
    about that — not synthesize fake structure."""
    scenario = BUILTIN_SCENARIO_FACTORIES["spike-burst-baseline"]()
    payload = _scenario_to_topology_payload(scenario, "spike-burst-baseline")

    assert payload["shape"] == "substrate-bundled"
    assert payload["topology_file"].endswith("topology-256.txt")
    assert "introspection" in payload
    # Must NOT claim a structured leaves/spines count it doesn't actually know
    assert "leaves" not in payload
    assert "spines" not in payload


def test_get_topology_payload_does_not_leak_eval_ground_truth():
    """intended_symptom, root_cause, AND scenario_name are eval-time
    ground truth and must NOT appear in the agent-facing topology
    payload — that's the Stage 2 v1 mistake (system prompt enumerated
    failure classes) and the Stage 3 first-pass mistake (scenario name
    in the topology data; the model read it back as 'declared scenario:
    microburst' rather than deducing the symptom from structure).

    Fabrics are not named after their failure modes. An SRE querying
    their fabric topology does not get back a "scenario: foo" label.
    """
    for name in ("microburst", "pfc-storm", "asymmetric-path", "hash-polarization"):
        scenario = BUILTIN_SCENARIO_FACTORIES[name]()
        payload = _scenario_to_topology_payload(scenario, name)
        # The scenario object DOES carry these fields; the payload must filter them
        assert scenario.intended_symptom, f"{name} should have ground-truth metadata for eval"
        assert scenario.root_cause, f"{name} should have ground-truth metadata for eval"
        assert "intended_symptom" not in payload, f"{name} payload leaks intended_symptom"
        assert "root_cause" not in payload, f"{name} payload leaks root_cause"
        assert "difficulty" not in payload, f"{name} payload leaks difficulty"
        # The substrate scenario name must NOT appear — also leaked the
        # answer key in the Stage 3 first-pass live run on 2026-05-07.
        assert "scenario" not in payload, (
            f"{name} payload leaks the scenario name (answer key); "
            f"the model reads it as 'declared scenario: {name}'"
        )
        # And no field whose value is the scenario name in disguise
        for key, value in payload.items():
            if isinstance(value, str):
                assert value != name, (
                    f"{name} payload field {key!r} == scenario name; "
                    f"likely leaks the answer key"
                )
