"""Compile Scenario declarations into substrate ``config-burst.txt`` format.

The substrate's config format is line-oriented ``KEY VALUE [VALUE...]``,
no comments. Field order in the emitted file mirrors the spike's known-good
baseline so a key-by-key comparison between a compiled-baseline scenario
and the spike's baseline ``config.txt`` is the empty set.

Knobs that aren't yet user-settable on Scenario (rate-control timers, L2
parameters, window/feedback flags) are emitted with their spike-validated
defaults from a frozen block inside this module. Promoting a frozen
default to a Scenario field is a one-line change here plus one new field
on Scenario.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from doppelganger.scenarios.types import EcnSpeedMap, Scenario


class ScenarioCompileError(ValueError):
    """Raised when a Scenario cannot be compiled (invalid field values)."""


# Paths a custom topology/traffic file gets written to inside the
# bind-mounted trace dir. The container sees them at ``/traces/...``;
# the substrate's parser opens them via the same string.
CUSTOM_TOPOLOGY_PATH_IN_CONTAINER = "/traces/topology.txt"
CUSTOM_FLOW_PATH_IN_CONTAINER = "/traces/flow.txt"


# Rate-control / window / feedback block. Spike-validated defaults; same
# order as the spike's baseline config.txt. Promote a key to a Scenario
# field when a scenario needs to vary it.
_RATE_CONTROL_BLOCK: list[tuple[str, str]] = [
    ("ALPHA_RESUME_INTERVAL", "1"),
    ("RATE_DECREASE_INTERVAL", "4"),
    ("CLAMP_TARGET_RATE", "0"),
    ("RP_TIMER", "900"),
    ("EWMA_GAIN", "0.00390625"),
    ("FAST_RECOVERY_TIMES", "1"),
    ("RATE_AI", "50Mb/s"),
    ("RATE_HAI", "100Mb/s"),
    ("MIN_RATE", "100Mb/s"),
    ("DCTCP_RATE_AI", "1000Mb/s"),
]

_L2_BLOCK: list[tuple[str, str]] = [
    ("L2_CHUNK_SIZE", "4000"),
    ("L2_ACK_INTERVAL", "1"),
    ("L2_BACK_TO_ZERO", "0"),
]

_WINDOW_FEEDBACK_BLOCK: list[tuple[str, str]] = [
    ("HAS_WIN", "1"),
    ("GLOBAL_T", "1"),
    ("VAR_WIN", "1"),
    ("FAST_REACT", "1"),
    ("U_TARGET", "0.95"),
    ("MI_THRESH", "5"),
    ("INT_MULTI", "1"),
    ("MULTI_RATE", "0"),
    ("SAMPLE_FEEDBACK", "0"),
    ("PINT_LOG_BASE", "1.05"),
    ("PINT_PROB", "1.0"),
]


def compile_scenario(scenario: Scenario, output_path: Path) -> Path:
    """Compile a Scenario into a substrate ``config-burst.txt`` file.

    Parameters
    ----------
    scenario:
        The scenario to compile.
    output_path:
        Where to write the compiled config. Parent directory is created if
        missing.

    Returns
    -------
    Path
        Same as ``output_path``, returned for fluent use.

    Raises
    ------
    ScenarioCompileError
        If any Scenario field is out of range or malformed.
    """
    _validate(scenario)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []

    # Top: queue / PFC enable
    lines.append(f"ENABLE_QCN {1 if scenario.enable_qcn else 0}")
    lines.append("USE_DYNAMIC_PFC_THRESHOLD 1")
    lines.append("")
    lines.append("PACKET_PAYLOAD_SIZE 1000")
    lines.append("")

    # Topology / file paths. Custom topology/traffic override the
    # bundled paths; the Driver will have written compiled files into
    # the bind-mounted trace dir at the paths below before invoking the
    # substrate.
    topology_file = (
        CUSTOM_TOPOLOGY_PATH_IN_CONTAINER
        if scenario.custom_topology is not None
        else scenario.topology.topology_path
    )
    flow_file = (
        CUSTOM_FLOW_PATH_IN_CONTAINER
        if scenario.custom_traffic is not None
        else scenario.topology.flow_path
    )
    lines.append(f"TOPOLOGY_FILE {topology_file}")
    lines.append(f"FLOW_FILE {flow_file}")
    lines.append("TRACE_FILE mix/trace.txt")
    lines.append("TRACE_OUTPUT_FILE mix/mix.tr")
    lines.append("FCT_OUTPUT_FILE mix/fct.txt")
    lines.append("PFC_OUTPUT_FILE mix/pfc.txt")
    lines.append("")

    # Simulation duration
    lines.append(f"SIMULATOR_STOP_TIME {_format_float(scenario.sim_duration_seconds)}")
    lines.append("")

    # Congestion control
    lines.append(f"CC_MODE {scenario.cc_mode}")
    for key, value in _RATE_CONTROL_BLOCK:
        if key == "MIN_RATE" and scenario.min_rate_override is not None:
            lines.append(f"MIN_RATE {scenario.min_rate_override}")
        else:
            lines.append(f"{key} {value}")
    lines.append("")

    # Failure injection: per-link error rate, then static link-down triple
    lines.append(f"ERROR_RATE_PER_LINK {scenario.link_error_rate:.4f}")

    # L2
    for key, value in _L2_BLOCK:
        lines.append(f"{key} {value}")
    lines.append("")

    # Window / feedback (spike-validated block; blank-line layout matches
    # the spike's baseline config so the diff against it is purely semantic)
    for key, value in _WINDOW_FEEDBACK_BLOCK:
        lines.append(f"{key} {value}")
    lines.append("")
    lines.append("RATE_BOUND 1")
    lines.append("")
    lines.append("ACK_HIGH_PRIO 0")
    lines.append("")

    # Static link-down (no flap injected in this scenario)
    lines.append("LINK_DOWN 0 0 0")
    lines.append("")
    lines.append("ENABLE_TRACE 1")
    lines.append("")

    # ECN threshold maps
    lines.append(f"KMAX_MAP {_format_int_speed_map(scenario.kmax_map)}")
    lines.append(f"KMIN_MAP {_format_int_speed_map(scenario.kmin_map)}")
    lines.append(f"PMAX_MAP {_format_float_speed_map(scenario.pmax_map)}")

    # Buffer + qlen monitoring. The substrate parses these as nanoseconds.
    # The spike's bundled config used hardcoded 2e9 / 2.01e9 ns (= 2.0 s
    # window) — past any reasonable SIMULATOR_STOP_TIME, which is one of
    # the reasons qlen.txt comes up empty. The substrate-side issue
    # (monitor_buffer is never initially scheduled) was the deeper cause;
    # see provandal/ns3-datacenter master for the kickoff fix. Even with
    # the kickoff in place, sane values here are required.
    lines.append(f"BUFFER_SIZE {scenario.buffer_size}")
    lines.append("QLEN_MON_FILE mix/qlen.txt")
    qlen_end_ns = int(scenario.sim_duration_seconds * 1_000_000_000)
    lines.append("QLEN_MON_START 0")
    lines.append(f"QLEN_MON_END {qlen_end_ns}")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def _validate(scenario: Scenario) -> None:
    if scenario.sim_duration_seconds <= 0:
        raise ScenarioCompileError(
            f"sim_duration_seconds must be positive, got {scenario.sim_duration_seconds!r}"
        )
    if not 0.0 <= scenario.link_error_rate <= 1.0:
        raise ScenarioCompileError(
            f"link_error_rate must be in [0, 1], got {scenario.link_error_rate!r}"
        )
    if scenario.cc_mode < 0:
        raise ScenarioCompileError(f"cc_mode must be non-negative, got {scenario.cc_mode!r}")
    if scenario.buffer_size <= 0:
        raise ScenarioCompileError(
            f"buffer_size must be positive, got {scenario.buffer_size!r}"
        )
    for label, ecn_map in [
        ("kmax_map", scenario.kmax_map),
        ("kmin_map", scenario.kmin_map),
        ("pmax_map", scenario.pmax_map),
    ]:
        if not ecn_map:
            raise ScenarioCompileError(f"{label} must contain at least one entry")


def _format_float(value: float) -> str:
    """Compact float repr, no trailing zeros (matches the spike's style)."""
    return f"{value:g}"


def _format_int_speed_map(ecn_map: EcnSpeedMap) -> str:
    """Substrate format: ``count speed1 value1 speed2 value2 …`` (integer values)."""
    return _format_speed_map(ecn_map, lambda v: str(int(v)))


def _format_float_speed_map(ecn_map: EcnSpeedMap) -> str:
    """Substrate format: ``count speed1 value1 speed2 value2 …`` (float values)."""
    return _format_speed_map(ecn_map, _format_float)


def _format_speed_map(ecn_map: EcnSpeedMap, format_value: Callable[[float], str]) -> str:
    parts: list[str] = [str(len(ecn_map))]
    for speed, value in ecn_map:
        parts.append(str(int(speed)))
        parts.append(format_value(value))
    return " ".join(parts)
