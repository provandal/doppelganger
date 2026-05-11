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

from pathlib import Path

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
        "microburst", "pfc-storm", "pfc-storm-realistic",
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
    """The server must expose all six adapter tools."""
    server = build_server()
    # FastMCP exposes registered tools via list_tools (async); we go through
    # the tool manager directly for sync access in tests.
    tool_names = set(server._tool_manager._tools.keys())  # type: ignore[attr-defined]
    assert "list_scenarios" in tool_names
    assert "run_scenario" in tool_names
    assert "get_topology" in tool_names
    assert "get_fabric_counters" in tool_names
    assert "get_flow_records" in tool_names
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

    # Per-leaf hosts should partition all 16 hosts contiguously, with IPs
    # following the substrate's node_id_to_ip convention: id N → 11.0.N.1
    # (within a /16 block — fourth octet = 1, third octet = id mod 256).
    leaves = payload["leaf_switches"]
    assert len(leaves) == 2
    assert [h["id"] for h in leaves[0]["hosts"]] == list(range(0, 8))
    assert [h["id"] for h in leaves[1]["hosts"]] == list(range(8, 16))
    # Spot-check IPs at the leaf boundary — the bug we're guarding against
    # is the agent assuming 11.0.0.x → host_id x or any other naive scheme.
    leaf0_ips = [h["ip"] for h in leaves[0]["hosts"]]
    leaf1_ips = [h["ip"] for h in leaves[1]["hosts"]]
    assert leaf0_ips[0] == "11.0.0.1"   # host_id 0
    assert leaf0_ips[1] == "11.0.1.1"   # host_id 1
    assert leaf0_ips[7] == "11.0.7.1"   # host_id 7
    assert leaf1_ips[0] == "11.0.8.1"   # host_id 8
    assert leaf1_ips[7] == "11.0.15.1"  # host_id 15

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

    # Top-level convention string for cross-scenario reuse
    assert "11." in payload["host_ip_convention"]
    assert "node_id_to_ip" in payload["host_ip_convention"]


def test_host_id_to_ip_matches_substrate_node_id_to_ip():
    """The Python mirror of the substrate's node_id_to_ip must match
    its formula exactly. Source:
    ``examples/PowerTCP/powertcp-evaluation-burst.cc`` line 170::

        Ipv4Address(0x0b000001 + ((id / 256) * 0x00010000)
                                + ((id % 256) * 0x00000100));

    If this drifts, every harness-side IP-to-host bridging breaks
    silently — the agent gets wrong topology, but the eval still runs.
    """
    from doppelganger.adapter.server import _host_id_to_ip

    # Spot-check across the byte boundaries that the substrate's
    # divmod-by-256 formula crosses.
    cases = [
        (0, "11.0.0.1"),
        (1, "11.0.1.1"),
        (7, "11.0.7.1"),
        (8, "11.0.8.1"),
        (15, "11.0.15.1"),
        (255, "11.0.255.1"),
        (256, "11.1.0.1"),
        (257, "11.1.1.1"),
        (511, "11.1.255.1"),
        (512, "11.2.0.1"),
    ]
    for host_id, expected_ip in cases:
        actual = _host_id_to_ip(host_id)
        assert actual == expected_ip, (
            f"host_id {host_id}: substrate's node_id_to_ip would produce "
            f"{expected_ip}, our mirror produced {actual!r}"
        )


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


# ----------------------------------------------- response-shape leak guards


def _walk_strings(obj):
    """Yield every string value reachable in a nested data structure."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk_strings(v)


def test_get_fabric_counters_response_does_not_leak_scenario_name():
    """Stage 5a-realistic closing-test bug (trace
    3ef43138e182c9c84d41f35cc9a353b0, 2026-05-09): the fabric counters
    response contained "scenario": "pfc-storm-16h", which the agent
    quoted directly as "Scenario tag in the counter dump literally
    reads pfc-storm-16h." The response data — including any
    auto-generated run_id — must contain no string equal to or
    containing the substrate scenario name.
    """
    from doppelganger.adapter.server import build_server
    from doppelganger.driver.simulation import Driver

    # Non-runnable Driver — we don't actually exercise the substrate;
    # we exercise only the part of the path that builds the response
    # for an in-process scenario lookup. The leaked field was always
    # the data-side scenario string, never the substrate output.
    driver = Driver(substrate_image="bogus-image")
    server = build_server(driver=driver)
    tool = server._tool_manager._tools["get_fabric_counters"]  # type: ignore[attr-defined]
    # We cannot call tool.fn() because it triggers a real subprocess;
    # instead, assert the docstring and the data-key set we ship for
    # any future change to this surface.
    assert tool.fn.__doc__, "get_fabric_counters needs a docstring"
    import ast
    import inspect
    import textwrap
    src = textwrap.dedent(inspect.getsource(tool.fn))
    tree = ast.parse(src)
    # Find every dict literal in the response and check none of its
    # static keys is "scenario" or "trace_dir".
    # `scenario` is banned because it's the literal scenario name
    # (e.g. "pfc-storm-16h"). `trace_dir` is intentionally allowed —
    # callers (HarnessIT runner) need it for compare_runs plumbing,
    # and the path is non-leaky as long as run_ids are UUID-style
    # (Driver auto-generates that pattern; HarnessIT runner does too
    # since 2026-05-10).
    leaky_keys = {"scenario"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and k.value in leaky_keys:
                    raise AssertionError(
                        f"get_fabric_counters response builds a dict with "
                        f"key {k.value!r} — this surfaces the scenario name "
                        f"to the agent. Drop the field."
                    )


def test_run_scenario_response_does_not_leak_scenario_name():
    """Same leak guard as get_fabric_counters but for run_scenario.
    Both tools previously emitted "scenario" + "trace_dir" data fields;
    both must drop them so auto-generated run_ids (UUID-prefixed) are
    the only run-identifying string the agent sees.
    """
    from doppelganger.adapter.server import build_server
    from doppelganger.driver.simulation import Driver

    driver = Driver(substrate_image="bogus-image")
    server = build_server(driver=driver)
    tool = server._tool_manager._tools["run_scenario"]  # type: ignore[attr-defined]
    import ast
    import inspect
    import textwrap
    src = textwrap.dedent(inspect.getsource(tool.fn))
    tree = ast.parse(src)
    # `scenario` is banned because it's the literal scenario name
    # (e.g. "pfc-storm-16h"). `trace_dir` is intentionally allowed —
    # callers (HarnessIT runner) need it for compare_runs plumbing,
    # and the path is non-leaky as long as run_ids are UUID-style
    # (Driver auto-generates that pattern; HarnessIT runner does too
    # since 2026-05-10).
    leaky_keys = {"scenario"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and k.value in leaky_keys:
                    raise AssertionError(
                        f"run_scenario response builds a dict with "
                        f"key {k.value!r} — leaks scenario name to agent."
                    )


def test_driver_auto_run_id_does_not_embed_scenario_name(tmp_path):
    """Driver.run_scenario with run_id=None must auto-generate an ID
    that does NOT contain the scenario name. The previous pattern was
    f"{scenario_name}-{int(time.time())}" — so the generated trace
    directory name read e.g. "pfc-storm-16h-1746719876", and adapter
    response surfaces that to the agent.
    """
    from doppelganger.driver.simulation import Driver
    from doppelganger.scenarios.builtin import pfc_storm

    driver = Driver(substrate_image="bogus-image", traces_root=tmp_path)
    scenario = pfc_storm()  # name is "pfc-storm-16h"

    # Exercise the resolver via the private prep method to avoid
    # spinning a subprocess.
    _name, _cmd, _cfg, trace_dir = driver._prepare_run(  # type: ignore[attr-defined]
        scenario, run_id=None,
    )
    assert "pfc-storm" not in trace_dir.name, (
        f"auto-generated run_id {trace_dir.name!r} contains scenario "
        f"name; this is the leak vector"
    )

    _name, _cmd, _cfg, trace_dir = driver._prepare_run(  # type: ignore[attr-defined]
        "spike-burst", run_id=None,
    )
    assert "spike-burst" not in trace_dir.name, (
        f"auto-generated run_id {trace_dir.name!r} contains 'spike-burst'"
    )


# ---------------------------------------------- get_flow_records


def test_get_flow_records_response_does_not_leak_scenario_name():
    """Mirror of test_get_fabric_counters_response_does_not_leak_scenario_name
    for the new get_flow_records tool. Same AST-walk: no dict literal in
    the response builder may carry a `scenario` key. trace_dir is
    allowed because (a) HarnessIT runner needs it for compare_runs
    plumbing in paired scenarios, and (b) the path is non-leaky as long
    as run_ids are UUID-style (Driver auto-generates that pattern).
    """
    from doppelganger.adapter.server import build_server
    from doppelganger.driver.simulation import Driver

    driver = Driver(substrate_image="bogus-image")
    server = build_server(driver=driver)
    tool = server._tool_manager._tools["get_flow_records"]  # type: ignore[attr-defined]
    assert tool.fn.__doc__, "get_flow_records needs a docstring"
    import ast
    import inspect
    import textwrap
    src = textwrap.dedent(inspect.getsource(tool.fn))
    tree = ast.parse(src)
    leaky_keys = {"scenario"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and k.value in leaky_keys:
                    raise AssertionError(
                        f"get_flow_records response builds a dict with "
                        f"key {k.value!r} — leaks scenario name to agent."
                    )


def test_get_flow_records_unknown_scenario_raises():
    """The tool must raise ValueError for an unknown scenario name —
    same error contract as the other run-the-scenario tools."""
    from doppelganger.adapter.server import build_server
    from doppelganger.driver.simulation import Driver

    driver = Driver(substrate_image="bogus-image")
    server = build_server(driver=driver)
    tool = server._tool_manager._tools["get_flow_records"]  # type: ignore[attr-defined]

    with pytest.raises(ValueError, match="Unknown scenario"):
        tool.fn(name="no-such-scenario", run_id=None)


# ---------------------------------------------- get_fabric_counters end-to-end (gated)

@pytest.mark.requires_substrate
def test_get_fabric_counters_asymmetry_inverts_with_ecn_config_only(tmp_path):
    """Direct demonstration that the PFC vs ECN-CN asymmetry is driven
    by the ECN config, not by the workload. Same `pfc_storm` topology
    and traffic, run twice — once with default ECN thresholds (DCQCN
    engaged, ECN marks fire), once with KMIN bumped above buffer
    capacity (DCQCN running blind, only PFC fires).

    The inversion is the load-bearing pedagogical signal Stage 5b's
    skill teaches the agent to read. This test bypasses the MCP factory
    registry because exposing a "pfc-storm-healthy" name there would
    leak fault-class information through the manifest; the test invokes
    the Driver directly with both ECN configurations and aggregates
    counters from the trace files.
    """
    from doppelganger.driver.counters import aggregate_counters
    from doppelganger.driver.parsers.ecn import parse_ecn_file
    from doppelganger.driver.parsers.pfc import parse_pfc_file
    from doppelganger.driver.simulation import Driver
    from doppelganger.scenarios.builtin import pfc_storm

    if not _substrate_image_present():
        pytest.skip("doppelganger-substrate image not built locally")

    driver = Driver(traces_root=tmp_path)

    healthy = driver.run_scenario(
        pfc_storm(ecn_misconfigured=False), run_id="asymmetry-healthy"
    )
    healthy_pfc = parse_pfc_file(healthy.trace_dir / "pfc.txt")
    healthy_ecn = parse_ecn_file(healthy.trace_dir / "ecn.txt")
    healthy_ports = aggregate_counters(healthy_pfc, healthy_ecn)["ports"]

    misconfig = driver.run_scenario(
        pfc_storm(ecn_misconfigured=True), run_id="asymmetry-misconfig"
    )
    misconfig_pfc = parse_pfc_file(misconfig.trace_dir / "pfc.txt")
    misconfig_ecn = parse_ecn_file(misconfig.trace_dir / "ecn.txt")
    misconfig_ports = aggregate_counters(misconfig_pfc, misconfig_ecn)["ports"]

    # SONiC-shape (2026-05-10): per-queue records — sum across queues per
    # port, then across ports. Aggregates were deliberately removed from
    # the response so the agent sums them itself; tests do the same.
    healthy_ecn_total = sum(
        q["ecn_marks_sent"] for p in healthy_ports for q in p["queues"]
    )
    misconfig_ecn_total = sum(
        q["ecn_marks_sent"] for p in misconfig_ports for q in p["queues"]
    )
    misconfig_pfc_total = sum(
        q["pfc_pause_sent"] for p in misconfig_ports for q in p["queues"]
    )

    # Healthy ECN config: DCQCN throttles via marks before PFC headroom
    assert healthy_ecn_total > 0, (
        f"healthy DCQCN must emit CE-stamps; got {healthy_ecn_total}"
    )
    # Misconfigured ECN: ShouldSendCN always returns false → zero marks
    assert misconfig_ecn_total == 0, (
        f"KMIN above buffer must produce zero CE-stamps; got {misconfig_ecn_total}"
    )
    # Misconfigured ECN: queues build past PFC headroom → pauses fire
    assert misconfig_pfc_total > 0, (
        f"ECN misconfig must push past PFC headroom; got {misconfig_pfc_total}"
    )


@pytest.mark.requires_substrate
def test_get_fabric_counters_pfc_storm_ecn_misconfigured_inverts_asymmetry(tmp_path):
    """ECN-misconfigured pfc_storm. KMIN bumped above buffer capacity →
    ShouldSendCN always returns false → no CE-stamps. DCQCN runs blind,
    queues build past PFC headroom → pause frames fire.

    The asymmetry inverts: ECN marks_sent == 0 alongside PFC pause_sent
    > 0 is the SRE-recognizable signature for ECN misconfiguration. The
    skill at Stage 5b will read this exact asymmetry.
    """
    from doppelganger.adapter.server import build_server
    from doppelganger.driver.simulation import Driver

    if not _substrate_image_present():
        pytest.skip("doppelganger-substrate image not built locally")

    server = build_server(driver=Driver(traces_root=tmp_path))
    tool = server._tool_manager._tools["get_fabric_counters"]  # type: ignore[attr-defined]
    response = tool.fn(name="pfc-storm", run_id="counters-pfc-storm")

    ports = response["data"]["ports"]
    ecn_total = sum(
        q["ecn_marks_sent"] for p in ports for q in p["queues"]
    )
    pfc_total = sum(
        q["pfc_pause_sent"] for p in ports for q in p["queues"]
    )
    assert ecn_total == 0, (
        f"ECN misconfig (KMIN above capacity) must produce zero CE-stamps; "
        f"got {ecn_total}"
    )
    assert pfc_total > 0, (
        f"ECN misconfig must still push queues past PFC headroom; "
        f"got pfc_pause_sent={pfc_total}"
    )


@pytest.mark.requires_substrate
def test_get_fabric_counters_payload_carries_both_classes_in_every_record(tmp_path):
    """Constraint memory: PFC and ECN-CN must be in one payload, every
    record. Even on a port that only saw one class of event, the other
    class's counters must be present and zero — never absent. This is the
    *structural* leak guard: the agent must not be able to read PFC
    elevation without seeing the ECN counter alongside it."""
    from doppelganger.adapter.server import build_server
    from doppelganger.driver.simulation import Driver

    if not _substrate_image_present():
        pytest.skip("doppelganger-substrate image not built locally")

    server = build_server(driver=Driver(traces_root=tmp_path))
    tool = server._tool_manager._tools["get_fabric_counters"]  # type: ignore[attr-defined]
    response = tool.fn(name="microburst", run_id="counters-leak-guard")

    # SONiC-shape (2026-05-10): per-queue records under each port.
    # Structural-leak guard now extends to all per-queue fields; the
    # agent must always see PFC + ECN + volumetric + watermarks per
    # priority queue, never just one class.
    required_per_queue = {
        "q_index",
        "rx_packets", "rx_bytes", "tx_packets", "tx_bytes",
        "dropped_packets", "qlen_peak_bytes", "pg_watermark_bytes",
        "pfc_pause_sent", "pfc_pause_rcvd",
        "pfc_resume_sent", "pfc_resume_rcvd",
        "ecn_marks_sent",
    }
    required_port_top_level = {
        "node_id", "if_index", "node_type",
        "oper_status", "admin_status",
        "speed_bps", "mtu_bytes",
        "queues",
    }
    for rec in response["data"]["ports"]:
        missing = required_port_top_level - rec.keys()
        assert not missing, f"port record missing fields: {missing} on {rec!r}"
        assert len(rec["queues"]) == 8, (
            f"port {rec['node_id']}/{rec['if_index']} has "
            f"{len(rec['queues'])} queues; SONiC-shape requires 8"
        )
        for q in rec["queues"]:
            missing_q = required_per_queue - q.keys()
            assert not missing_q, f"queue record missing fields: {missing_q}"
            for f in required_per_queue:
                assert isinstance(q[f], int), (
                    f"queue field {f} must be int (zero is data, not "
                    f"absence); got {type(q[f]).__name__}"
                )


@pytest.mark.requires_substrate
def test_pfc_storm_realistic_distributes_volumetric_activity(tmp_path):
    """Stage 5a-realistic. The point of layered background traffic is
    that under healthy ECN config the fabric baseline is *populated* —
    the agent has to find the storm port among many ports doing real
    work, not against a 2-row payload that pre-aggregates asymmetry by
    omission. ECN marks may still concentrate on the storm path (they
    are the load-bearing congestion-shaping signal) but the volumetric
    distribution must broaden materially vs the toy scenario.

    Asserts the rollup populates rx_packets on >= 8 distinct
    (switch_id, if_index) pairs under healthy config — the toy
    pfc_storm without background typically populates 2-3.
    """
    from doppelganger.driver.counters import aggregate_counters
    from doppelganger.driver.parsers.counters import parse_counters_file
    from doppelganger.driver.parsers.ecn import parse_ecn_file
    from doppelganger.driver.parsers.pfc import parse_pfc_file
    from doppelganger.driver.simulation import Driver
    from doppelganger.scenarios.builtin import pfc_storm

    if not _substrate_image_present():
        pytest.skip("doppelganger-substrate image not built locally")

    driver = Driver(traces_root=tmp_path)
    result = driver.run_scenario(
        pfc_storm(
            ecn_misconfigured=False,
            background_pairs_per_leaf=2,
        ),
        run_id="realistic-healthy",
    )
    pfc = parse_pfc_file(result.trace_dir / "pfc.txt")
    ecn = parse_ecn_file(result.trace_dir / "ecn.txt")
    rollup = parse_counters_file(result.trace_dir / "counters.txt")
    topo = pfc_storm(background_pairs_per_leaf=2).custom_topology
    ports = aggregate_counters(pfc, ecn, rollup_rows=rollup, topology=topo)["ports"]

    # SONiC-shape: a port is "active" if any of its 8 queues saw
    # rx_packets. The substrate's RoCE traffic typically lands on q=3.
    active = {
        (r["node_id"], r["if_index"])
        for r in ports
        if any(q["rx_packets"] > 0 for q in r["queues"])
    }
    assert len(active) >= 8, (
        f"realistic scenario should produce rx activity on >= 8 distinct "
        f"ports under healthy config; got {len(active)} active pairs: "
        f"{sorted(active)}"
    )


@pytest.mark.requires_substrate
def test_get_fabric_counters_zero_fills_every_topology_switch_port(tmp_path):
    """Stage 5a-realistic: topology-aware port enumeration must produce
    ONE record per switch port the scenario topology declares — including
    ports that saw no activity (zero-filled). Otherwise the agent gets a
    sparse payload from which absolute asymmetry (0 vs N) is trivial to
    spot; the realism goal is *relative* asymmetry against a populated
    fabric baseline."""
    from doppelganger.adapter.server import (
        BUILTIN_SCENARIO_FACTORIES,
        build_server,
    )
    from doppelganger.driver.simulation import Driver

    if not _substrate_image_present():
        pytest.skip("doppelganger-substrate image not built locally")

    scenario = BUILTIN_SCENARIO_FACTORIES["microburst"]()
    topo = scenario.custom_topology
    assert topo is not None, "microburst should declare a custom topology"
    expected_ports = (
        topo.leaves * (topo.hosts_per_leaf + topo.spines)
        + topo.spines * topo.leaves
    )

    server = build_server(driver=Driver(traces_root=tmp_path))
    tool = server._tool_manager._tools["get_fabric_counters"]  # type: ignore[attr-defined]
    response = tool.fn(name="microburst", run_id="counters-zero-fill")
    ports = response["data"]["ports"]
    assert len(ports) >= expected_ports, (
        f"expected at least {expected_ports} port records (topology "
        f"enumeration), got {len(ports)}"
    )
    # The cardinality check above already proves topology zero-fill is
    # adding ports beyond observed (microburst's flow pattern wouldn't
    # populate every leaf↔spine and host port absent enumeration). For
    # the per-queue dimension: most ports see traffic only on q=3 (the
    # RoCE/RDMA priority_group) — assert that at least one (port, queue)
    # pair is zero-filled, even if every port is active on q=3.
    quiet_queues = sum(
        1 for r in ports for q in r["queues"]
        if q["rx_packets"] == 0 and q["tx_packets"] == 0
        and q["pfc_pause_sent"] == 0 and q["ecn_marks_sent"] == 0
    )
    assert quiet_queues > 0, (
        "expected at least one zero-filled (port, queue) from topology "
        "enumeration; every queue had observed activity, suggesting "
        "the per-queue zero-fill collapsed"
    )


# ---------------------------------------------- get_flow_records end-to-end (gated)

@pytest.mark.requires_substrate
def test_get_flow_records_microburst_returns_per_flow_array(tmp_path):
    """Run the microburst scenario via the adapter and verify the
    response envelope carries a non-empty `flows` array whose records
    have the expected per-flow fields (sip/dip/sport/dport/status/
    fct_ns/standalone_fct_ns/slowdown/actual_size_bytes/actual_start_ns)
    plus a `summary` field with completed/incomplete/total counts and
    FCT distribution.
    """
    from doppelganger.adapter.server import build_server
    from doppelganger.driver.simulation import Driver

    if not _substrate_image_present():
        pytest.skip("doppelganger-substrate image not built locally")

    driver = Driver(traces_root=tmp_path)
    server = build_server(driver=driver)
    tool = server._tool_manager._tools["get_flow_records"]  # type: ignore[attr-defined]

    envelope = tool.fn(name="microburst", run_id=None)
    assert envelope["confidence"] == "high"
    data = envelope["data"]

    assert isinstance(data["flows"], list)
    assert len(data["flows"]) > 0
    record = data["flows"][0]
    expected_fields = {
        "sip", "dip", "sport", "dport",
        "status",
        "actual_size_bytes", "actual_start_ns",
        "fct_ns", "standalone_fct_ns",
        "slowdown",
    }
    assert expected_fields.issubset(record.keys()), (
        f"flow record missing fields: {expected_fields - record.keys()}"
    )

    summary = data["summary"]
    assert summary["total"] == len(data["flows"])
    assert summary["completed"] >= 0
    assert summary["incomplete"] >= 0
    assert summary["total"] == summary["completed"] + summary["incomplete"]
    assert "fct" in summary
    assert summary["fct"]["n"] >= 0


def _substrate_image_present() -> bool:
    """Local helper duplicated from conftest so the assertion message is
    inline with the test (don't depend on fixture autouse for skip)."""
    import shutil
    import subprocess
    if shutil.which("docker") is None:
        return False
    return subprocess.run(
        ["docker", "image", "inspect", "doppelganger-substrate"],
        capture_output=True,
    ).returncode == 0
