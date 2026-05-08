"""Parser for the substrate's `ecn.txt` CE-stamp trace.

Format per `powertcp-evaluation-burst.cc:201` in `provandal/ns3-datacenter`
(added in `da095c7`, HarnessIT Stage 5a):

    fprintf(fout, "%lu %u %u %u\\n",
            timestamp_ns, switch_id, if_index, q_index);

Four space-separated columns. One row per packet whose IPv4 ECN field
the switch egress stamped with CE (0x03). An empty `ecn.txt` is a valid
and *diagnostic* state: under `pfc_storm(ecn_misconfigured=True)`, KMIN
is bumped above buffer capacity, so `SwitchMmu::ShouldSendCN` always
returns false and no rows are written. Pairing zero ECN with elevated
PFC is the SRE-legible signature for DCQCN running blind.
"""

from __future__ import annotations

from pathlib import Path

from doppelganger.driver.types import EcnMarkEvent


def parse_ecn_file(path: Path) -> list[EcnMarkEvent]:
    """Parse `ecn.txt` into per-CE-stamp events.

    Lines that do not have the expected four integer columns are skipped
    silently, matching the pfc / fct parsers' tolerance for header /
    stderr-leak lines.
    """
    events: list[EcnMarkEvent] = []
    with path.open() as f:
        for line in f:
            parts = line.split()
            if len(parts) != 4:
                continue
            try:
                events.append(
                    EcnMarkEvent(
                        timestamp_ns=int(parts[0]),
                        switch_id=int(parts[1]),
                        if_index=int(parts[2]),
                        q_index=int(parts[3]),
                    )
                )
            except ValueError:
                continue
    return events
