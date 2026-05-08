"""Aggregate parsed PFC and ECN-mark events into per-port counter records.

The aggregator emits one record per ``(node_id, if_index)`` pair,
carrying *both* counter classes side-by-side. Honest zero handling: every
field is always populated; an absence of events surfaces as ``0``, not as
a missing key. Splitting PFC and ECN aggregates across separate records or
separate tools would let a caller see "PFC elevated" without seeing the
ECN-marks-near-zero discriminator — exactly the answer-key leak the
counter-asymmetry constraint exists to prevent.

The diagnostic the skill surfaced at Stage 5b reads from this shape:

* PFC pause_sent elevated alongside ECN marks_sent ~0 on the same fabric
  → DCQCN running blind (KMIN above buffer capacity, or QCN disabled);
  the asymmetry is the SRE-recognizable signature.
* PFC pause_sent low with ECN marks_sent moderate-to-high → DCQCN
  engaged and throttling normally; congestion is real but managed.
* Both elevated → genuine fabric overload exceeding ECN's ability to
  shape; a different fault class than ECN misconfiguration.
"""

from __future__ import annotations

from typing import Any

from doppelganger.driver.types import EcnMarkEvent, PfcEvent

# event_type encoding from substrate's get_pfc callback
_PFC_RESUME_RCVD = 0
_PFC_PAUSE_RCVD = 1
_PFC_PAUSE_SENT = 2
_PFC_RESUME_SENT = 3


def _empty_record(node_id: int, node_type: int, if_index: int) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "node_type": node_type,
        "if_index": if_index,
        "pfc_pause_sent": 0,
        "pfc_pause_rcvd": 0,
        "pfc_resume_sent": 0,
        "pfc_resume_rcvd": 0,
        "ecn_marks_sent": 0,
    }


def aggregate_counters(
    pfc_events: list[PfcEvent],
    ecn_events: list[EcnMarkEvent],
) -> dict[str, Any]:
    """Roll parsed events into per-port counter records.

    Returns ``{"ports": [...]}``: a list of per-``(node_id, if_index)``
    records, sorted stably. Each record always includes all PFC and ECN
    counter fields, zero-filled when no events fired.

    Deliberately does NOT emit a fabric-wide totals row. Stage 5a's
    closing test (2026-05-08) found that pre-aggregating totals handed
    the agent a one-line answer key: the model literally read off
    ``totals.ecn_marks_sent: 0`` as "the smoking gun." Forcing the
    agent to compute aggregates from per-port records preserves the
    investigative discipline the eval is designed to surface.
    """
    ports: dict[tuple[int, int], dict[str, Any]] = {}

    for ev in pfc_events:
        key = (ev.node_id, ev.if_index)
        rec = ports.setdefault(key, _empty_record(ev.node_id, ev.node_type, ev.if_index))
        if ev.event_type == _PFC_RESUME_RCVD:
            rec["pfc_resume_rcvd"] += 1
        elif ev.event_type == _PFC_PAUSE_RCVD:
            rec["pfc_pause_rcvd"] += 1
        elif ev.event_type == _PFC_PAUSE_SENT:
            rec["pfc_pause_sent"] += 1
        elif ev.event_type == _PFC_RESUME_SENT:
            rec["pfc_resume_sent"] += 1

    for ev in ecn_events:
        key = (ev.switch_id, ev.if_index)
        # ECN marks only fire on switches; node_type=1.
        rec = ports.setdefault(key, _empty_record(ev.switch_id, 1, ev.if_index))
        rec["ecn_marks_sent"] += 1

    port_records = sorted(ports.values(), key=lambda r: (r["node_id"], r["if_index"]))

    return {"ports": port_records}
