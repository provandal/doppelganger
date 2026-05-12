"""Cross-reference intended.txt against fct.txt to surface incomplete flows.

Doppelgänger v0.2 §4.2 specifies that *every flow the scenario intended
to run* should produce a ``PerFlowRecord`` — completed or not. The
substrate's ``fct.txt`` only emits rows for completed flows; the
substrate's ``intended.txt`` (added 2026-05-12 to the fork) emits one
row per flow at schedule-read time. The set-difference is incomplete.

This module is the cheap, durable closure of the
silent-drops-tool-coverage gap surfaced by the 2026-05-12 step-2a
re-run: at 0.001 per-link error rate the substrate produced 252/252
completed flows with zero incomplete records visible. With this
cross-reference in place, the count of incomplete flows is the
diagnostic signal an agent can see directly — both as a non-zero
``summary.incomplete`` and as ``PerFlowRecord(status=
DROPPED_WITHOUT_COMPLETION)`` entries in the ``flows`` array.

Match key is ``(sip, dip, dport)`` — sport is excluded because the
substrate assigns it at flow-schedule time (after intended.txt is
written); incomplete flows that never schedule never get an sport, so
including it would never match. The scenarios this targets do not
multiplex flows of the same ``(sip, dip, dport)`` triple at different
sports, so the key is unambiguous.
"""

from __future__ import annotations

from doppelganger.driver.parsers.intended import IntendedFlowRecord
from doppelganger.driver.types import CompletionStatus, PerFlowRecord


def compute_incomplete_flows(
    intended: list[IntendedFlowRecord],
    completed: list[PerFlowRecord],
) -> list[PerFlowRecord]:
    """Return PerFlowRecord(status=DROPPED_WITHOUT_COMPLETION) for every
    intended flow not present in the completed set.

    Parameters
    ----------
    intended:
        IntendedFlowRecord list (parsed from intended.txt).
    completed:
        PerFlowRecord list with status=COMPLETED (parsed from fct.txt).

    Returns
    -------
    list[PerFlowRecord]
        One record per intended flow whose ``(sip, dip, dport)`` does
        not appear in the completed set. Each record carries the
        intended 5-tuple (sport=0 because unknown), the intended start
        time, and ``status=DROPPED_WITHOUT_COMPLETION``. Measurement
        fields (fct_ns, standalone_fct_ns, actual_*) are left None
        because the flow never produced them.
    """
    completed_keys = {(r.sip, r.dip, r.dport) for r in completed}
    incomplete: list[PerFlowRecord] = []
    for intent in intended:
        key = (intent.sip, intent.dip, intent.dport)
        if key in completed_keys:
            continue
        incomplete.append(
            PerFlowRecord(
                sip=intent.sip,
                dip=intent.dip,
                sport=0,
                dport=intent.dport,
                status=CompletionStatus.DROPPED_WITHOUT_COMPLETION,
                intended_start_ns=intent.intended_start_ns,
            )
        )
    return incomplete


__all__ = ["compute_incomplete_flows"]
