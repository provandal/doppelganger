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

from doppelganger.driver.counters import aggregate_counters
from doppelganger.driver.parsers.counters import parse_counters_file
from doppelganger.driver.parsers.ecn import parse_ecn_file
from doppelganger.driver.parsers.fct import parse_fct_file
from doppelganger.driver.parsers.pfc import parse_pfc_file
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
from doppelganger.scenarios.topology import Topology
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
    "pfc-storm-realistic": lambda: pfc_storm(background_pairs_per_leaf=2),
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


def _host_id_to_ip(host_id: int) -> str:
    """Mirror the substrate's ``node_id_to_ip`` function in Python.

    Substrate source (``examples/PowerTCP/powertcp-evaluation-burst.cc``,
    line 170 of the pinned fork)::

        Ipv4Address(0x0b000001 + ((id / 256) * 0x00010000)
                                + ((id % 256) * 0x00000100));

    Which decodes to ``11.<(id // 256) % 256>.<id % 256>.1`` — the
    fourth octet is always 1 (anchor), the third octet equals
    ``id % 256``, the second octet equals ``id // 256``. This is the
    canonical mapping for every scenario the substrate runs;
    surfacing it on the topology payload lets the agent bridge from
    a help-ticket IP (e.g., "11.0.0.1") to the host_id used by the
    rest of the topology data without having to guess.
    """
    second_octet = (host_id // 256) % 256
    third_octet = host_id % 256
    return f"11.{second_octet}.{third_octet}.1"


HOST_IP_CONVENTION = (
    "Host with substrate id N has IP 11.<(N // 256) % 256>.<N % 256>.1. "
    "The fourth octet is always 1 (anchor). The third octet equals "
    "N mod 256 (so within a /16 block, the third octet equals the "
    "host_id). The second octet is N // 256 (only nonzero for fabrics "
    "with more than 256 hosts). Pinned by the substrate's "
    "node_id_to_ip function in examples/PowerTCP/"
    "powertcp-evaluation-burst.cc; same convention across all scenarios."
)


def _topology_to_dict(topology: Topology) -> dict[str, Any]:
    """Render a Topology declaration into agent-facing structural facts.

    Includes only fabric structure: dimensions, switch/host node IDs,
    per-host IPs (per the substrate's ``node_id_to_ip`` convention),
    link parameters, asymmetry. Eval ground-truth metadata
    (intended_symptom, root_cause, difficulty) lives on the parent
    Scenario and is deliberately NOT exposed here — surfacing it would
    re-leak the answer key the way the Stage 2 v1 prompt did.

    Each leaf entry exposes ``hosts: [{id, ip}, ...]`` rather than the
    earlier ``host_ids: [...]`` so the agent can bridge a help-ticket
    IP to a host_id in one step. The top-level ``host_ip_convention``
    field documents the underlying mapping for cross-scenario reuse.
    """
    first_leaf = topology.first_leaf_id()
    first_spine = topology.first_spine_id()
    leaf_switches = [
        {
            "index": leaf_offset,
            "node_id": first_leaf + leaf_offset,
            "hosts": [
                {"id": host_id, "ip": _host_id_to_ip(host_id)}
                for host_id in range(
                    leaf_offset * topology.hosts_per_leaf,
                    (leaf_offset + 1) * topology.hosts_per_leaf,
                )
            ],
        }
        for leaf_offset in range(topology.leaves)
    ]
    spine_switches = [
        {"index": spine_offset, "node_id": first_spine + spine_offset}
        for spine_offset in range(topology.spines)
    ]
    slow_indices = list(topology.slow_spine_indices)
    asymmetry = {
        "present": bool(slow_indices),
        "slow_spine_indices": slow_indices,
        "slow_link_bps": topology.slow_spine_link_bps if slow_indices else None,
        "slow_link_delay": topology.slow_spine_link_delay if slow_indices else None,
    }
    return {
        "shape": "leaf-spine",
        "leaves": topology.leaves,
        "spines": topology.spines,
        "hosts_per_leaf": topology.hosts_per_leaf,
        "total_hosts": topology.num_hosts,
        "host_ip_convention": HOST_IP_CONVENTION,
        "leaf_switches": leaf_switches,
        "spine_switches": spine_switches,
        "host_link": {
            "bps": topology.host_link_bps,
            "delay": topology.host_link_delay,
        },
        "spine_link": {
            "bps": topology.spine_link_bps,
            "delay": topology.spine_link_delay,
        },
        "ecmp": "full-mesh leaf-to-spine",
        "asymmetry": asymmetry,
    }


def _scenario_to_topology_payload(
    scenario: Scenario, scenario_name: str
) -> dict[str, Any]:
    """Build the get_topology data payload for a scenario.

    Custom-topology scenarios get full structural detail. Scenarios
    pinned to a substrate-bundled topology file (spike-burst*) return a
    degraded payload that names the file path — the structural detail
    isn't available without parsing the substrate's topology.txt and
    that's not load-bearing for any current eval.

    Critically: ``scenario_name`` is NOT included in the data. An SRE
    querying their fabric's topology doesn't receive a "scenario:
    microburst" label — fabrics aren't named after their failure
    modes. Surfacing the substrate scenario name here would re-leak
    the answer key the way Stage 2 v1's failure-class enumeration in
    the system prompt did. The name still appears in the response
    envelope's ``source`` field (operator-side trace metadata, not
    agent-visible).
    """
    if scenario.custom_topology is not None:
        payload = _topology_to_dict(scenario.custom_topology)
        payload["congestion_control"] = {
            "cc_mode": scenario.cc_mode,
            "name": _cc_mode_name(scenario.cc_mode),
            "qcn_enabled": scenario.enable_qcn,
            "buffer_size_mb": scenario.buffer_size,
        }
        return payload
    return {
        "shape": "substrate-bundled",
        "topology_file": scenario.topology.topology_path,
        "flow_file": scenario.topology.flow_path,
        "description": scenario.topology.description,
        "introspection": (
            "structural-detail-not-available: this scenario references a "
            "substrate-bundled topology file; structured fields are not "
            "exposed by the v0.1 Adapter."
        ),
    }


def _cc_mode_name(cc_mode: int) -> str:
    """Map the substrate's CC_MODE integer to a name. Same mapping the
    spike's config-burst.txt uses; v0.1 covers the modes that scenarios
    actually set today.
    """
    return {3: "DCQCN", 8: "TIMELY", 11: "PowerTCP"}.get(cc_mode, f"cc_mode={cc_mode}")


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
    def get_topology(name: str) -> dict[str, Any]:
        """Return the topology declaration of a named scenario.

        Reports fabric structure only: dimensions, switch and host node
        IDs, link parameters, asymmetry, and CC mode. Eval ground-truth
        metadata (intended_symptom, root_cause) is deliberately omitted
        — this tool is callable by an agent during eval, and surfacing
        the answer key here would re-leak it.

        Parameters
        ----------
        name:
            One of the names returned by ``list_scenarios``.

        Returns the response envelope; ``data.shape`` is ``"leaf-spine"``
        for scenarios with a custom topology declaration, or
        ``"substrate-bundled"`` for spike-burst* scenarios that point at
        a substrate-shipped topology file (structural detail not
        introspected in v0.1).
        """
        if name == "spike-burst":
            scenario = spike_burst_baseline()
        elif name in BUILTIN_SCENARIO_FACTORIES:
            scenario = BUILTIN_SCENARIO_FACTORIES[name]()
        else:
            raise ValueError(
                f"Unknown scenario {name!r}. "
                f"Call list_scenarios for the available set."
            )
        payload = _scenario_to_topology_payload(scenario, name)
        return envelope(
            payload,
            source=f"adapter.scenario_topology({name!r})",
            observed_at_ns=None,
            staleness_class="fresh",
        )

    @server.tool()
    def get_fabric_counters(name: str, run_id: str | None = None) -> dict[str, Any]:
        """Run a scenario and return per-port PFC + ECN-CN counter records.

        Each port record carries BOTH counter classes side-by-side. PFC
        counts are broken down by event direction (pause_sent, pause_rcvd,
        resume_sent, resume_rcvd); ECN-CN is the count of CE-stamps emitted
        at egress. Zero counts surface as ``0``, not as missing fields —
        zero is data, not absence. No fabric-wide totals row is emitted;
        callers compute aggregates from per-port records.

        The diagnostic surface this enables: PFC pause_sent elevated
        alongside ECN marks_sent ~0 on the same fabric is the
        SRE-recognizable signature for DCQCN running blind (ECN
        misconfiguration). Splitting these classes across separate tools
        would let a caller observe one without the discriminator — the
        constraint this tool's design enforces is that the agent always
        sees both at once.

        Parameters
        ----------
        name:
            One of the names returned by ``list_scenarios``.
        run_id:
            Optional run identifier (used as the trace-dir name).

        Returns the response envelope; ``data.ports`` is a list of
        per-(node_id, if_index) records.
        """
        scenario_topology: Topology | None = None
        if name == "spike-burst":
            result = driver.run_scenario("spike-burst", run_id=run_id)
        elif name in BUILTIN_SCENARIO_FACTORIES:
            scenario = BUILTIN_SCENARIO_FACTORIES[name]()
            scenario_topology = scenario.custom_topology
            result = driver.run_scenario(scenario, run_id=run_id)
        else:
            raise ValueError(
                f"Unknown scenario {name!r}. "
                f"Call list_scenarios for the available set."
            )

        pfc_path = result.trace_dir / "pfc.txt"
        ecn_path = result.trace_dir / "ecn.txt"
        counters_path = result.trace_dir / "counters.txt"
        pfc_events = parse_pfc_file(pfc_path) if pfc_path.exists() else []
        ecn_events = parse_ecn_file(ecn_path) if ecn_path.exists() else []
        rollup_rows = (
            parse_counters_file(counters_path) if counters_path.exists() else []
        )
        aggregate = aggregate_counters(
            pfc_events, ecn_events, rollup_rows, scenario_topology
        )

        return envelope(
            {
                "scenario": result.scenario,
                "run_id": result.trace_dir.name,
                "trace_dir": str(result.trace_dir),
                "ports": aggregate["ports"],
            },
            source=f"driver.run_scenario({name!r})+counters_aggregate",
            observed_at_ns=None,
            staleness_class="fresh",
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
