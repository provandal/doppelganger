"""Traffic-pattern declarations + flow.txt compiler.

Compiles a ``TrafficPattern`` (a list of Flow declarations) into the
substrate's ``flow.txt`` format. The substrate's parser is at
``examples/PowerTCP/powertcp-evaluation-burst.cc:145``:

    flowf >> flow_input.src
          >> flow_input.dst
          >> flow_input.pg
          >> flow_input.dport
          >> flow_input.maxPacketCount
          >> flow_input.start_time;

``maxPacketCount`` is the number of packets the QP is configured to send
before stopping; the substrate's bundled flow files set it to ``1e12``
(effectively infinite, open-loop sender) so flows run until
``SIMULATOR_STOP_TIME`` halts the sim. Setting a small ``packet_count``
produces short bursts that complete within the simulation; setting
``OPEN_LOOP`` keeps the QP active for the whole sim.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Substrate convention: 1e12 packets ≈ "open-loop sender; stop on sim end"
OPEN_LOOP_PACKETS: int = 10**12


class TrafficCompileError(ValueError):
    """Raised when a TrafficPattern is invalid."""


@dataclass(frozen=True)
class Flow:
    """One flow declaration.

    Parameters
    ----------
    src:
        Source host ID.
    dst:
        Destination host ID.
    priority_group:
        PFC priority group (typically 3 for RoCEv2 traffic).
    dst_port:
        Destination port. Must be unique per (src, dst) pair if multiple
        flows share endpoints, or the substrate's port-tracking will
        collide.
    packet_count:
        Number of packets the QP transmits. Use :data:`OPEN_LOOP_PACKETS`
        for sustained traffic; smaller values for short bursts.
    start_time_seconds:
        When the flow starts, in simulation seconds. Must be < the
        scenario's ``sim_duration_seconds``.
    """

    src: int
    dst: int
    priority_group: int
    dst_port: int
    packet_count: int
    start_time_seconds: float


@dataclass(frozen=True)
class TrafficPattern:
    """A list of Flow declarations + metadata.

    Parameters
    ----------
    flows:
        Tuple of Flow records. Order is preserved in the emitted file.
    name:
        Short identifier for the pattern (used in scenario metadata).
    description:
        Free-form description for documentation.
    """

    flows: tuple[Flow, ...]
    name: str = ""
    description: str = ""

    def __len__(self) -> int:
        return len(self.flows)


def compile_traffic(pattern: TrafficPattern, output_path: Path) -> Path:
    """Compile a TrafficPattern into a substrate ``flow.txt`` file.

    Raises
    ------
    TrafficCompileError
        If any flow has invalid fields or if the pattern is empty.
    """
    _validate(pattern)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = [str(len(pattern.flows))]
    for flow in pattern.flows:
        lines.append(
            f"{flow.src} {flow.dst} {flow.priority_group} {flow.dst_port} "
            f"{flow.packet_count} {flow.start_time_seconds:g}"
        )

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def _validate(pattern: TrafficPattern) -> None:
    if not pattern.flows:
        raise TrafficCompileError("TrafficPattern must contain at least one flow")
    for i, flow in enumerate(pattern.flows):
        if flow.src < 0:
            raise TrafficCompileError(f"flow[{i}].src must be non-negative")
        if flow.dst < 0:
            raise TrafficCompileError(f"flow[{i}].dst must be non-negative")
        if flow.src == flow.dst:
            raise TrafficCompileError(
                f"flow[{i}] has src==dst ({flow.src}); "
                f"the substrate cannot route a flow to itself"
            )
        if flow.priority_group < 0:
            raise TrafficCompileError(
                f"flow[{i}].priority_group must be non-negative"
            )
        if flow.packet_count <= 0:
            raise TrafficCompileError(
                f"flow[{i}].packet_count must be positive"
            )
        if flow.start_time_seconds < 0:
            raise TrafficCompileError(
                f"flow[{i}].start_time_seconds must be non-negative"
            )
