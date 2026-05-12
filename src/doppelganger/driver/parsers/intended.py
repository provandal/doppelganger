"""Parser for the substrate's ``intended.txt`` flow-intent trace.

Format per ``powertcp-evaluation-burst.cc`` ``ReadFlowInput`` (substrate
fork 2026-05-12 change):

    fprintf(intended_file, "%08x %08x %lu %lu %lu\\n",
            sip, dip, dport, packet_count, start_ns);

Five space-separated columns. The first two are 8-hex-digit IPv4
addresses (matching ``fct.txt``'s sip/dip column format). One row per
flow the scenario *intended to run* — written at flow-schedule time,
not at flow-completion time, so flows that never completed still
appear here.

Cross-referencing intended.txt against fct.txt is how Doppelgänger
surfaces incomplete flows per v0.2 §4.2. The match key is the
three-tuple ``(sip, dip, dport)`` — sport is intentionally omitted
because the substrate assigns it at schedule time, after intended.txt
is written; incomplete flows that never schedule never get an sport,
so including it in the key would never match.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class IntendedFlowRecord:
    """One row from intended.txt — one flow the scenario intended to run.

    Fields populated at flow-read time in the substrate; specifically
    *not* including sport (assigned later by the transport) or any
    actual measurement fields.
    """

    sip: str                       # 8-hex
    dip: str                       # 8-hex
    dport: int
    intended_size_packets: int
    intended_start_ns: int


def parse_intended_file(path: Path) -> list[IntendedFlowRecord]:
    """Parse ``intended.txt`` into intended-flow records.

    Lines that do not have the expected five columns are skipped
    silently; the substrate occasionally emits informational lines into
    trace streams, and being permissive here matches the existing
    parser pattern (see ``parsers/fct.py``).
    """
    records: list[IntendedFlowRecord] = []
    with path.open() as f:
        for line in f:
            parts = line.split()
            if len(parts) != 5:
                continue
            try:
                records.append(
                    IntendedFlowRecord(
                        sip=parts[0],
                        dip=parts[1],
                        dport=int(parts[2]),
                        intended_size_packets=int(parts[3]),
                        intended_start_ns=int(parts[4]),
                    )
                )
            except ValueError:
                continue
    return records
