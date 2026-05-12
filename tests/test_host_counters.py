"""Tests for the host_counters.txt parser.

Hermetic — builds synthetic files in tmp_path and exercises the
parser directly. The MCP-tool integration (get_host_counters response
shape, zero-fill from topology) is covered by the gated tests in
test_adapter.py which run against the real substrate.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from doppelganger.driver.parsers.host_counters import (
    HostCounterRow,
    parse_host_counters_file,
)


def test_parse_empty_file(tmp_path: Path) -> None:
    p = tmp_path / "host_counters.txt"
    p.write_text("")
    assert parse_host_counters_file(p) == []


def test_parse_single_row(tmp_path: Path) -> None:
    p = tmp_path / "host_counters.txt"
    p.write_text("0 1 42\n")
    rows = parse_host_counters_file(p)
    assert rows == [HostCounterRow(host_id=0, if_index=1, drop_packets=42)]


def test_parse_multiple_rows(tmp_path: Path) -> None:
    p = tmp_path / "host_counters.txt"
    p.write_text(textwrap.dedent("""\
        0 1 13
        7 1 1
        265 1 1
    """))
    rows = parse_host_counters_file(p)
    assert len(rows) == 3
    assert rows[0].host_id == 0
    assert rows[0].drop_packets == 13
    assert rows[2].host_id == 265


def test_parse_skips_malformed_lines(tmp_path: Path) -> None:
    """Stray lines should not crash the parser. Matches the
    permissive shape of other parsers in this package."""
    p = tmp_path / "host_counters.txt"
    p.write_text(textwrap.dedent("""\
        # comment-shaped header
        0 1 13
        only two cols
        7 not_a_number 5
        265 1 1
    """))
    rows = parse_host_counters_file(p)
    assert len(rows) == 2
    assert rows[0].host_id == 0
    assert rows[1].host_id == 265
