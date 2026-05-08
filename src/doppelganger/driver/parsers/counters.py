"""Parser for the substrate's `counters.txt` per-port rollup file.

Format per `powertcp-evaluation-burst.cc` end-of-sim emission in
`provandal/ns3-datacenter` (added in `640ea8d`, HarnessIT
Stage 5a-realistic):

    fprintf(fout, "%u %u %lu %lu %lu %lu %lu %lu\\n",
            switch_id, if_index,
            rx_packets, rx_bytes, tx_packets, tx_bytes,
            drops, qlen_peak_bytes);

Eight space-separated columns, one row per ``(switch_id, if_index)`` that
saw at least one Enqueue / Dequeue / Drop event during the run. Distinct
from pfc.txt / ecn.txt: those are per-event streams; this is the
SNMP-style cumulative snapshot real switches expose via interface
counters. Ports that saw no activity are absent from the file — the
aggregator zero-fills them from the scenario topology.

An empty `counters.txt` is a valid state (no traffic in the simulation).
"""

from __future__ import annotations

from pathlib import Path

from doppelganger.driver.types import CounterRollupRow


def parse_counters_file(path: Path) -> list[CounterRollupRow]:
    """Parse `counters.txt` into per-port counter rollup rows.

    Lines that do not have the expected eight integer columns are skipped
    silently, matching the pfc / ecn / fct parsers' tolerance for header
    or stderr-leak lines.
    """
    rows: list[CounterRollupRow] = []
    with path.open() as f:
        for line in f:
            parts = line.split()
            if len(parts) != 8:
                continue
            try:
                rows.append(
                    CounterRollupRow(
                        switch_id=int(parts[0]),
                        if_index=int(parts[1]),
                        rx_packets=int(parts[2]),
                        rx_bytes=int(parts[3]),
                        tx_packets=int(parts[4]),
                        tx_bytes=int(parts[5]),
                        drops=int(parts[6]),
                        qlen_peak_bytes=int(parts[7]),
                    )
                )
            except ValueError:
                continue
    return rows
