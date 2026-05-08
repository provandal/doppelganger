"""Aggregate parsed PFC / ECN events and the counters.txt rollup into
per-port records.

The aggregator emits one record per ``(node_id, if_index)`` pair, carrying
PFC counters, ECN-mark counts, *and* volumetric counters (rx/tx
packets+bytes, drops, qlen peak) side-by-side. Honest zero handling: every
field is always populated; an absence of events surfaces as ``0``, not as
a missing key. Splitting these classes across separate records or separate
tools would let a caller see "PFC elevated" without seeing the
ECN-marks-near-zero discriminator — exactly the answer-key leak the
counter-asymmetry constraint exists to prevent.

The topology-aware mode (Stage 5a-realistic, 2026-05-09) extends the
record set: when a Topology is passed, every switch port the topology
declares is included in the output, zero-filled from the topology's
structural enumeration even when the substrate emitted no events for
that port. Stage 5a's closing test (trace
``668a11072f2a9d51814ce55841fca6ef``) showed that a 2-port-only payload
let naked Opus 4.7 reach the correct ECN-misconfig diagnosis trivially —
because asymmetry was absolute (0 vs 8288) rather than relative. With
zero-fill enumeration the agent has to find the storm port among
hundreds of zeroes, which is the production-shaped triage problem.

The diagnostic the skill surfaces reads from this shape:

* PFC pause_sent elevated alongside ECN marks_sent ~0 on the same fabric
  → DCQCN running blind (KMIN above buffer capacity, or QCN disabled);
  the asymmetry is the SRE-recognizable signature.
* PFC pause_sent low with ECN marks_sent moderate-to-high → DCQCN
  engaged and throttling normally; congestion is real but managed.
* Both elevated → genuine fabric overload exceeding ECN's ability to
  shape; a different fault class than ECN misconfiguration.
* Volumetric asymmetry (rx/tx anomalously high on a port relative to
  the fabric baseline) is the entry-point signal for picking which
  port to interrogate further when the fabric is large.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doppelganger.driver.types import (
    CounterRollupRow,
    EcnMarkEvent,
    PfcEvent,
)

if TYPE_CHECKING:
    from doppelganger.scenarios.topology import Topology

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
        "rx_packets": 0,
        "rx_bytes": 0,
        "tx_packets": 0,
        "tx_bytes": 0,
        "drops": 0,
        "qlen_peak_bytes": 0,
    }


def _switch_port_keys(topology: Topology) -> list[tuple[int, int]]:
    """Enumerate (switch_id, if_index) pairs for every port in the topology.

    Substrate convention: each switch's NetDevice indices start at 1
    (index 0 is the loopback). A leaf has ``hosts_per_leaf + spines``
    ports (one per host downlink, one per spine uplink). A spine has
    ``leaves`` ports. The exact host-vs-spine ordering of if_indices
    depends on the order links are declared in topology.txt; for
    enumeration purposes we only need the count, not the role mapping.
    """
    first_leaf = topology.first_leaf_id()
    first_spine = topology.first_spine_id()
    keys: list[tuple[int, int]] = []
    leaf_port_count = topology.hosts_per_leaf + topology.spines
    for leaf_offset in range(topology.leaves):
        switch_id = first_leaf + leaf_offset
        for if_idx in range(1, leaf_port_count + 1):
            keys.append((switch_id, if_idx))
    spine_port_count = topology.leaves
    for spine_offset in range(topology.spines):
        switch_id = first_spine + spine_offset
        for if_idx in range(1, spine_port_count + 1):
            keys.append((switch_id, if_idx))
    return keys


def aggregate_counters(
    pfc_events: list[PfcEvent],
    ecn_events: list[EcnMarkEvent],
    rollup_rows: list[CounterRollupRow] | None = None,
    topology: Topology | None = None,
) -> dict[str, Any]:
    """Roll parsed counter sources into per-port records.

    Parameters
    ----------
    pfc_events:
        Parsed pfc.txt events (per-frame stream).
    ecn_events:
        Parsed ecn.txt events (per-CE-stamp stream).
    rollup_rows:
        Parsed counters.txt rows (end-of-sim per-port volumetric snapshot).
        ``None`` and ``[]`` are equivalent; volumetric fields stay zero.
    topology:
        When provided, every switch port the topology declares appears in
        the output, zero-filled when the substrate emitted no events. When
        ``None``, only ports observed in the inputs appear (Stage 5a
        compatibility).

    Returns ``{"ports": [...]}``: a list of per-``(node_id, if_index)``
    records, sorted stably. Each record always includes every counter
    field across PFC, ECN-mark, and volumetric classes — zero-filled
    where no events fired. Deliberately does NOT emit a fabric-wide
    totals row (Stage 5a finding, 2026-05-08).
    """
    rollup_rows = rollup_rows or []
    ports: dict[tuple[int, int], dict[str, Any]] = {}

    if topology is not None:
        for switch_id, if_index in _switch_port_keys(topology):
            ports[(switch_id, if_index)] = _empty_record(
                node_id=switch_id, node_type=1, if_index=if_index
            )

    for ev in pfc_events:
        key = (ev.node_id, ev.if_index)
        rec = ports.setdefault(
            key, _empty_record(ev.node_id, ev.node_type, ev.if_index)
        )
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
        rec = ports.setdefault(key, _empty_record(ev.switch_id, 1, ev.if_index))
        rec["ecn_marks_sent"] += 1

    for row in rollup_rows:
        key = (row.switch_id, row.if_index)
        rec = ports.setdefault(
            key, _empty_record(row.switch_id, 1, row.if_index)
        )
        rec["rx_packets"] = row.rx_packets
        rec["rx_bytes"] = row.rx_bytes
        rec["tx_packets"] = row.tx_packets
        rec["tx_bytes"] = row.tx_bytes
        rec["drops"] = row.drops
        rec["qlen_peak_bytes"] = row.qlen_peak_bytes

    port_records = sorted(
        ports.values(), key=lambda r: (r["node_id"], r["if_index"])
    )

    return {"ports": port_records}
