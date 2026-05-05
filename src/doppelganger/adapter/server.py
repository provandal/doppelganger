"""MCP server implementation: tool registration + Driver delegation.

Tools wrap their return values in the response envelope from
Doppelgänger v0.2 §2.3 (``data``, ``observed_at_ns``, ``source``,
``confidence``, ``staleness_class``). For Doppelgänger specifically:

* ``confidence`` is always ``"high"`` — substrate data is simulation
  ground truth, not measured.
* ``observed_at_ns`` is *simulation* time, not wall clock. Static
  metadata (e.g., scenario list) leaves it ``None``.
* ``staleness_class`` is ``"fresh"`` for just-completed runs;
  re-parsed historical traces should be ``"stale"``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP

from doppelganger.driver.parsers.fct import parse_fct_file
from doppelganger.driver.simulation import Driver
from doppelganger.driver.types import CompletionStatus, PerFlowRecord
from doppelganger.eval.comparison import compare_runs as _compare_runs
from doppelganger.eval.comparison import summarize_run
from doppelganger.scenarios.builtin import (
    asymmetric_path,
    hash_polarization,
    microburst,
    pfc_storm,
    spike_burst_baseline,
    spike_burst_silent_drops,
)
from doppelganger.scenarios.types import Scenario

# Scenario factories exposed by name through the MCP surface. Each entry
# returns a ready-to-run Scenario with default parameters; v0.1 of the
# Adapter does not surface parameterization (callers wanting that use
# the Python API directly).
BUILTIN_SCENARIO_FACTORIES: dict[str, Callable[[], Scenario]] = {
    "spike-burst-baseline": spike_burst_baseline,
    "spike-burst-silent-drops": lambda: spike_burst_silent_drops(rate=0.001),
    "microburst": microburst,
    "pfc-storm": pfc_storm,
    "asymmetric-path": asymmetric_path,
    "hash-polarization": hash_polarization,
}


def envelope(
    data: Any,
    *,
    source: str,
    observed_at_ns: int | None = None,
    staleness_class: str = "fresh",
) -> dict[str, Any]:
    """Wrap a tool result in Doppelgänger's response envelope (§2.3).

    Doppelgänger always reports ``confidence == "high"`` because every
    field is simulation ground truth.
    """
    return {
        "data": data,
        "observed_at_ns": observed_at_ns,
        "source": source,
        "confidence": "high",
        "staleness_class": staleness_class,
    }


def _flow_to_dict(flow: PerFlowRecord) -> dict[str, Any]:
    """Serialize a PerFlowRecord into JSON-friendly form for MCP transport."""
    return {
        "sip": flow.sip,
        "dip": flow.dip,
        "sport": flow.sport,
        "dport": flow.dport,
        "status": flow.status.value,
        "actual_size_bytes": flow.actual_size_bytes,
        "actual_start_ns": flow.actual_start_ns,
        "fct_ns": flow.fct_ns,
        "standalone_fct_ns": flow.standalone_fct_ns,
        "slowdown": flow.slowdown,
    }


def _summary_to_dict(summary) -> dict[str, Any]:
    return {
        "total": summary.total,
        "completed": summary.completed,
        "incomplete": summary.incomplete,
        "by_status": {s.value: c for s, c in summary.by_status.items()},
        "fct": {
            "n": summary.fct.n,
            "min_ns": summary.fct.min_ns,
            "p50_ns": summary.fct.p50_ns,
            "p90_ns": summary.fct.p90_ns,
            "p99_ns": summary.fct.p99_ns,
            "p999_ns": summary.fct.p999_ns,
            "max_ns": summary.fct.max_ns,
            "mean_ns": summary.fct.mean_ns,
        },
    }


def build_server(
    *,
    driver: Driver | None = None,
    server_name: str = "doppelganger-substrate-adapter",
) -> FastMCP:
    """Construct and return a FastMCP server with Doppelgänger tools registered.

    Parameters
    ----------
    driver:
        Optional Driver instance to delegate to. If None, a default
        ``Driver()`` is constructed with the standard ``doppelganger-substrate``
        image and ``./traces`` root. Tests pass a configured Driver here.
    server_name:
        MCP server identity announced to clients.
    """
    if driver is None:
        driver = Driver()

    server = FastMCP(server_name)

    @server.tool()
    def list_scenarios() -> dict[str, Any]:
        """List the named scenarios this Adapter can run.

        Returns a response-envelope dict whose ``data`` field is a list of
        ``{name, difficulty, intended_symptom, root_cause}`` entries. The
        ``"spike-burst"`` legacy name (string scenario passed directly to
        the substrate's bundled config) is included alongside the
        Scenario-object factories.
        """
        items: list[dict[str, Any]] = [
            {
                "name": "spike-burst",
                "difficulty": "basic",
                "intended_symptom": "(reproduces the substrate's bundled example)",
                "root_cause": "(none)",
            },
        ]
        for name, factory in BUILTIN_SCENARIO_FACTORIES.items():
            scenario = factory()
            items.append({
                "name": name,
                "difficulty": scenario.difficulty,
                "intended_symptom": scenario.intended_symptom,
                "root_cause": scenario.root_cause,
            })
        return envelope(
            items,
            source="adapter.builtin_scenario_registry",
            observed_at_ns=None,
        )

    @server.tool()
    def run_scenario(name: str, run_id: str | None = None) -> dict[str, Any]:
        """Run a named scenario end-to-end. Returns summary + flow records.

        Parameters
        ----------
        name:
            One of the names returned by ``list_scenarios``.
        run_id:
            Optional run identifier (used as the trace-dir name). If
            omitted, the Driver generates one from the scenario name +
            unix timestamp.
        """
        if name == "spike-burst":
            result = driver.run_scenario("spike-burst", run_id=run_id)
        elif name in BUILTIN_SCENARIO_FACTORIES:
            scenario = BUILTIN_SCENARIO_FACTORIES[name]()
            result = driver.run_scenario(scenario, run_id=run_id)
        else:
            raise ValueError(
                f"Unknown scenario {name!r}. "
                f"Call list_scenarios for the available set."
            )

        summary = summarize_run(result.flows)
        return envelope(
            {
                "scenario": result.scenario,
                "run_id": result.trace_dir.name,
                "trace_dir": str(result.trace_dir),
                "compiled_config_path": (
                    str(result.compiled_config_path)
                    if result.compiled_config_path else None
                ),
                "wall_clock_seconds": result.wall_clock_seconds,
                "summary": _summary_to_dict(summary),
                "flows": [_flow_to_dict(f) for f in result.flows],
            },
            source=f"driver.run_scenario({name!r})",
            observed_at_ns=None,  # per-flow observed_at is in the records
        )

    @server.tool()
    def compare_runs(
        baseline_trace_dir: str,
        injected_trace_dir: str,
    ) -> dict[str, Any]:
        """Compare two completed runs by re-parsing their fct.txt files.

        Parameters
        ----------
        baseline_trace_dir:
            Path to a trace directory containing ``fct.txt`` (typically
            ``traces/<run-id>/`` from a prior ``run_scenario`` call).
        injected_trace_dir:
            Path to the comparison trace directory.

        Returns the comparison findings: flow_count_delta, per-percentile
        FCT deltas, and human-readable ``findings`` strings.
        """
        baseline_fct = Path(baseline_trace_dir) / "fct.txt"
        injected_fct = Path(injected_trace_dir) / "fct.txt"
        if not baseline_fct.exists():
            raise ValueError(f"baseline fct.txt not found: {baseline_fct}")
        if not injected_fct.exists():
            raise ValueError(f"injected fct.txt not found: {injected_fct}")

        baseline_records = parse_fct_file(baseline_fct)
        injected_records = parse_fct_file(injected_fct)
        comparison = _compare_runs(baseline_records, injected_records)

        return envelope(
            {
                "baseline_trace_dir": str(baseline_fct.parent),
                "injected_trace_dir": str(injected_fct.parent),
                "flow_count_delta": comparison.flow_count_delta,
                "has_count_divergence": comparison.has_count_divergence,
                "fct_p50_delta_ns": comparison.fct_p50_delta_ns,
                "fct_p99_delta_ns": comparison.fct_p99_delta_ns,
                "fct_p999_delta_ns": comparison.fct_p999_delta_ns,
                "baseline_summary": _summary_to_dict(comparison.baseline),
                "injected_summary": _summary_to_dict(comparison.injected),
                "findings": comparison.findings,
            },
            source=f"eval.compare_runs(parsed-from-disk)",
            observed_at_ns=None,
            staleness_class="stale",  # historical re-parse, not a fresh run
        )

    return server
