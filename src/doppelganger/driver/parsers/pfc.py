"""Parser for the substrate's `pfc.txt` PFC-frame trace.

Format per `powertcp-evaluation-burst.cc:194` in `provandal/ns3-datacenter`:

    fprintf(fout, "%lu %u %u %u %u\\n",
            timestamp_ns, node_id, node_type, if_index, event_type);

Five space-separated columns. `event_type`: 0=resume_rcvd, 1=pause_rcvd,
2=pause_sent, 3=resume_sent. An empty `pfc.txt` is a valid and
load-bearing state: under DCQCN-engaged incast, no PFC events fire
because ECN marking throttles senders before queues approach PFC
headroom (the `pfc_storm(ecn_misconfigured=False)` baseline).
"""

from __future__ import annotations

from pathlib import Path

from doppelganger.driver.types import PfcEvent


def parse_pfc_file(path: Path) -> list[PfcEvent]:
    """Parse `pfc.txt` into per-frame PFC events.

    Lines that do not have the expected five integer columns are skipped
    silently, matching the fct parser's tolerance for header / stderr-leak
    lines.
    """
    events: list[PfcEvent] = []
    with path.open() as f:
        for line in f:
            parts = line.split()
            if len(parts) != 5:
                continue
            try:
                events.append(
                    PfcEvent(
                        timestamp_ns=int(parts[0]),
                        node_id=int(parts[1]),
                        node_type=int(parts[2]),
                        if_index=int(parts[3]),
                        event_type=int(parts[4]),
                    )
                )
            except ValueError:
                continue
    return events
