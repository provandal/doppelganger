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

    ``sport`` is ``int | None`` (2026-05-12) — completed flows carry an
    int from fct.txt; incomplete flows carry None because the substrate
    assigns sport at flow-schedule time, after intended.txt is written,
    and a flow that never schedules never gets one. Using a sentinel
    like 0 here let agents read it as a real diagnostic signal and
    fabricate plausible-but-wrong stories around it (silent-drops trace
    4911e4f5... on 2026-05-12); None forces the field to render as
    null in the response and disambiguates "unknown" from a real port.
    """

    # Identifying fields (always present; populated from scenario or trace)
    sip: str
    dip: str
    sport: int | None
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
    `q_index`: 802.1p priority of the PFC frame (0-7). Added 2026-05-10
    for SONiC-shaped per-priority PFC reporting; substrate fork emits
    via the new ``QbbPfcQ`` trace source.
    """

    timestamp_ns: int
    node_id: int
    node_type: int
    if_index: int
    event_type: int
    q_index: int

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


@dataclass(frozen=True)
class CounterRollupRow:
    """One row from counters.txt — end-of-sim per-(switch, port, queue) counters.

    Per-queue dimension (q_index 0-7) added 2026-05-10 for SONiC alignment:
    matches the per-priority breakdown SONiC operators see via
    ``show queue counters`` / ``show queue watermark`` /
    ``show priority-group watermark``. Substrate emits one row per
    (switch, port, queue) that saw any activity OR any non-zero watermark
    sample; queues with no activity are absent from the file and
    zero-filled by the aggregator from the scenario topology.

    Distinct shape from PfcEvent / EcnMarkEvent: those are per-event
    streams; this is the SNMP-style cumulative snapshot real switches
    expose via interface counters.

    Fields:
        switch_id, if_index, q_index: identifying triple.
        rx_packets, rx_bytes: enqueued into this queue (counts what
            arrived from the routing stage destined for this priority).
        tx_packets, tx_bytes: dequeued from this queue.
        dropped_packets: admission-control failures into this queue.
        qlen_peak_bytes: per-queue egress depth peak (sampled
            periodically, mirrors SAI ``SAI_QUEUE_STAT_WATERMARK_BYTES``).
        pg_watermark_bytes: per-priority-group ingress occupancy peak
            (sampled periodically, mirrors SAI
            ``SAI_INGRESS_PRIORITY_GROUP_STAT_XOFF_ROOM_WATERMARK_BYTES``).
    """

    switch_id: int
    if_index: int
    q_index: int
    rx_packets: int
    rx_bytes: int
    tx_packets: int
    tx_bytes: int
    dropped_packets: int
    qlen_peak_bytes: int
    pg_watermark_bytes: int
