"""Parser for the substrate's `fct.txt` flow-completion-time trace.

Format per `powertcp-evaluation-burst.cc:184` in `provandal/ns3-datacenter`:

    fprintf(fout, "%08x %08x %u %u %lu %lu %lu %lu\\n",
            sip, dip, sport, dport, size, start_ns, fct_ns, standalone_fct_ns);

Eight space-separated columns. The first two are 8-hex-digit IPv4 addresses
(no separators). The substrate emits one row per *completed* flow only —
incomplete flows leave no row, which the eval-discipline finding
(Doppelgänger v0.2 §6.3) treats as a load-bearing signal.

This parser produces PerFlowRecord instances with status=COMPLETED. Surfacing
incomplete flows requires cross-referencing scenario metadata, which is the
topology compiler's job (later commit).
"""

from __future__ import annotations

from pathlib import Path

from doppelganger.driver.types import CompletionStatus, PerFlowRecord


def parse_fct_file(path: Path) -> list[PerFlowRecord]:
    """Parse `fct.txt` into completed-flow records.

    Lines that do not have the expected eight columns are skipped silently;
    the substrate occasionally emits header or stderr-leak lines into the
    trace stream, and being permissive here is the right shape (the parser
    is downstream of substrate quirks it does not own).
    """
    records: list[PerFlowRecord] = []
    with path.open() as f:
        for line in f:
            parts = line.split()
            if len(parts) != 8:
                continue
            try:
                records.append(
                    PerFlowRecord(
                        sip=parts[0],
                        dip=parts[1],
                        sport=int(parts[2]),
                        dport=int(parts[3]),
                        status=CompletionStatus.COMPLETED,
                        actual_size_bytes=int(parts[4]),
                        actual_start_ns=int(parts[5]),
                        fct_ns=int(parts[6]),
                        standalone_fct_ns=int(parts[7]),
                    )
                )
            except ValueError:
                # Non-numeric content where digits expected; skip.
                continue
    return records
