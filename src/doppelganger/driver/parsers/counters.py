"""Parser for the substrate's `counters.txt` per-(port, queue) rollup file.

Format per `powertcp-evaluation-burst.cc` end-of-sim emission in
`provandal/ns3-datacenter` (extended 2026-05-10 for SONiC per-priority
alignment, HarnessIT Stage 5a-realistic SONiC counter expansion):

    fprintf(fout, "%u %u %u %lu %lu %lu %lu %lu %lu %lu\\n",
            switch_id, if_index, q_index,
            rx_packets, rx_bytes, tx_packets, tx_bytes,
            dropped_packets, qlen_peak_bytes, pg_watermark_bytes);

Ten space-separated columns, one row per ``(switch_id, if_index, q_index)``
that saw at least one Enqueue / Dequeue / Drop event OR a non-zero
egress / ingress watermark sample during the run. Per-queue rather than
per-port: matches SONiC's per-priority breakdown
(``show queue counters`` + ``show queue watermark`` +
``show priority-group watermark``). Distinct from pfc.txt / ecn.txt:
those are per-event streams; this is the SNMP-style cumulative snapshot
real switches expose via interface counters. Per-(port, queue) tuples
that saw no activity are absent from the file — the aggregator
zero-fills them from the scenario topology and the q-cardinality of 8.

An empty `counters.txt` is a valid state (no traffic in the simulation).
"""

from __future__ import annotations

from pathlib import Path

from doppelganger.driver.types import CounterRollupRow


def parse_counters_file(path: Path) -> list[CounterRollupRow]:
    """Parse `counters.txt` into per-(switch, port, queue) rollup rows.

    Lines that do not have the expected ten integer columns are skipped
    silently, matching the pfc / ecn / fct parsers' tolerance for header
    or stderr-leak lines.
    """
    rows: list[CounterRollupRow] = []
    with path.open() as f:
        for line in f:
            parts = line.split()
            if len(parts) != 10:
                continue
            try:
                rows.append(
                    CounterRollupRow(
                        switch_id=int(parts[0]),
                        if_index=int(parts[1]),
                        q_index=int(parts[2]),
                        rx_packets=int(parts[3]),
                        rx_bytes=int(parts[4]),
                        tx_packets=int(parts[5]),
                        tx_bytes=int(parts[6]),
                        dropped_packets=int(parts[7]),
                        qlen_peak_bytes=int(parts[8]),
                        pg_watermark_bytes=int(parts[9]),
                    )
                )
            except ValueError:
                continue
    return rows
