"""Comparison primitives for SimulationResult pairs.

The shape is deliberately conservative. ``compare_runs`` does not try to
*explain* a divergence — that's the agent's job. It surfaces the load-bearing
signals (flow counts, distribution percentiles, the missing-flow trap) and
lets downstream consumers reason from there.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import mean
from typing import Iterable, Sequence

from doppelganger.driver.types import CompletionStatus, PerFlowRecord


@dataclass(frozen=True)
class FctDistribution:
    """Percentile summary of flow-completion-time over completed flows.

    All values in nanoseconds. Computed only over flows with
    ``status == COMPLETED`` and ``fct_ns is not None``.
    """

    n: int
    min_ns: int
    p50_ns: int
    p90_ns: int
    p99_ns: int
    p999_ns: int
    max_ns: int
    mean_ns: float

    @classmethod
    def empty(cls) -> "FctDistribution":
        return cls(
            n=0,
            min_ns=0,
            p50_ns=0,
            p90_ns=0,
            p99_ns=0,
            p999_ns=0,
            max_ns=0,
            mean_ns=0.0,
        )


@dataclass(frozen=True)
class RunSummary:
    """Single-run summary: counts by status + FCT distribution over completed flows."""

    total: int
    completed: int
    incomplete: int
    by_status: dict[CompletionStatus, int]
    fct: FctDistribution

    @property
    def completed_fraction(self) -> float:
        return self.completed / self.total if self.total else 0.0


@dataclass
class ComparisonResult:
    """Result of comparing baseline against injected.

    Attributes
    ----------
    baseline:
        :class:`RunSummary` for the baseline run.
    injected:
        :class:`RunSummary` for the injected run.
    flow_count_delta:
        ``injected.completed - baseline.completed``. Negative means flows
        were lost. **This is the primary failure signature** per
        Doppelgänger v0.2 §6.3 — check it before reading any FCT delta.
    fct_p50_delta_ns / fct_p99_delta_ns / fct_p999_delta_ns:
        ``injected - baseline`` deltas at percentiles. Useful for tail-
        behavior comparison; meaningless under non-zero flow_count_delta.
    findings:
        Human-readable findings the comparison surfaces. The first finding
        is always the flow-count delta if non-zero; subsequent findings
        report distribution-level observations. Findings are pre-formatted
        strings, intended for trajectory rendering or eval-report output.
    """

    baseline: RunSummary
    injected: RunSummary
    flow_count_delta: int
    fct_p50_delta_ns: int
    fct_p99_delta_ns: int
    fct_p999_delta_ns: int
    findings: list[str] = field(default_factory=list)

    @property
    def has_count_divergence(self) -> bool:
        """True iff flow counts differ — the case where naive timing comparison misleads."""
        return self.flow_count_delta != 0


# --------------------------------------------------------------- helpers

def _completed_fcts(records: Iterable[PerFlowRecord]) -> list[int]:
    out: list[int] = []
    for r in records:
        if r.status is CompletionStatus.COMPLETED and r.fct_ns is not None:
            out.append(r.fct_ns)
    return out


def _percentile(sorted_values: Sequence[int], pct: float) -> int:
    """Nearest-rank percentile on a pre-sorted ascending sequence.

    pct is in [0, 100]. Empty input returns 0.
    """
    if not sorted_values:
        return 0
    if pct <= 0:
        return sorted_values[0]
    if pct >= 100:
        return sorted_values[-1]
    # Nearest-rank: smallest index k such that k/n >= pct/100, with k in [1, n].
    n = len(sorted_values)
    k = max(1, math.ceil(pct / 100.0 * n))
    return sorted_values[k - 1]


def _distribution(records: Iterable[PerFlowRecord]) -> FctDistribution:
    fcts = sorted(_completed_fcts(records))
    if not fcts:
        return FctDistribution.empty()
    return FctDistribution(
        n=len(fcts),
        min_ns=fcts[0],
        p50_ns=_percentile(fcts, 50),
        p90_ns=_percentile(fcts, 90),
        p99_ns=_percentile(fcts, 99),
        p999_ns=_percentile(fcts, 99.9),
        max_ns=fcts[-1],
        mean_ns=mean(fcts),
    )


def _by_status(records: Iterable[PerFlowRecord]) -> dict[CompletionStatus, int]:
    out: dict[CompletionStatus, int] = {s: 0 for s in CompletionStatus}
    for r in records:
        out[r.status] = out.get(r.status, 0) + 1
    return out


# ------------------------------------------------------------ public API

def summarize_run(records: Sequence[PerFlowRecord]) -> RunSummary:
    """Summarize a single run's flow records: counts by status + FCT distribution."""
    by_status = _by_status(records)
    completed = by_status.get(CompletionStatus.COMPLETED, 0)
    total = len(records)
    return RunSummary(
        total=total,
        completed=completed,
        incomplete=total - completed,
        by_status=by_status,
        fct=_distribution(records),
    )


def compare_runs(
    baseline: Sequence[PerFlowRecord],
    injected: Sequence[PerFlowRecord],
) -> ComparisonResult:
    """Compare two flow-record sequences and return findings.

    Parameters
    ----------
    baseline, injected:
        Per-Flow Records from two scenario runs (use ``SimulationResult.flows``).

    Returns
    -------
    ComparisonResult
        ``flow_count_delta`` is the primary signature; ``findings`` is a
        prose summary suitable for inclusion in eval reports or trajectory
        annotations.
    """
    base_summary = summarize_run(baseline)
    inj_summary = summarize_run(injected)

    flow_count_delta = inj_summary.completed - base_summary.completed
    p50_delta = inj_summary.fct.p50_ns - base_summary.fct.p50_ns
    p99_delta = inj_summary.fct.p99_ns - base_summary.fct.p99_ns
    p999_delta = inj_summary.fct.p999_ns - base_summary.fct.p999_ns

    findings: list[str] = []

    if flow_count_delta != 0:
        sign = "fewer" if flow_count_delta < 0 else "more"
        findings.append(
            f"Flow-count delta: {abs(flow_count_delta)} {sign} flows completed in "
            f"injected ({inj_summary.completed}) than baseline ({base_summary.completed}). "
            f"This is the primary failure signature per Doppelgänger v0.2 §6.3 — "
            f"distribution-level FCT comparisons over completed-only flows are not "
            f"interpretable until the missing flows are accounted for."
        )
        if flow_count_delta < 0 and p50_delta < 0:
            findings.append(
                f"Trap warning: median FCT appears to *improve* in injected "
                f"({_fmt_pct(p50_delta, base_summary.fct.p50_ns)}), but "
                f"{abs(flow_count_delta)} flows did not complete. The improvement "
                f"is most likely censoring artifact, not a real speedup; the "
                f"absent flows are likely the slow ones."
            )

    distribution_diverges = (
        base_summary.fct.n > 0
        and inj_summary.fct.n > 0
        and (p50_delta != 0 or p99_delta != 0 or p999_delta != 0)
    )
    if distribution_diverges:
        findings.append(
            f"Distribution: baseline p50/p99/p99.9 = "
            f"{base_summary.fct.p50_ns}/{base_summary.fct.p99_ns}/{base_summary.fct.p999_ns} ns; "
            f"injected p50/p99/p99.9 = "
            f"{inj_summary.fct.p50_ns}/{inj_summary.fct.p99_ns}/{inj_summary.fct.p999_ns} ns; "
            f"deltas = "
            f"{_fmt_signed(p50_delta)}/{_fmt_signed(p99_delta)}/{_fmt_signed(p999_delta)} ns."
        )

    if not findings:
        findings.append(
            f"No divergence detected. Flow counts equal "
            f"({base_summary.completed}); FCT distributions effectively identical."
        )

    return ComparisonResult(
        baseline=base_summary,
        injected=inj_summary,
        flow_count_delta=flow_count_delta,
        fct_p50_delta_ns=p50_delta,
        fct_p99_delta_ns=p99_delta,
        fct_p999_delta_ns=p999_delta,
        findings=findings,
    )


def _fmt_signed(delta_ns: int) -> str:
    return f"{delta_ns:+d}"


def _fmt_pct(delta: int, ref: int) -> str:
    if ref == 0:
        return "n/a"
    pct = 100.0 * delta / ref
    return f"{pct:+.1f}%"
