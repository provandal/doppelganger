"""Eval-time comparison primitives.

Compares two simulation runs (baseline vs. injected) and surfaces the
findings that ``Doppelgänger_Design_v0.2.md`` §6.3 commits to:

1. **Flow-count delta is a primary failure signature.** Compare counts
   *before* comparing flow times. A run with fewer completed flows than
   baseline has lost flows; aggregate timing comparisons are not
   interpretable until the missing flows are accounted for.
2. **Compare distributions, not means.** Tail behavior (p99, p99.9, max)
   is where pathologies show up; means are systematically pulled around
   by missing-flow censoring.
3. **Annotate incomplete flows.** Once flow.txt compilation lets the
   Driver cross-reference intended-vs-observed flows, the comparison
   primitive will surface incomplete flows with their last-observed
   state. Today the substrate's ``fct.txt`` only emits completed flows;
   incomplete flows show up via the flow-count delta only.

The 2026-05-02 fork-spike's exact data is the test ground truth: with
silent drops at 0.001 on a 0.2s sim, four flows did not complete; aggregate
median FCT on the surviving flows reported the injected run as 17%
*faster* than baseline because the four absent flows were the slowest.
``compare_runs`` must produce a finding that flags this trap.
"""

from doppelganger.eval.comparison import (
    ComparisonResult,
    FctDistribution,
    RunSummary,
    compare_runs,
    summarize_run,
)

__all__ = [
    "ComparisonResult",
    "FctDistribution",
    "RunSummary",
    "compare_runs",
    "summarize_run",
]
