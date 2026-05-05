"""Tests for the TrafficPattern → flow.txt compiler."""

from __future__ import annotations

import pytest

from doppelganger.scenarios import (
    OPEN_LOOP_PACKETS,
    Flow,
    TrafficCompileError,
    TrafficPattern,
    compile_traffic,
)


def _parse(text: str) -> dict:
    """Parse a flow.txt into a dict for assertions."""
    lines = [line for line in text.splitlines() if line.strip()]
    n = int(lines[0])
    flows = []
    for line in lines[1:]:
        parts = line.split()
        flows.append({
            "src": int(parts[0]),
            "dst": int(parts[1]),
            "pg": int(parts[2]),
            "dport": int(parts[3]),
            "packet_count": int(parts[4]),
            "start_time": float(parts[5]),
        })
    return {"flow_num": n, "flows": flows}


def test_compile_single_flow(tmp_path):
    pattern = TrafficPattern(flows=(
        Flow(src=0, dst=16, priority_group=3, dst_port=11000,
             packet_count=2800, start_time_seconds=0.13),
    ))
    out = compile_traffic(pattern, tmp_path / "flow.txt")
    parsed = _parse(out.read_text(encoding="utf-8"))

    assert parsed["flow_num"] == 1
    assert parsed["flows"][0] == {
        "src": 0, "dst": 16, "pg": 3, "dport": 11000,
        "packet_count": 2800, "start_time": 0.13,
    }


def test_compile_open_loop_packet_count_emits_substrate_convention(tmp_path):
    """OPEN_LOOP_PACKETS = 1e12 matches the substrate's bundled flow file."""
    assert OPEN_LOOP_PACKETS == 10**12
    pattern = TrafficPattern(flows=(
        Flow(src=0, dst=1, priority_group=3, dst_port=10000,
             packet_count=OPEN_LOOP_PACKETS, start_time_seconds=0.1),
    ))
    out = compile_traffic(pattern, tmp_path / "flow.txt")
    parsed = _parse(out.read_text(encoding="utf-8"))

    assert parsed["flows"][0]["packet_count"] == 10**12


def test_compile_preserves_flow_order(tmp_path):
    flows = tuple(
        Flow(src=i, dst=i + 100, priority_group=3, dst_port=20000 + i,
             packet_count=1000, start_time_seconds=0.1)
        for i in range(5)
    )
    pattern = TrafficPattern(flows=flows)
    out = compile_traffic(pattern, tmp_path / "flow.txt")
    parsed = _parse(out.read_text(encoding="utf-8"))

    assert parsed["flow_num"] == 5
    for i, flow in enumerate(parsed["flows"]):
        assert flow["src"] == i


def test_empty_pattern_rejected(tmp_path):
    pattern = TrafficPattern(flows=())
    with pytest.raises(TrafficCompileError, match="at least one flow"):
        compile_traffic(pattern, tmp_path / "flow.txt")


def test_self_loop_flow_rejected(tmp_path):
    pattern = TrafficPattern(flows=(
        Flow(src=5, dst=5, priority_group=3, dst_port=10000,
             packet_count=1000, start_time_seconds=0.1),
    ))
    with pytest.raises(TrafficCompileError, match="src==dst"):
        compile_traffic(pattern, tmp_path / "flow.txt")


def test_zero_packet_count_rejected(tmp_path):
    pattern = TrafficPattern(flows=(
        Flow(src=0, dst=1, priority_group=3, dst_port=10000,
             packet_count=0, start_time_seconds=0.1),
    ))
    with pytest.raises(TrafficCompileError, match="packet_count"):
        compile_traffic(pattern, tmp_path / "flow.txt")


def test_negative_start_time_rejected(tmp_path):
    pattern = TrafficPattern(flows=(
        Flow(src=0, dst=1, priority_group=3, dst_port=10000,
             packet_count=1000, start_time_seconds=-0.5),
    ))
    with pytest.raises(TrafficCompileError, match="start_time_seconds"):
        compile_traffic(pattern, tmp_path / "flow.txt")
