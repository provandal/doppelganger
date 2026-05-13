"""Aggregate parsed host_counters.txt rows into per-host records with a
derived ``drops_per_million`` rate.

Why the derived field: silent-drops scenarios surface PHY corruption as a
raw count in ``host_counters.txt``. The diagnostic question an SRE asks is
not "how many drops" but "what fraction of inbound traffic was
corrupted." Variance-pass results 2026-05-12 showed agents reaching CORRECT
on this scenario only when they kept an explicit verify-first stance —
"compute drops-per-million before dispatching cable swap." Promoting the
ratio to a substrate-derived field means the agent reads it rather than
having to compute it; the arithmetic step that gates verify-first
collapses into the response.

Denominator semantics: the count of packets the host's upstream leaf
*transmitted* toward this host. counters.txt already carries this as
per-(switch, port, queue) ``tx_packets``; for a host the relevant leaf
port is the one connecting that host to its leaf, summed across all 8
queues (a host-leaf link carries this host's traffic only). This is the
honest "what reached the wire" denominator — independent of whether
flows completed, independent of which flows were scheduled. Two
alternative denominators considered and rejected:

* ``intended.txt`` packets filtered by ``dip``: counts *scheduled*
  packets, not delivered. Silent-drops produces incomplete flows by
  design, so using intended-count systematically inflates the divisor
  on exactly the scenario this field is meant to lift.
* Substrate-side PhyRxEnd accumulator: doesn't exist. The substrate
  only subscribes ``PhyRxDrop`` on host NICs (see
  ``powertcp-evaluation-burst.cc`` lines 1058-1070). Adding a successful-rx
  accumulator would require a substrate commit.

Leaf-port mapping: pinned by ``scenarios/topology.py``'s link
emission order — each leaf's host links are emitted first, in host_id
order, before its uplinks. NS-3 assigns ``if_index`` sequentially as
NetDevices are installed (with index 0 reserved for loopback), so for
host ``H`` attached to leaf ``L = first_leaf + H // hosts_per_leaf`` the
leaf's host-facing port is ``if_index = (H % hosts_per_leaf) + 1``. This
is the same mapping ``driver/counters.py:_switch_port_keys`` already
relies on for per-port zero-fill.

Leak rule: when the denominator is 0 (host had no inbound traffic this
run, or counters.txt is missing/empty), ``drops_per_million`` is
``None``, not ``0``. Same instinct as ``PerFlowRecord.sport``: a
"didn't observe" sentinel must be distinguishable from a real
measurement of zero. Same pattern when ``topology`` is ``None``
(substrate-bundled scenarios with no Doppelgänger Topology declaration):
the leaf-port mapping cannot be computed, so the rate is ``None``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doppelganger.driver.parsers.host_counters import HostCounterRow
from doppelganger.driver.types import CounterRollupRow

if TYPE_CHECKING:
    from doppelganger.scenarios.topology import Topology


def _host_id_to_ip(host_id: int) -> str:
    """Substrate's ``node_id_to_ip`` mirrored in Python.

    Duplicated from ``adapter/server.py`` deliberately: the aggregator
    owns the per-host record shape and shouldn't reach back through the
    adapter to format an IP. If the substrate's IP convention ever
    changes, both copies move together.
    """
    second_octet = (host_id // 256) % 256
    third_octet = host_id % 256
    return f"11.{second_octet}.{third_octet}.1"


def _leaf_port_tx_packets(
    rollup_by_key: dict[tuple[int, int, int], int],
    leaf_id: int,
    leaf_port_if: int,
) -> int:
    """Sum ``tx_packets`` across all 8 queues on ``(leaf_id, leaf_port_if)``.

    A host-leaf link carries traffic destined only for that host, so
    the sum across queues on the leaf's host-facing port is the count
    of packets the leaf transmitted toward this host.
    """
    total = 0
    for q_index in range(8):
        total += rollup_by_key.get((leaf_id, leaf_port_if, q_index), 0)
    return total


def aggregate_host_counters(
    rows: list[HostCounterRow],
    rollup_rows: list[CounterRollupRow] | None,
    topology: "Topology | None",
) -> list[dict[str, Any]]:
    """Roll parsed host-counter rows into per-host records with rate.

    Parameters
    ----------
    rows:
        Parsed ``host_counters.txt`` rows (per-(host, NIC) PhyRxDrop
        counts).
    rollup_rows:
        Parsed ``counters.txt`` rows (per-(switch, port, queue) end-of-sim
        snapshot). Used to compute the denominator for
        ``drops_per_million``. ``None`` and ``[]`` are equivalent.
    topology:
        When provided, every host the topology declares appears in the
        output with ``drop_packets`` zero-filled where unobserved, and
        ``drops_per_million`` computed against the leaf's host-facing
        port. When ``None``, only observed rows surface and
        ``drops_per_million`` is ``None`` (cannot compute the
        host→leaf-port mapping without topology metadata).

    Returns a list of per-host records sorted by ``(host_id, if_index)``.
    Each record carries ``host_id``, ``ip``, ``if_index``,
    ``drop_packets``, and ``drops_per_million`` (``float | None``).
    """
    rollup_rows = rollup_rows or []
    rollup_tx_by_key: dict[tuple[int, int, int], int] = {
        (r.switch_id, r.if_index, r.q_index): r.tx_packets
        for r in rollup_rows
    }

    observed_by_key: dict[tuple[int, int], int] = {
        (r.host_id, r.if_index): r.drop_packets for r in rows
    }

    def _rate(host_id: int, drop_packets: int) -> float | None:
        if topology is None:
            return None
        leaf_id = topology.first_leaf_id() + host_id // topology.hosts_per_leaf
        leaf_port_if = (host_id % topology.hosts_per_leaf) + 1
        denominator = _leaf_port_tx_packets(
            rollup_tx_by_key, leaf_id, leaf_port_if
        )
        if denominator == 0:
            return None
        return drop_packets / denominator * 1_000_000.0

    records: list[dict[str, Any]] = []

    if topology is not None:
        for host_id in range(topology.num_hosts):
            ifs = [k for k in observed_by_key if k[0] == host_id]
            if ifs:
                for (h, ifi) in ifs:
                    drop_packets = observed_by_key[(h, ifi)]
                    records.append({
                        "host_id": h,
                        "ip": _host_id_to_ip(h),
                        "if_index": ifi,
                        "drop_packets": drop_packets,
                        "drops_per_million": _rate(h, drop_packets),
                    })
            else:
                records.append({
                    "host_id": host_id,
                    "ip": _host_id_to_ip(host_id),
                    "if_index": 1,
                    "drop_packets": 0,
                    "drops_per_million": _rate(host_id, 0),
                })
    else:
        for r in rows:
            records.append({
                "host_id": r.host_id,
                "ip": _host_id_to_ip(r.host_id),
                "if_index": r.if_index,
                "drop_packets": r.drop_packets,
                "drops_per_million": None,
            })

    records.sort(key=lambda x: (x["host_id"], x["if_index"]))
    return records
