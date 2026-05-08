"""Driver data types.

Per Doppelgänger v0.2 §4.2, every flow the scenario *intended to run* produces
a record — completed or not. The substrate's native `fct.txt` only emits records
for completed flows; the Driver is responsible for cross-referencing scenario
metadata against the trace to surface incomplete flows. The cross-referencing
arrives with the topology compiler (later commit). This module defines the shape
the Driver populates today (completed flows) and will populate tomorrow
(incomplete flows with last-observed state).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class CompletionStatus(str, Enum):
    """Outcome of a flow the scenario intended to run."""

    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    DROPPED_WITHOUT_COMPLETION = "dropped_without_completion"


@dataclass
class PerFlowRecord:
    """One record per flow the scenario intended to run.

    Field set per Doppelgänger v0.2 §4.2. Fields are populated based on
    `status`: completed flows fill `actual_*` fields from `fct.txt`;
    incomplete flows leave them as None and the record exists because the
    scenario metadata said this flow should have run.
    """

    # Identifying fields (always present; populated from scenario or trace)
    sip: str
    dip: str
    sport: int
    dport: int
    flow_id: str | None = None
    intended_start_ns: int | None = None
    intended_size_bytes: int | None = None

    # Outcome
    status: CompletionStatus = CompletionStatus.COMPLETED

    # Actual measurements (completed flows only; None for incomplete)
    actual_size_bytes: int | None = None
    actual_start_ns: int | None = None
    actual_completion_ns: int | None = None
    fct_ns: int | None = None
    standalone_fct_ns: int | None = None

    # Path through the fabric (full for completed, partial for incomplete-where-reachable)
    path: list[str] = field(default_factory=list)

    # Congestion experience (last-observed state for incomplete flows)
    ecn_marks: int | None = None
    rtt_samples_ns: list[int] = field(default_factory=list)
    retransmissions: int | None = None

    @property
    def slowdown(self) -> float | None:
        """Ratio of measured FCT to ideal (uncongested) FCT, completed flows only."""
        if self.fct_ns is None or self.standalone_fct_ns is None:
            return None
        if self.standalone_fct_ns == 0:
            return float("inf")
        return self.fct_ns / self.standalone_fct_ns


@dataclass(frozen=True)
class PfcEvent:
    """One row from pfc.txt — per-PFC-frame event at a QbbNetDevice.

    `event_type`: 0=resume_rcvd, 1=pause_rcvd, 2=pause_sent, 3=resume_sent.
    """

    timestamp_ns: int
    node_id: int
    node_type: int
    if_index: int
    event_type: int

    @property
    def is_pause(self) -> bool:
        return self.event_type in (1, 2)


@dataclass(frozen=True)
class EcnMarkEvent:
    """One row from ecn.txt — one CE-stamp event at a switch egress queue."""

    timestamp_ns: int
    switch_id: int
    if_index: int
    q_index: int
