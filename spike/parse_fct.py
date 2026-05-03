"""
Parse PowerTCP fct.txt output (inet-tub/ns3-datacenter, NS-3.39).

Format per powertcp-evaluation-burst.cc:184 —
    fprintf(fout, "%08x %08x %u %u %lu %lu %lu %lu\n",
            sip, dip, sport, dport, size, start_ns, fct_ns, standalone_fct_ns)

Run from the spike directory:
    python parse_fct.py traces/baseline/fct.txt
    python parse_fct.py traces/injected/fct.txt
"""

import sys
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median


@dataclass
class FlowRecord:
    sip: str        # 8-hex source IP
    dip: str        # 8-hex destination IP
    sport: int
    dport: int
    size_bytes: int
    start_ns: int
    fct_ns: int           # measured flow completion time
    standalone_fct_ns: int  # ideal FCT (no congestion)

    @property
    def slowdown(self) -> float:
        return self.fct_ns / self.standalone_fct_ns if self.standalone_fct_ns else float("inf")


def parse(path: Path) -> list[FlowRecord]:
    records = []
    with path.open() as f:
        for line in f:
            parts = line.split()
            if len(parts) != 8:
                continue
            records.append(FlowRecord(
                sip=parts[0],
                dip=parts[1],
                sport=int(parts[2]),
                dport=int(parts[3]),
                size_bytes=int(parts[4]),
                start_ns=int(parts[5]),
                fct_ns=int(parts[6]),
                standalone_fct_ns=int(parts[7]),
            ))
    return records


def summary(label: str, recs: list[FlowRecord]) -> None:
    if not recs:
        print(f"[{label}] no records")
        return
    fcts = [r.fct_ns for r in recs]
    slows = [r.slowdown for r in recs]
    print(f"[{label}] flows={len(recs)} "
          f"fct_ns(min/med/mean/max)="
          f"{min(fcts)}/{int(median(fcts))}/{int(mean(fcts))}/{max(fcts)} "
          f"slowdown(med/mean/max)="
          f"{median(slows):.2f}/{mean(slows):.2f}/{max(slows):.2f}")


def main():
    paths = [Path(p) for p in sys.argv[1:]]
    if not paths:
        baseline = Path("traces/baseline/fct.txt")
        injected = Path("traces/injected/fct.txt")
        paths = [p for p in (baseline, injected) if p.exists()]
    if not paths:
        print(__doc__)
        sys.exit(1)

    parsed = [(p, parse(p)) for p in paths]
    for p, recs in parsed:
        summary(p.parent.name or p.name, recs)
        if recs:
            r = recs[0]
            print(f"  sample: {r.sip} -> {r.dip} sport={r.sport} dport={r.dport} "
                  f"size={r.size_bytes}B start={r.start_ns}ns fct={r.fct_ns}ns "
                  f"standalone={r.standalone_fct_ns}ns slowdown={r.slowdown:.2f}")

    if len(parsed) == 2:
        a_label, a_recs = parsed[0][0].parent.name, parsed[0][1]
        b_label, b_recs = parsed[1][0].parent.name, parsed[1][1]
        a_med = median(r.fct_ns for r in a_recs)
        b_med = median(r.fct_ns for r in b_recs)
        delta_pct = 100 * (b_med - a_med) / a_med if a_med else float("inf")
        print(f"\nMedian FCT: {a_label}={int(a_med)}ns vs {b_label}={int(b_med)}ns "
              f"({delta_pct:+.1f}%)")
        print(f"Flow count: {a_label}={len(a_recs)} vs {b_label}={len(b_recs)} "
              f"(diff={len(b_recs)-len(a_recs)})")


if __name__ == "__main__":
    main()
