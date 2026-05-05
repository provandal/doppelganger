"""Driver smoke tests.

Three layers:

1. **Parser unit test.** Synthetic ``fct.txt`` content; no Docker required.
2. **Driver error-path test.** Drive the image-missing case; no Docker required.
3. **End-to-end scenario test.** Gated on the substrate image being built
   locally; runs the spike-burst scenario and asserts at least one flow record
   comes back. Marked ``requires_substrate``; auto-skipped if not present.
"""

from __future__ import annotations

import textwrap

import pytest

from doppelganger.driver import Driver, DriverError
from doppelganger.driver.parsers.fct import parse_fct_file
from doppelganger.driver.types import CompletionStatus
from doppelganger.scenarios import (
    SPIKE_BURST_256,
    Scenario,
    spike_burst_baseline,
)


# --------------------------------------------------------------------- parser

def test_fct_parser_parses_well_formed_lines(tmp_path):
    sample = textwrap.dedent(
        """\
        0a000001 0a000002 49152 50000 4096 1000 12500 10000
        0a000001 0a000003 49153 50000 8192 1500 25000 20000
        """
    )
    fct = tmp_path / "fct.txt"
    fct.write_text(sample)

    records = parse_fct_file(fct)

    assert len(records) == 2
    first = records[0]
    assert first.sip == "0a000001"
    assert first.dip == "0a000002"
    assert first.sport == 49152
    assert first.dport == 50000
    assert first.actual_size_bytes == 4096
    assert first.fct_ns == 12500
    assert first.standalone_fct_ns == 10000
    assert first.status is CompletionStatus.COMPLETED
    assert first.slowdown == pytest.approx(1.25)


def test_fct_parser_skips_malformed_lines(tmp_path):
    sample = textwrap.dedent(
        """\
        # this is a header comment, not a record
        0a000001 0a000002 49152 50000 4096 1000 12500 10000
        not enough columns
        0a000001 0a000003 49153 50000 nondigit 1500 25000 20000
        0a000001 0a000004 49154 50000 4096 2000 11000 10000
        """
    )
    fct = tmp_path / "fct.txt"
    fct.write_text(sample)

    records = parse_fct_file(fct)

    # The two well-formed lines survive; comment / short / non-digit lines drop.
    assert len(records) == 2


def test_fct_parser_handles_empty_file(tmp_path):
    fct = tmp_path / "fct.txt"
    fct.write_text("")
    assert parse_fct_file(fct) == []


# ----------------------------------------------------------------- driver api

def test_driver_lists_builtin_scenarios():
    driver = Driver(substrate_image="doesnt-matter-for-this-test")
    scenarios = driver.list_scenarios()
    assert "spike-burst" in scenarios


def test_driver_rejects_unknown_scenario(tmp_path):
    driver = Driver(
        substrate_image="doesnt-matter-for-this-test",
        traces_root=tmp_path,
    )
    with pytest.raises(DriverError, match="Unknown scenario"):
        driver.run_scenario("not-a-real-scenario")


def test_driver_raises_when_image_missing(tmp_path):
    """If the substrate image isn't built locally, run_scenario raises clearly."""
    driver = Driver(
        substrate_image="doppelganger-substrate-definitely-does-not-exist",
        traces_root=tmp_path,
    )
    with pytest.raises(DriverError) as exc_info:
        driver.run_scenario("spike-burst")
    msg = str(exc_info.value)
    assert "not found locally" in msg or "docker CLI not found" in msg


def test_driver_rejects_non_string_non_scenario_input(tmp_path):
    driver = Driver(
        substrate_image="bogus-image",
        traces_root=tmp_path,
    )
    with pytest.raises(DriverError, match="must be a str or Scenario"):
        driver.run_scenario(42)  # type: ignore[arg-type]


def test_driver_compiles_scenario_before_image_check(tmp_path):
    """Driver should compile the scenario into trace_dir before checking the image.

    Means a fresh trace dir + compiled config-burst.txt exist on disk even
    if the substrate image isn't built — useful for inspecting what would
    have run, and confirms the compile-and-stage path works without Docker.
    """
    driver = Driver(
        substrate_image="doppelganger-substrate-definitely-does-not-exist",
        traces_root=tmp_path,
    )
    scenario = spike_burst_baseline()

    with pytest.raises(DriverError, match="not found locally"):
        driver.run_scenario(scenario, run_id="compile-only")

    expected_config = tmp_path / "compile-only" / "config-burst.txt"
    assert expected_config.exists()
    text = expected_config.read_text(encoding="utf-8")
    assert text.startswith("ENABLE_QCN 1\n")
    assert "TOPOLOGY_FILE examples/PowerTCP/topology-256.txt" in text


# ---------------------------------------------------------- end-to-end (gated)

@pytest.mark.requires_substrate
def test_driver_runs_spike_burst_end_to_end(tmp_path, substrate_available):
    """Full Driver round-trip: build image → run scenario → parse flows.

    Requires the doppelganger-substrate image to be built locally:

        docker build -t doppelganger-substrate -f docker/substrate.Dockerfile .

    Auto-skipped otherwise so CI without Docker still passes the rest.
    """
    if not substrate_available:
        pytest.skip("doppelganger-substrate image not built locally")

    driver = Driver(traces_root=tmp_path)
    result = driver.run_scenario("spike-burst", run_id="smoke")

    assert result.scenario == "spike-burst"
    assert result.trace_dir == tmp_path / "smoke"
    assert result.compiled_config_path is None  # built-in path doesn't compile
    assert (result.trace_dir / "fct.txt").exists(), "substrate did not produce fct.txt"
    assert len(result.flows) > 0, "expected at least one completed flow"
    assert all(r.status is CompletionStatus.COMPLETED for r in result.flows)
    assert all(r.fct_ns is not None and r.fct_ns > 0 for r in result.flows)


@pytest.mark.requires_substrate
def test_driver_runs_compiled_scenario_end_to_end(tmp_path, substrate_available):
    """Full Driver round-trip via Scenario object.

    Verifies the compile-and-stage path: Driver compiles ``spike_burst_baseline()``
    into ``trace_dir/config-burst.txt``, the substrate runs against the
    compiled config (not the bundled one), and flows come back.
    """
    if not substrate_available:
        pytest.skip("doppelganger-substrate image not built locally")

    driver = Driver(traces_root=tmp_path)
    result = driver.run_scenario(spike_burst_baseline(), run_id="smoke-scenario")

    assert result.scenario == "spike-burst-baseline"
    assert result.compiled_config_path == tmp_path / "smoke-scenario" / "config-burst.txt"
    assert result.compiled_config_path.exists()
    assert (result.trace_dir / "fct.txt").exists()
    assert len(result.flows) > 0
    assert all(r.status is CompletionStatus.COMPLETED for r in result.flows)
