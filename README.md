# Doppelgänger

NS-3-based fabric simulation Substrate Adapter for [HarnessIT](https://github.com/provandal/harnessit).

Doppelgänger wraps the NS-3 simulator (with the [`inet-tub/ns3-datacenter`](https://github.com/inet-tub/ns3-datacenter) RDMA additions) and exposes it through MCP tools that match HarnessIT's substrate contract. Internally, Doppelgänger has two layers:

- A **Driver** — pure-Python wrapper around NS-3. Spawns the simulator as a subprocess, writes scenario configs, parses trace outputs. No MCP, no NS-3 linkage.
- An **Adapter** — MCP server that imports the Driver and exposes its methods as MCP tools.

This split keeps the Driver reusable (e.g., by ProtoViz) and keeps an arms-length boundary between Apache-2.0 Doppelgänger code and GPL-2.0 NS-3.

## Status

Stage 1 implementation as of 2026-05-05. The 2026-05-02 fork spike (see [`spike/decision_memo.md`](spike/decision_memo.md)) committed to `inet-tub/ns3-datacenter` (NS-3.39) as the upstream substrate; we maintain a pinned fork at [`provandal/ns3-datacenter`](https://github.com/provandal/ns3-datacenter). The Dockerfile currently pins SHA `9881be120a7863e2072f641f89ffa6532c585829`, which includes the upstream HEAD validated by the spike (`4dd55d8…`), the top-level GPL-2.0 LICENSE clarification (`6aeea1c`), the 2026-05-05 trace-output gap fixes for `pfc.txt` / `mix.tr` / `qlen.txt` (`bff3b9c`), and the dropped `argv[2]` filename mutation on `TRACE_OUTPUT_FILE` (`9881be1`).

See [`docs/Doppelganger_Design_v0.2.md`](docs/Doppelganger_Design_v0.2.md) for the canonical design document. The legacy `.docx` and `.txt` extraction files in `docs/` are historical artifacts at v0.1.

## Layout

- `docs/` — design documentation
- `spike/` — fork-spike artifacts (Dockerfile, parsers, decision memo, traces from the validated runs)

## License

Apache License 2.0 for source code in this repository. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE) for the full story, including the GPL-2.0 inheritance through built Docker images.
