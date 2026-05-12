"""Parser for the substrate's ``host_counters.txt`` host-ingress drop trace.

Format per ``powertcp-evaluation-burst.cc`` (substrate fork 2026-05-12,
SHA 1a7b9d0+):

    fprintf(host_file, "%u %u %lu\\n", host_id, if_index, drop_packets);

Three space-separated columns. One row per (host_id, if_index) that
registered any drops via PhyRxDrop during simulation; absent rows
mean zero drops (Doppelgänger zero-fills from topology, same shape
as get_fabric_counters).

WHY: silent drops at ``link_error_rate`` manifest at the link layer —
RateErrorModel is attached as the ``ReceiveErrorModel`` on every
QbbNetDevice, so when a packet is corrupted it fires ``PhyRxDrop`` on
the receiving device. Switch-side admission drops (the existing
``counters.txt`` ``dropped_packets`` field) don't see these because
the RateErrorModel runs *before* the switch's QBB admission check.
``host_counters.txt`` surfaces the missing signal: per-(host, NIC)
PHY-rx drops are the SRE-visible signature of link-layer silent drops.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HostCounterRow:
    """One row from host_counters.txt — per-(host, NIC) PHY-rx drop count."""

    host_id: int
    if_index: int
    drop_packets: int


def parse_host_counters_file(path: Path) -> list[HostCounterRow]:
    """Parse ``host_counters.txt`` into per-host drop-count rows.

    Lines that do not have the expected three columns are skipped
    silently; matches the permissive shape of other Doppelgänger
    parsers (the parser is downstream of substrate quirks it does not
    own).
    """
    rows: list[HostCounterRow] = []
    with path.open() as f:
        for line in f:
            parts = line.split()
            if len(parts) != 3:
                continue
            try:
                rows.append(
                    HostCounterRow(
                        host_id=int(parts[0]),
                        if_index=int(parts[1]),
                        drop_packets=int(parts[2]),
                    )
                )
            except ValueError:
                continue
    return rows
