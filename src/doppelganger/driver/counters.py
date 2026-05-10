"""Aggregate parsed PFC, ECN, and counters.txt rollup into SONiC-shaped
per-(switch, port) records carrying a per-queue array.

SONiC alignment (Stage 5a-realistic SONiC counter expansion, 2026-05-10):
the per-port shape mirrors what an SRE actually sees from
``show interfaces status`` + ``show queue counters`` + ``show queue
watermark`` + ``show priority-group watermark`` + ``show pfc counters``,
collated per port. Each port record carries:

* Interface state: ``oper_status``, ``admin_status``, ``speed_bps``,
  ``mtu_bytes`` (from the topology declaration; substrate doesn't model
  link-down or speed renegotiation in steady-state runs).
* A ``queues`` array of 8 per-priority records (q_index 0-7), each
  carrying volumetric counters (rx/tx packets+bytes, dropped packets),
  egress + ingress watermarks (qlen_peak_bytes, pg_watermark_bytes),
  per-priority PFC counts (pause_sent, pause_rcvd, resume_sent,
  resume_rcvd), and per-priority ECN-CN marks (ecn_marks_sent).

**Port-level aggregates are deliberately NOT emitted.** Pre-aggregating
across queues is itself a skill — summing PFC / ECN counts across
priorities, computing total port throughput, etc. By omitting these
the agent has to do the arithmetic itself, which is honest about what
"reading the counter dump" means in production. (Erik's call,
2026-05-10. We may add aggregates later if a skill demonstrably
needs them; treating aggregates as optional tool-output rather than
free signal preserves Stage 5b's measurement integrity.)

Honest zero handling: every queue field is always populated; an absence
of events surfaces as ``0``, never as a missing key. The structural
leak guard from Stage 5a (no field disappears just because no events
fired against it) extends to every per-queue field added here.

Topology-aware enumeration: when a Topology is provided, every switch
port the topology declares appears in the output with its full 8-queue
array zero-filled. Without topology, only ports observed in the
inputs appear (Stage 5a backward compatibility).

Diagnostic patterns this exposes:

* PFC pause_sent elevated on q=3 + ECN marks_sent ~0 on q=3 across the
  fabric → DCQCN running blind on the lossless queue (KMIN above
  buffer capacity, or marking disabled on PG3); the per-priority
  isolation is exactly what SONiC's ``show pfc counters`` would surface.
* Single port's q=3 ``qlen_peak_bytes`` orders of magnitude above
  siblings → isolated egress congestion at that port-priority.
* PG watermark elevated on a port's q=3 alongside PFC pause_sent on
  the same priority → upstream is being told to back off; the port is
  ingress-pressuring whoever sends to it.
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

QUEUE_COUNT = 8

# The substrate's PACKET_PAYLOAD_SIZE is fabric-wide and currently fixed
# at 1000 bytes (see scenarios/compiler.py and the bundled
# config-burst.txt). If we ever expose per-scenario MTU as a Scenario
# field, plumb it through aggregate_counters and remove this constant.
SUBSTRATE_FIXED_MTU_BYTES = 1000

# event_type encoding from substrate's get_pfc callback
_PFC_RESUME_RCVD = 0
_PFC_PAUSE_RCVD = 1
_PFC_PAUSE_SENT = 2
_PFC_RESUME_SENT = 3


def _empty_queue(q_index: int) -> dict[str, Any]:
    return {
        "q_index": q_index,
        "rx_packets": 0,
        "rx_bytes": 0,
        "tx_packets": 0,
        "tx_bytes": 0,
        "dropped_packets": 0,
        "qlen_peak_bytes": 0,
        "pg_watermark_bytes": 0,
        "pfc_pause_sent": 0,
        "pfc_pause_rcvd": 0,
        "pfc_resume_sent": 0,
        "pfc_resume_rcvd": 0,
        "ecn_marks_sent": 0,
    }


def _empty_port_record(
    node_id: int,
    if_index: int,
    *,
    speed_bps: int = 0,
    mtu_bytes: int = 0,
    oper_status: str = "up",
    admin_status: str = "up",
) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "if_index": if_index,
        "node_type": 1,  # switch (host ports excluded)
        "oper_status": oper_status,
        "admin_status": admin_status,
        "speed_bps": speed_bps,
        "mtu_bytes": mtu_bytes,
        "queues": [_empty_queue(q) for q in range(QUEUE_COUNT)],
    }


def _switch_port_keys(topology: Topology) -> list[tuple[int, int, int]]:
    """Enumerate (switch_id, if_index, port_link_speed_bps) for every
    switch port the topology declares.

    Substrate convention: each switch's NetDevice indices start at 1
    (index 0 is the loopback). A leaf has ``hosts_per_leaf + spines``
    ports; a spine has ``leaves`` ports. The exact host-vs-spine
    ordering of if_indices is set by topology.txt link-declaration
    order — the Doppelgänger compiler emits host-side links first then
    leaf↔spine links (see ``scenarios/topology.py``), so for each
    leaf, if_indices 1..hosts_per_leaf are downlinks (host_link_bps)
    and hosts_per_leaf+1..hosts_per_leaf+spines are uplinks
    (spine_link_bps); for each spine, if_indices 1..leaves are
    downlinks (spine_link_bps).
    """
    first_leaf = topology.first_leaf_id()
    first_spine = topology.first_spine_id()
    keys: list[tuple[int, int, int]] = []
    for leaf_offset in range(topology.leaves):
        switch_id = first_leaf + leaf_offset
        for if_idx in range(1, topology.hosts_per_leaf + 1):
            keys.append((switch_id, if_idx, topology.host_link_bps))
        for if_idx in range(
            topology.hosts_per_leaf + 1,
            topology.hosts_per_leaf + topology.spines + 1,
        ):
            keys.append((switch_id, if_idx, topology.spine_link_bps))
    for spine_offset in range(topology.spines):
        switch_id = first_spine + spine_offset
        for if_idx in range(1, topology.leaves + 1):
            keys.append((switch_id, if_idx, topology.spine_link_bps))
    return keys


def aggregate_counters(
    pfc_events: list[PfcEvent],
    ecn_events: list[EcnMarkEvent],
    rollup_rows: list[CounterRollupRow] | None = None,
    topology: Topology | None = None,
) -> dict[str, Any]:
    """Roll parsed counter sources into SONiC-shaped per-port records.

    Parameters
    ----------
    pfc_events:
        Parsed pfc.txt events (per-frame stream, includes q_index since
        2026-05-10).
    ecn_events:
        Parsed ecn.txt events (per-CE-stamp stream, includes q_index).
    rollup_rows:
        Parsed counters.txt rows (per-(switch, port, queue) end-of-sim
        snapshot). ``None`` and ``[]`` are equivalent.
    topology:
        When provided, every switch port the topology declares appears
        in the output with all 8 queues zero-filled. Interface state
        fields (speed, mtu) are populated from this. ``None`` means
        only observed (switch, port, queue) tuples appear and link
        speed defaults to 0.

    Returns ``{"ports": [...]}``: a list of per-(node_id, if_index)
    records, sorted stably. Each record carries interface state +
    ``queues`` (an 8-element array). Aggregates across queues are
    deliberately omitted — the agent must compute them.
    """
    rollup_rows = rollup_rows or []
    mtu = SUBSTRATE_FIXED_MTU_BYTES if topology is not None else 0

    # port_link_speed[(switch_id, if_index)] = bps
    port_link_speed: dict[tuple[int, int], int] = {}
    if topology is not None:
        for switch_id, if_index, speed_bps in _switch_port_keys(topology):
            port_link_speed[(switch_id, if_index)] = speed_bps

    # ports[(switch_id, if_index)] = port record
    ports: dict[tuple[int, int], dict[str, Any]] = {}

    if topology is not None:
        for (switch_id, if_index), speed_bps in port_link_speed.items():
            ports[(switch_id, if_index)] = _empty_port_record(
                switch_id, if_index,
                speed_bps=speed_bps, mtu_bytes=mtu,
            )

    def _ensure_port(switch_id: int, if_index: int) -> dict[str, Any]:
        key = (switch_id, if_index)
        if key not in ports:
            ports[key] = _empty_port_record(
                switch_id, if_index,
                speed_bps=port_link_speed.get(key, 0),
                mtu_bytes=mtu,
            )
        return ports[key]

    for ev in pfc_events:
        if not (0 <= ev.q_index < QUEUE_COUNT):
            continue
        port = _ensure_port(ev.node_id, ev.if_index)
        q = port["queues"][ev.q_index]
        if ev.event_type == _PFC_RESUME_RCVD:
            q["pfc_resume_rcvd"] += 1
        elif ev.event_type == _PFC_PAUSE_RCVD:
            q["pfc_pause_rcvd"] += 1
        elif ev.event_type == _PFC_PAUSE_SENT:
            q["pfc_pause_sent"] += 1
        elif ev.event_type == _PFC_RESUME_SENT:
            q["pfc_resume_sent"] += 1

    for ev in ecn_events:
        if not (0 <= ev.q_index < QUEUE_COUNT):
            continue
        port = _ensure_port(ev.switch_id, ev.if_index)
        port["queues"][ev.q_index]["ecn_marks_sent"] += 1

    for row in rollup_rows:
        if not (0 <= row.q_index < QUEUE_COUNT):
            continue
        port = _ensure_port(row.switch_id, row.if_index)
        q = port["queues"][row.q_index]
        q["rx_packets"] = row.rx_packets
        q["rx_bytes"] = row.rx_bytes
        q["tx_packets"] = row.tx_packets
        q["tx_bytes"] = row.tx_bytes
        q["dropped_packets"] = row.dropped_packets
        q["qlen_peak_bytes"] = row.qlen_peak_bytes
        q["pg_watermark_bytes"] = row.pg_watermark_bytes

    port_records = sorted(
        ports.values(), key=lambda r: (r["node_id"], r["if_index"])
    )

    return {"ports": port_records}
