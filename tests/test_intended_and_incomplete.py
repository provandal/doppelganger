"""Tests for the intended.txt parser + incomplete-flow cross-reference.

Hermetic — no substrate, no Driver. Builds in-memory test fixtures
that mirror the substrate's intended.txt format and the existing
PerFlowRecord shape, then exercises the parser and the cross-reference
logic directly.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from doppelganger.driver.incomplete import compute_incomplete_flows
from doppelganger.driver.parsers.intended import (
    IntendedFlowRecord,
    parse_intended_file,
)
from doppelganger.driver.types import CompletionStatus, PerFlowRecord


# ----------------------------------------------------------- parser tests

def test_parse_empty_file(tmp_path: Path) -> None:
    p = tmp_path / "intended.txt"
    p.write_text("")
    assert parse_intended_file(p) == []


def test_parse_single_row(tmp_path: Path) -> None:
    p = tmp_path / "intended.txt"
    p.write_text("0b000001 0b000201 10001 5000 50000000\n")
    records = parse_intended_file(p)
    assert records == [
        IntendedFlowRecord(
            sip="0b000001",
            dip="0b000201",
            dport=10001,
            intended_size_packets=5000,
            intended_start_ns=50_000_000,
        )
    ]


def test_parse_multiple_rows(tmp_path: Path) -> None:
    p = tmp_path / "intended.txt"
    p.write_text(textwrap.dedent("""\
        0b000001 0b000201 10001 5000 50000000
        0b000101 0b000201 10002 5000 50000000
        0b000201 0b000301 10003 7500 60000000
    """))
    records = parse_intended_file(p)
    assert len(records) == 3
    assert records[2].dport == 10003
    assert records[2].intended_size_packets == 7500
    assert records[2].intended_start_ns == 60_000_000


def test_parse_skips_malformed_lines(tmp_path: Path) -> None:
    """Stray header / log lines in the trace stream should not crash
    the parser. Matches the permissive shape of parsers/fct.py."""
    p = tmp_path / "intended.txt"
    p.write_text(textwrap.dedent("""\
        # this is a comment-shaped header
        0b000001 0b000201 10001 5000 50000000
        too few cols
        0b000101 0b000201 not_a_number 5000 50000000
        0b000301 0b000401 10004 5000 70000000
    """))
    records = parse_intended_file(p)
    assert len(records) == 2
    assert records[0].dport == 10001
    assert records[1].dport == 10004


# ----------------------------------------------- cross-reference tests

def _completed_flow(
    sip: str = "0b000001",
    dip: str = "0b000201",
    sport: int = 49152,
    dport: int = 10001,
    fct_ns: int = 1500,
) -> PerFlowRecord:
    return PerFlowRecord(
        sip=sip,
        dip=dip,
        sport=sport,
        dport=dport,
        status=CompletionStatus.COMPLETED,
        actual_size_bytes=7500000,
        actual_start_ns=50_000_000,
        fct_ns=fct_ns,
        standalone_fct_ns=1000,
    )


def _intended_flow(
    sip: str = "0b000001",
    dip: str = "0b000201",
    dport: int = 10001,
    intended_size_packets: int = 5000,
    intended_start_ns: int = 50_000_000,
) -> IntendedFlowRecord:
    return IntendedFlowRecord(
        sip=sip,
        dip=dip,
        dport=dport,
        intended_size_packets=intended_size_packets,
        intended_start_ns=intended_start_ns,
    )


def test_no_intended_returns_empty():
    """Empty intended set means we have nothing to cross-reference;
    no incomplete records can be derived."""
    assert compute_incomplete_flows([], []) == []
    assert compute_incomplete_flows([], [_completed_flow()]) == []


def test_intended_fully_covered_returns_empty():
    intended = [_intended_flow(dport=10001), _intended_flow(dport=10002)]
    completed = [_completed_flow(dport=10001), _completed_flow(dport=10002)]
    assert compute_incomplete_flows(intended, completed) == []


def test_intended_partially_covered_returns_missing():
    intended = [
        _intended_flow(dport=10001),
        _intended_flow(dport=10002),
        _intended_flow(dport=10003),
    ]
    completed = [_completed_flow(dport=10001)]
    incomplete = compute_incomplete_flows(intended, completed)
    assert len(incomplete) == 2
    dports = {r.dport for r in incomplete}
    assert dports == {10002, 10003}
    # All incomplete records should carry the dropped-without-completion
    # status (this is the diagnostic signal an agent reads).
    for r in incomplete:
        assert r.status is CompletionStatus.DROPPED_WITHOUT_COMPLETION


def test_match_key_excludes_sport():
    """The substrate assigns sport at schedule time, after intended.txt
    is written. Intended records carry no sport; completed records do.
    The cross-reference key must be (sip, dip, dport) only, otherwise
    every intended flow would (incorrectly) appear in the incomplete
    list."""
    intended = [_intended_flow(dport=10001)]
    # completed has the same (sip, dip, dport) triple but a different sport
    completed = [_completed_flow(dport=10001, sport=49152)]
    assert compute_incomplete_flows(intended, completed) == []


def test_incomplete_record_preserves_intended_start_time():
    intended = [_intended_flow(dport=10042, intended_start_ns=123456789)]
    completed: list[PerFlowRecord] = []
    incomplete = compute_incomplete_flows(intended, completed)
    assert len(incomplete) == 1
    assert incomplete[0].intended_start_ns == 123456789


def test_incomplete_record_has_sport_zero_when_never_scheduled():
    """A flow that never scheduled has no sport. Zero is the
    canonical placeholder used in the response — the dport carries
    the agent-readable identifier."""
    intended = [_intended_flow(dport=10001)]
    completed: list[PerFlowRecord] = []
    incomplete = compute_incomplete_flows(intended, completed)
    assert incomplete[0].sport == 0


def test_completed_flow_not_in_intended_is_ignored():
    """If for some reason a completed flow has no matching intended
    row (substrate bug, mid-sim insertion, file truncation), it's not
    surfaced as incomplete. The cross-reference is one-directional:
    intended → check-against-completed."""
    intended = [_intended_flow(dport=10001)]
    completed = [_completed_flow(dport=10001), _completed_flow(dport=99999)]
    assert compute_incomplete_flows(intended, completed) == []
