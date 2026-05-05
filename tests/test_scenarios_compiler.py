"""Tests for the Scenario → config-burst.txt compiler.

The most load-bearing test compares the compiler's output for the baseline
spike scenario against the spike's actual ``config.txt`` (in
``spike/traces/baseline/``). Semantic equality on a key-by-key basis is the
contract; whitespace / blank-line layout is best-effort.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from doppelganger.scenarios import (
    SPIKE_BURST_256,
    Flow,
    Scenario,
    ScenarioCompileError,
    Topology,
    TrafficPattern,
    compile_scenario,
    spike_burst_baseline,
    spike_burst_silent_drops,
)


# ----------------------------------------------------------- helpers

def _parse_config(text: str) -> dict[str, str]:
    """Parse substrate ``config-burst.txt`` into a flat key→value dict.

    Lines are ``KEY VALUE [VALUE...]`` separated by single spaces. Blank
    lines and lines that don't fit the shape are ignored.
    """
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        key, value = parts
        out[key] = value.rstrip()
    return out


SPIKE_BASELINE_PATH = (
    Path(__file__).resolve().parent.parent
    / "spike" / "traces" / "baseline" / "config.txt"
)
SPIKE_INJECTED_PATH = (
    Path(__file__).resolve().parent.parent
    / "spike" / "traces" / "injected" / "config.txt"
)


# ----------------------------------------------------------- baseline

def test_baseline_compiles_without_error(tmp_path):
    out = compile_scenario(spike_burst_baseline(), tmp_path / "config-burst.txt")
    assert out.exists()
    assert out.read_text(encoding="utf-8").startswith("ENABLE_QCN 1\n")


def test_baseline_matches_spike_known_good_semantically(tmp_path):
    """The compiled baseline must agree key-by-key with the spike's baseline.

    This is the load-bearing test: if the compiler's output for the baseline
    scenario diverges from the spike's known-good config, we don't know
    whether the substrate will still validate the file. Exact-match on the
    fields that exist in both is the contract.
    """
    if not SPIKE_BASELINE_PATH.exists():
        pytest.skip(f"spike baseline config not present at {SPIKE_BASELINE_PATH}")

    compiled = compile_scenario(spike_burst_baseline(), tmp_path / "config-burst.txt")

    spike_keys = _parse_config(SPIKE_BASELINE_PATH.read_text(encoding="utf-8"))
    compiled_keys = _parse_config(compiled.read_text(encoding="utf-8"))

    # Every key the spike has, the compiled output has, with the same value.
    mismatches = {
        k: (spike_keys[k], compiled_keys.get(k))
        for k in spike_keys
        if compiled_keys.get(k) != spike_keys[k]
    }
    assert not mismatches, (
        f"Compiled baseline diverges from spike baseline on keys: {mismatches}"
    )


def test_silent_drops_differs_from_baseline_only_in_error_rate(tmp_path):
    """Spike's known-good silent-drops scenario and baseline differ in exactly one key.

    From ``diff spike/traces/baseline/config.txt spike/traces/injected/config.txt``:
    the only difference is ``ERROR_RATE_PER_LINK 0.0000`` vs ``0.001``.
    The compiler must reproduce the same property.
    """
    baseline = compile_scenario(
        spike_burst_baseline(), tmp_path / "baseline.txt"
    )
    silent_drops = compile_scenario(
        spike_burst_silent_drops(rate=0.001), tmp_path / "silent_drops.txt"
    )

    base_keys = _parse_config(baseline.read_text(encoding="utf-8"))
    drop_keys = _parse_config(silent_drops.read_text(encoding="utf-8"))

    diff = {
        k: (base_keys.get(k), drop_keys.get(k))
        for k in set(base_keys) | set(drop_keys)
        if base_keys.get(k) != drop_keys.get(k)
    }
    assert set(diff.keys()) == {"ERROR_RATE_PER_LINK"}
    assert diff["ERROR_RATE_PER_LINK"] == ("0.0000", "0.0010")


def test_silent_drops_matches_spike_injected_config(tmp_path):
    """Compiled silent-drops scenario agrees key-by-key with the spike's injected run.

    The spike's two hand-typed configs use inconsistent float decimal padding
    (``ERROR_RATE_PER_LINK 0.0000`` in baseline, ``0.001`` in injected). The
    compiler emits a stable 4-decimal format throughout. The substrate parses
    these as floats and treats them identically; the test compares
    ``ERROR_RATE_PER_LINK`` numerically and string-matches everything else.
    """
    if not SPIKE_INJECTED_PATH.exists():
        pytest.skip(f"spike injected config not present at {SPIKE_INJECTED_PATH}")

    compiled = compile_scenario(
        spike_burst_silent_drops(rate=0.001), tmp_path / "config-burst.txt"
    )

    spike_keys = _parse_config(SPIKE_INJECTED_PATH.read_text(encoding="utf-8"))
    compiled_keys = _parse_config(compiled.read_text(encoding="utf-8"))

    # Numeric comparison for the float-formatted error rate.
    assert float(compiled_keys["ERROR_RATE_PER_LINK"]) == pytest.approx(
        float(spike_keys["ERROR_RATE_PER_LINK"])
    )

    # String comparison for everything else.
    mismatches = {
        k: (spike_keys[k], compiled_keys.get(k))
        for k in spike_keys
        if k != "ERROR_RATE_PER_LINK" and compiled_keys.get(k) != spike_keys[k]
    }
    assert not mismatches, (
        f"Compiled silent-drops diverges from spike injected on keys: {mismatches}"
    )


# ----------------------------------------------------------- shape

def test_topology_and_flow_paths_are_emitted(tmp_path):
    compiled = compile_scenario(spike_burst_baseline(), tmp_path / "c.txt")
    keys = _parse_config(compiled.read_text(encoding="utf-8"))
    assert keys["TOPOLOGY_FILE"] == SPIKE_BURST_256.topology_path
    assert keys["FLOW_FILE"] == SPIKE_BURST_256.flow_path


def test_simulation_duration_is_emitted_compactly(tmp_path):
    s = Scenario(
        name="custom-duration",
        topology=SPIKE_BURST_256,
        sim_duration_seconds=1.5,
    )
    keys = _parse_config(
        compile_scenario(s, tmp_path / "c.txt").read_text(encoding="utf-8")
    )
    assert keys["SIMULATOR_STOP_TIME"] == "1.5"


def test_compiler_creates_parent_directory(tmp_path):
    target = tmp_path / "nested" / "deep" / "config-burst.txt"
    out = compile_scenario(spike_burst_baseline(), target)
    assert out.exists()


# ----------------------------------------------------------- validation

def test_negative_duration_rejected(tmp_path):
    bad = Scenario(
        name="bad", topology=SPIKE_BURST_256, sim_duration_seconds=-0.1
    )
    with pytest.raises(ScenarioCompileError, match="sim_duration_seconds"):
        compile_scenario(bad, tmp_path / "c.txt")


def test_zero_duration_rejected(tmp_path):
    bad = Scenario(
        name="bad", topology=SPIKE_BURST_256, sim_duration_seconds=0
    )
    with pytest.raises(ScenarioCompileError, match="sim_duration_seconds"):
        compile_scenario(bad, tmp_path / "c.txt")


def test_error_rate_above_one_rejected(tmp_path):
    bad = Scenario(
        name="bad", topology=SPIKE_BURST_256, link_error_rate=1.5
    )
    with pytest.raises(ScenarioCompileError, match="link_error_rate"):
        compile_scenario(bad, tmp_path / "c.txt")


def test_error_rate_below_zero_rejected(tmp_path):
    bad = Scenario(
        name="bad", topology=SPIKE_BURST_256, link_error_rate=-0.001
    )
    with pytest.raises(ScenarioCompileError, match="link_error_rate"):
        compile_scenario(bad, tmp_path / "c.txt")


def test_zero_buffer_rejected(tmp_path):
    bad = Scenario(name="bad", topology=SPIKE_BURST_256, buffer_size=0)
    with pytest.raises(ScenarioCompileError, match="buffer_size"):
        compile_scenario(bad, tmp_path / "c.txt")


def test_empty_kmax_map_rejected(tmp_path):
    bad = Scenario(name="bad", topology=SPIKE_BURST_256, kmax_map=())
    with pytest.raises(ScenarioCompileError, match="kmax_map"):
        compile_scenario(bad, tmp_path / "c.txt")


# --------------------------------------------- custom topology / traffic

def test_custom_topology_redirects_topology_file(tmp_path):
    """When custom_topology is set, TOPOLOGY_FILE must point at /traces/topology.txt."""
    s = Scenario(
        name="custom-topo",
        topology=SPIKE_BURST_256,  # ignored when custom_topology is set
        custom_topology=Topology(leaves=2, spines=1, hosts_per_leaf=2),
    )
    keys = _parse_config(
        compile_scenario(s, tmp_path / "c.txt").read_text(encoding="utf-8")
    )
    assert keys["TOPOLOGY_FILE"] == "/traces/topology.txt"
    # FLOW_FILE stays at the bundled path because custom_traffic is None
    assert keys["FLOW_FILE"] == SPIKE_BURST_256.flow_path


def test_custom_traffic_redirects_flow_file(tmp_path):
    """When custom_traffic is set, FLOW_FILE must point at /traces/flow.txt."""
    s = Scenario(
        name="custom-traffic",
        topology=SPIKE_BURST_256,
        custom_traffic=TrafficPattern(flows=(
            Flow(src=0, dst=1, priority_group=3, dst_port=10000,
                 packet_count=1000, start_time_seconds=0.1),
        )),
    )
    keys = _parse_config(
        compile_scenario(s, tmp_path / "c.txt").read_text(encoding="utf-8")
    )
    assert keys["FLOW_FILE"] == "/traces/flow.txt"
    # TOPOLOGY_FILE stays at the bundled path
    assert keys["TOPOLOGY_FILE"] == SPIKE_BURST_256.topology_path


def test_both_custom_redirects_both_paths(tmp_path):
    s = Scenario(
        name="custom-both",
        topology=SPIKE_BURST_256,
        custom_topology=Topology(leaves=1, spines=1, hosts_per_leaf=2),
        custom_traffic=TrafficPattern(flows=(
            Flow(src=0, dst=1, priority_group=3, dst_port=10000,
                 packet_count=1000, start_time_seconds=0.1),
        )),
    )
    keys = _parse_config(
        compile_scenario(s, tmp_path / "c.txt").read_text(encoding="utf-8")
    )
    assert keys["TOPOLOGY_FILE"] == "/traces/topology.txt"
    assert keys["FLOW_FILE"] == "/traces/flow.txt"
