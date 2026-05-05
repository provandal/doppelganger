"""Parsers for substrate trace file formats.

The substrate emits multiple trace files (`fct.txt`, `mix.tr`, `pfc.txt`,
`qlen.txt`); each has its own parser here. v0.1 ships `fct.txt` only. The
spike (2026-05-02, decision_memo.md Finding) found `mix.tr` / `pfc.txt` /
`qlen.txt` empty in default runs; populating them is Stage 1 backlog
(Doppelgänger v0.2 §10).
"""
