"""Tests for eval-time comparison primitives.

The load-bearing test uses the spike's actual ``fct.txt`` data
(``spike/traces/{baseline,injected}/fct.txt``). The 2026-05-02 fork-spike
memo documents that the baseline run produced 255 flows and the injected
run (silent drops at 0.001) produced 251; aggregate median FCT on the
surviving flows reported the injected run as faster than baseline because
the four absent flows were the slowest. ``compare_runs`` must surface
this trap explicitly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from doppelganger.driver.parsers.fct import parse_fct_file
from doppelganger.driver.types import CompletionStatus, PerFlowRecord
from doppelganger.eval import (
    ComparisonResult,
    FctDistribution,
    RunSummary,
    compare_runs,
    summarize_run,
)


SPIKE_BASELINE_FCT = (
    Path(__file__).resolve().parent.parent
    / "spike" / "traces" / "baseline" / "fct.txt"
)
SPIKE_INJECTED_FCT = (
    Path(__file__).resolve().parent.parent
    / "spike" / "traces" / "injected" / "fct.txt"
)


def _completed(fct_ns: int, **overrides) -> PerFlowRecord:
    """Construct a synthetic completed PerFlowRecord with a given FCT."""
    base = dict(
        sip="0a000001",
        dip="0a000002",
        sport=49152,
        dport=50000,
        status=CompletionStatus.COMPLETED,
        fct_ns=fct_ns,
        standalone_fct_ns=fct_ns // 2,
    )
    base.update(overrides)
    return PerFlowRecord(**base)


# ----------------------------------------------------------- summarize

def test_summarize_empty_run():
    s = summarize_run([])
    assert s.total == 0
    assert s.completed == 0
    assert s.fct.n == 0
    assert s.completed_fraction == 0.0


def test_summarize_counts_by_status():
    records = [
        _completed(1000),
        _completed(2000),
        PerFlowRecord(
            sip="0a000001", dip="0a000003", sport=1, dport=2,
            status=CompletionStatus.TIMED_OUT,
        ),
    ]
    s = summarize_run(records)
    assert s.total == 3
    assert s.completed == 2
    assert s.incomplete == 1
    assert s.by_status[CompletionStatus.COMPLETED] == 2
    assert s.by_status[CompletionStatus.TIMED_OUT] == 1


def test_summarize_distribution_percentiles():
    """Distribution percentiles use nearest-rank: p50 of [10,20,30] is 20."""
    records = [_completed(fct) for fct in [10, 20, 30, 40, 50]]
    s = summarize_run(records)
    assert s.fct.n == 5
    assert s.fct.min_ns == 10
    assert s.fct.max_ns == 50
    assert s.fct.p50_ns == 30  # ceil(0.5 * 5) = 3rd entry = 30 in nearest-rank
    assert s.fct.mean_ns == 30.0


# ----------------------------------------------------------- comparison

def test_compare_runs_zero_delta_when_identical():
    records = [_completed(fct) for fct in [100, 200, 300]]
    result = compare_runs(records, records)
    assert result.flow_count_delta == 0
    assert not result.has_count_divergence
    assert any("No divergence" in f for f in result.findings)


def test_compare_runs_surfaces_count_delta_first():
    baseline = [_completed(fct) for fct in [100, 200, 300]]
    injected = [_completed(fct) for fct in [100, 200]]  # one fewer
    result = compare_runs(baseline, injected)
    assert result.flow_count_delta == -1
    assert result.has_count_divergence
    # First finding mentions the count delta explicitly.
    assert "Flow-count delta" in result.findings[0]
    assert "1 fewer" in result.findings[0]


def test_compare_runs_emits_trap_warning_when_naive_compare_misleads():
    """Spike's exact pattern: fewer completed flows AND median appears 'faster'.

    The trap warning fires when count fell AND p50 dropped — the case where
    a naive timing comparison would reach the wrong conclusion.
    """
    # Baseline: five flows at 100, 200, 300, 400, 500 → p50 = 300
    baseline = [_completed(fct) for fct in [100, 200, 300, 400, 500]]
    # Injected: the two slowest dropped → 100, 200, 300 → p50 = 200
    # (naive read: "injected is faster")
    injected = [_completed(fct) for fct in [100, 200, 300]]

    result = compare_runs(baseline, injected)
    assert result.flow_count_delta == -2
    assert result.fct_p50_delta_ns < 0
    assert any("Trap warning" in f for f in result.findings)
    # The trap-warning finding names the censoring artifact explicitly
    trap = next(f for f in result.findings if "Trap warning" in f)
    assert "censoring" in trap


def test_compare_runs_handles_empty_baseline():
    result = compare_runs([], [_completed(100)])
    assert result.flow_count_delta == 1
    # No distribution-line finding when baseline has no flows.
    assert not any("Distribution:" in f for f in result.findings)


# ------------------------------------------- against the spike's actual data

def test_compare_runs_on_spike_data_recovers_the_eval_discipline_finding():
    """The 2026-05-02 spike's exact data is the test ground truth.

    Baseline 255 completed flows; injected 251. compare_runs must:
      - Report flow_count_delta == -4
      - Mark has_count_divergence True
      - Surface the first finding as the count delta
      - Emit the trap warning if the naive median appears 'faster'
    """
    if not SPIKE_BASELINE_FCT.exists() or not SPIKE_INJECTED_FCT.exists():
        pytest.skip(f"spike fct.txt files not present")

    baseline_records = parse_fct_file(SPIKE_BASELINE_FCT)
    injected_records = parse_fct_file(SPIKE_INJECTED_FCT)

    assert len(baseline_records) == 255
    assert len(injected_records) == 251

    result = compare_runs(baseline_records, injected_records)
    assert result.flow_count_delta == -4
    assert result.has_count_divergence
    assert "4 fewer" in result.findings[0]

    # The spike's finding: median FCT appears improved despite four missing
    # slow flows. Confirm the trap warning fires.
    if result.fct_p50_delta_ns < 0:
        assert any("Trap warning" in f for f in result.findings), (
            "Expected trap warning when median fell despite missing flows"
        )


def test_summarize_spike_baseline_distribution():
    """Spike baseline distribution sanity-check: expected counts and ordering."""
    if not SPIKE_BASELINE_FCT.exists():
        pytest.skip("spike baseline fct.txt not present")

    records = parse_fct_file(SPIKE_BASELINE_FCT)
    s = summarize_run(records)
    assert s.completed == 255
    # Distribution monotonicity: min ≤ p50 ≤ p99 ≤ p99.9 ≤ max
    assert s.fct.min_ns <= s.fct.p50_ns
    assert s.fct.p50_ns <= s.fct.p99_ns
    assert s.fct.p99_ns <= s.fct.p999_ns
    assert s.fct.p999_ns <= s.fct.max_ns


# ----------------------------------------------------------- distribution

def test_distribution_empty_factory():
    d = FctDistribution.empty()
    assert d.n == 0
    assert d.max_ns == 0
