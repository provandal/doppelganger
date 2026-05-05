# Doppelgänger — Design Document

**The NS-3 Substrate Adapter for HarnessIT**

Draft v0.2 · provandal.dev

**Companion to:** HarnessIT Architecture Overview v0.5

---

## 1. Purpose and Scope

Doppelgänger is the simulated fabric that HarnessIT investigates. Its name is deliberate: a doppelgänger is a non-biological double, a counterpart of a living thing, which is exactly what Doppelgänger is to a real RoCE fabric.

This document specifies what Doppelgänger is, what it does, what it explicitly does not do, and how it is built. It is written for the engineer who will implement Doppelgänger and the readers of the HarnessIT series who want to understand how the simulator works underneath.

### 1.1 What Doppelgänger Is

Doppelgänger is the NS-3 Substrate Adapter for HarnessIT (see §1.4). It is a Python project that wraps an NS-3-based RDMA simulator — specifically [`provandal/ns3-datacenter`](https://github.com/provandal/ns3-datacenter), a pinned fork of [`inet-tub/ns3-datacenter`](https://github.com/inet-tub/ns3-datacenter) — and exposes simulation telemetry, configuration, and event data to HarnessIT through MCP tools that match the substrate contract. Doppelgänger defines fabric topologies, sets up failure injection conditions, runs simulations to completion, and surfaces the results.

Doppelgänger inherits the entire NS-3 simulation engine and the RDMA-specific work that the HPCC, DCQCN, PFC, and ECN community has invested in over the last several years. It does not reimplement any of this. The simulator is the work of others; Doppelgänger is the scenario and exposure layer that makes the simulator useful for agent investigation. The substrate-fork choice (inet-tub/ns3-datacenter on NS-3.39) and the rationale for it are covered in §9.2.

### 1.2 What Doppelgänger Is Not

**Not a packet-level simulator.** That work was done by the NS-3 community. We use it; we do not rebuild it.

**Not a switch operating system emulator.** That is what Cumulus VX, SONiC virtual machines, and NVIDIA AIR provide. Doppelgänger does not run vendor NOS images.

**Not a behavioral simulator.** Behavioral simulators specify counter increments and state transitions by hand. They scale poorly, miss interactions, and require constant maintenance as scenarios grow. Doppelgänger gets its behavior from the actual NS-3 simulation rather than from hand-specified rules. This is a deliberate choice grounded in painful prior experience.

**Not a real-time fabric.** Doppelgänger runs simulations to completion and exposes the recorded artifacts. The agent investigates against post-simulation state, not live state. When real-time investigation matters, that is what the NVIDIA AIR adapter is for.

**Not a digital twin in the formal sense.** There are no mathematical proofs about forwarding behavior. Doppelgänger is a high-fidelity simulation, but its outputs should be treated as simulated artifacts, not as verified facts about a real fabric.

### 1.3 The Core Insight

The reason Doppelgänger exists in this form, rather than as a behavioral simulator, comes from a specific failure mode. Hand-specified behavioral simulators do not scale. Every flow, every counter, every interaction has to be enumerated. The specification surface grows faster than any maintainer can chase, and the simulator inevitably misses things. The first scenario is easy; the tenth scenario is painful; the hundredth scenario is impossible.

Mechanism-based simulators do not have this problem. NS-3 models the network stack once. Counters and flows emerge from the simulation rather than being specified. Adding a new flow does not require updating any counter logic. Adding a new failure mode produces downstream effects automatically because they propagate through the actual simulated mechanism. The combinatorial explosion goes away.

Mechanism scales. Specification does not.

Doppelgänger inherits this property by inheriting NS-3. The cost is a steeper setup story and a less Python-native experience. The benefit is that we never run out of fidelity at the place where we need it.

### 1.4 Doppelgänger as a Substrate Adapter

Doppelgänger is the first instance of a named architectural role in HarnessIT: the *Substrate Adapter*. A Substrate Adapter wraps an underlying network simulator, emulator, or fabric, and exposes it through HarnessIT's MCP tool contract. Doppelgänger wraps NS-3; the AIR Adapter (§8) wraps NVIDIA AIR; future adapters — a SONiC Adapter, a real-fabric adapter — would wrap their own substrates. HarnessIT does not know which Substrate Adapter is providing data; that is the contract's purpose.

Internally, every Substrate Adapter has the same two-layer structure:

- A *Driver* that talks to the underlying substrate in its native idiom. For Doppelgänger, the Driver spawns the NS-3 binary as a subprocess and parses its text outputs. For the AIR Adapter, the Driver would talk to AIR's REST and SSH interfaces. For a SONiC Adapter, the Driver would talk to ConfigDB, gNMI, and SONiC CLI.
- An *Adapter shell* — an MCP server that imports the Driver and exposes its methods as MCP tools, applies the response envelope (§2.3), and handles subscription primitives (§2.4).

The split exists for testability (the Driver is reusable from a REPL or unit test without MCP scaffolding), separation of concerns (substrate semantics versus protocol/transport), and reusability of the Driver by sibling consumers like ProtoViz (see §7) that read substrate outputs directly without going through MCP. The architecture document (HarnessIT Architecture v0.5) covers the Substrate Adapter role at length; this section names Doppelgänger's place in it.

---

## 2. The Interface Contract

This section is the most important part of the document. The interface contract is what consumers see; it is the durable spec; everything else in this document can change without breaking it. Get this right and swapping NS-3 for AIR later is a matter of writing an adapter. Get it wrong and the AIR transition becomes a major rewrite.

### 2.1 Consumers

Doppelgänger has two consumer classes:

- **HarnessIT (primary).** The agentic harness consumes Doppelgänger as its sensing layer. HarnessIT does not know whether it is talking to Doppelgänger, an AIR adapter, or eventually a real fabric. It consumes the interface defined here.
- **ProtoViz (secondary).** The protocol visualization tool consumes the same simulation artifacts to render protocol-level interactions. ProtoViz is a sibling consumer, not a downstream one — it reads Doppelgänger's outputs directly rather than going through HarnessIT.

### 2.2 The Tool Surface

Doppelgänger exposes its state through MCP tools. Tool families and their semantics:

| Family | Purpose |
|---|---|
| **Topology** | Query the fabric graph: nodes (switches, hosts), ports, links, neighbor relationships. Examples: `get_topology`, `get_node`, `get_neighbors`, `trace_path`. |
| **Counters and metrics** | Query interface counters, queue depths, PFC pause counters, ECN marks, drop counters. Time-indexed; consumers can request a point-in-time snapshot or a time-range series. Examples: `get_counter`, `get_metric_series`. |
| **Configuration** | Query device configuration as the simulation initialized it. Examples: `get_config`, `get_config_history` (for scenarios that include config changes mid-run). |
| **Logs and events** | Query log entries and discrete events emitted by the simulation: PFC pause events, ECN mark events, queue overflow events, link state changes. Examples: `tail_log`, `search_events`, `get_event`. |
| **Flows and traffic** | Query the flows that ran during the simulation: source, destination, byte counts, completion times, congestion experience. Examples: `list_flows`, `get_flow`, `get_flow_packets`. |
| **Scenario metadata** | Query metadata about the simulation itself: scenario name, parameters, what failures were injected, simulation duration, completion status. Examples: `get_scenario_info`, `get_failure_injections`. |

### 2.3 The Response Envelope

Every Doppelgänger tool returns its data wrapped in the standard envelope HarnessIT expects:

```
{
  data: <tool-specific payload>,
  observed_at: <simulation timestamp>,
  source: <which simulation artifact this came from>,
  confidence: high  (always; this is simulated data),
  staleness_class: <fresh|recent|stale>
}
```

Two notes on the envelope semantics for Doppelgänger specifically:

- `observed_at` is simulation time, not wall clock. If the simulation modeled 60 seconds of fabric activity, `observed_at` values fall within that 60-second window. The agent reasons about temporal relationships within the simulation, not within the wall clock during which the simulation ran.
- `confidence` is always `high`. Doppelgänger's data is the simulation's ground truth. There is no measurement uncertainty, no source disagreement, no telemetry gaps. This is one of the ways simulated investigation differs from real investigation, and the series will discuss it explicitly.

### 2.4 Subscription Semantics

Doppelgänger supports the same subscription primitives HarnessIT expects from any sensing layer (state-change, threshold, stream, anomaly), with one important caveat: subscriptions over Doppelgänger replay simulation events at a configurable rate. The agent can request real-time replay (matching simulation time to wall clock), accelerated replay (faster than simulation time, useful for fast-forwarding to interesting moments), or paused replay (subscriber controls when the next event arrives, useful for stepwise investigation).

This is one of Doppelgänger's pedagogical advantages. A live fabric only flows at one speed. A simulated fabric can be paused, rewound, and replayed at the rate the investigator needs. When eval scenarios run in real-time replay mode (no rewind, advance gated on wall-clock time), the agent's exposure to the simulation has the *feel* of a live investigation — exercising the live-troubleshooting disciplines (counter clearing, propagation watching, time-pressure prioritization) that pure post-mortem analysis does not.

---

## 3. The Fabric Model

Doppelgänger v0.2 supports a single fabric topology class: leaf-spine RoCE fabrics in the small-to-medium size range relevant for AI training scenarios.

### 3.1 Topology Class

Leaf-spine fabrics with the following characteristics:

- Configurable number of leaf switches and spine switches.
- Configurable hosts per leaf (representing GPU servers).
- Configurable link speeds at leaf-host and leaf-spine boundaries.
- Configurable buffer sizes per switch (modeled on real ASIC characteristics).
- Configurable PFC, ECN, and DCQCN parameters per switch and per priority.

v0.2 ships with three reference topologies:

- **Small.** 4 leaves, 2 spines, 8 hosts per leaf. 32 hosts total. Useful for fast iteration during agent development and for small-scale eval scenarios.
- **Medium.** 8 leaves, 4 spines, 16 hosts per leaf. 128 hosts total. Useful for scenarios where fabric-wide behavior matters.
- **Large.** 16 leaves, 8 spines, 32 hosts per leaf. 512 hosts total. Useful for scenarios that need to exercise spine-level congestion or multi-pod patterns. Slower to simulate.

### 3.2 What Fabric Behavior Doppelgänger Models

By inheriting NS-3 with the inet-tub RDMA additions, Doppelgänger models:

- Layer 2 forwarding with realistic switch buffering.
- PFC pause frame propagation across priority lanes.
- ECN marking based on configurable WRED/RED thresholds.
- DCQCN congestion control on the RDMA NICs.
- RDMA flow behavior including QP state, retransmission, and completion semantics.
- Multi-path forwarding with ECMP hashing.
- Realistic congestion propagation patterns including microbursts, head-of-line blocking, and PFC storm conditions.

### 3.3 What Doppelgänger Does Not Model

**Layer 3 routing protocol behavior** (BGP, OSPF). Topologies are configured statically; route convergence is not simulated.

**Switch operating system behavior**, control plane CPU effects, or management plane interactions.

**Hardware faults at the optical or transceiver level.** Link state changes can be scripted, but the underlying physical layer is not modeled.

**Multi-tenant isolation, VLAN behavior, or VXLAN overlays.** The fabric is single-tenant and underlay-only.

**AI-training-specific behaviors that v0.2 does not model.** The architecture document positions HarnessIT around AI fabric operations. Doppelgänger v0.2's actual scope is narrower than that framing implies: it covers fabric-layer congestion pathologies (PFC, ECN, DCQCN, queue dynamics, microbursts) on AI-style topologies. It does not model:

- **NCCL collective patterns.** Ring, tree, and double-binary-tree collectives produce specific traffic shapes (incast bursts at allreduce boundaries, persistent pairwise traffic during alltoall) that scenario authors must hand-author as flow patterns rather than emerging from a collective orchestrator.
- **GPUDirect RDMA semantics.** GPU memory pinning, NIC-GPU peer-to-peer, and timing artifacts where the GPU is the producer or consumer are not modeled. Pathologies that look like NIC stalls but originate at the GPU are invisible.
- **SEND vs WRITE vs READ verb differences.** The flow model is essentially WRITE — push bytes, signaled completion. SEND with receive-queue starvation, READ with response RTT, atomics, and the RDMA error semantics that distinguish them are not differentiated.
- **Out-of-order delivery and selective acknowledgement.** The RoCE model assumes in-order delivery with go-back-N retransmit. Modern ConnectX-6/7 NICs support out-of-order delivery with adaptive retransmit; that behavior is not modeled.
- **Multi-rail / dual-port topologies.** Real AI training fabrics often use multi-rail (each GPU has its own NIC, different rails are independent fabrics, traffic is striped). Doppelgänger v0.2 topologies are single-rail.
- **Receive-side scaling and NIC-internal queue pathologies.** Multi-QP RSS, completion queue overflow, and CQ moderation behavior are abstracted away.
- **Adaptive routing.** Spectrum-X and Tomahawk-5 adaptive routing differ materially from static ECMP; v0.2 models static ECMP only.
- **Application-layer congestion control that bypasses NIC CC.** Meta (SIGCOMM 2024) has reported disabling DCQCN for LLM training because it does not work well for low-flow-entropy / high-burstiness workloads, and instead implements traffic scheduling at the application layer; DeepSeek has done the same. Doppelgänger v0.2 covers DCQCN/PFC/ECN-based congestion control, which is still the dominant deployed pattern outside hyperscale LLM training. App-layer-scheduled traffic patterns are not modeled.

These limitations are not weaknesses; they are scope decisions. **Doppelgänger v0.2 is "AI fabric *congestion* troubleshooting," not the full AI-fabric operational surface.** Later versions can extend in any of these directions if specific scenarios require it. The interface contract in §2 is designed so extensions add new tool families and event types without breaking existing consumers.

---

## 4. What Doppelgänger Surfaces

This section enumerates what observers (HarnessIT, ProtoViz, eventually direct human inspection) see when they query a completed Doppelgänger simulation. It is not the implementation; it is the visible surface.

### 4.1 Per-Port Counters

For every port on every switch and host, Doppelgänger exposes the standard counter set as a time series across the simulation duration:

- Bytes and packets transmitted and received.
- Drops by class (buffer exhaustion, policer, error).
- PFC pause frames transmitted and received, per priority.
- ECN marks (CE bits set) transmitted and received.
- Queue depths per priority, sampled at configurable granularity.
- Link state (up, down, with timestamps for transitions).

### 4.2 Per-Flow Records

For every RDMA flow that the scenario *intended to run* — whether or not it completed successfully — Doppelgänger exposes:

- **Identifying fields.** Source and destination identifiers, scenario-assigned flow ID, intended start time, intended byte count.
- **Outcome.** Completion status (`completed`, `failed`, `timed_out`, `dropped_without_completion`). For completed flows: actual completion timestamp, actual flow completion time, actual bytes transferred.
- **Path taken through the fabric** (hop sequence) for completed flows; partial path for incomplete flows where reachable.
- **Congestion experience.** ECN marks received, RTT samples, retransmission count, congestion window evolution. For incomplete flows: last-observed state.

The "every flow that the scenario intended to run" framing is load-bearing for eval discipline (see §6.3). Flows that fail to complete must produce a record, not silence — otherwise aggregate flow-time statistics on the surviving flows are systematically misleading.

### 4.3 Discrete Events

Beyond counters and flows, Doppelgänger surfaces discrete events that an investigator would care about:

- PFC pause events (start, duration, source, propagation chain).
- ECN mark surge events (when marks exceed a threshold for a duration).
- Queue overflow events.
- Link state change events.
- Configuration change events (for scenarios that include mid-run config changes).
- Injected failure events (for traceability — every injected failure produces an event so the agent can correlate symptoms with the underlying cause when investigating).

### 4.4 Configuration Snapshots

For each device, Doppelgänger exposes the configuration as it was at the start of the simulation and at any point where it changed during the simulation. The configuration model includes per-port settings, buffer allocations, PFC and ECN configurations, and routing tables.

---

## 5. Failure Injection

This is where Doppelgänger genuinely earns its keep over alternatives. Real fabrics do not let you summon a microburst on demand. Doppelgänger does. The failure injection model is what makes the simulator useful for teaching and for eval work specifically.

### 5.1 Injection Philosophy

Doppelgänger's failure injection happens at scenario authorship time, not at investigation time. A scenario author defines the conditions that produce a failure, and the simulation produces the failure naturally as those conditions are met. This is honest to how failures actually work — they emerge from conditions — and it gives downstream effects that are mechanistically correct.

Concretely, you do not call `inject_microburst()` and have a microburst appear. You configure incast traffic at a specific moment, with specific flow timing and queue settings, and the simulation produces the microburst. This is more work than scripted-magical injection, but it produces more realistic propagation effects, and the agent investigating the result has to reason about the actual mechanism rather than the magical injection.

### 5.2 Failure Classes Supported in v0.2

| Failure class | Injection mechanism |
|---|---|
| **Microburst** | Configure incast traffic patterns with synchronized start times. The simulation produces the burst, with associated buffer pressure and possible PFC propagation. |
| **PFC storm** | Configure persistent congestion on a downstream link. PFC pause propagates upstream naturally. The agent sees pause counters incrementing and has to trace the source. |
| **Asymmetric path performance** | Configure differential link characteristics on parallel paths. ECMP-hashed flows experience different performance based on which path they hash to. |
| **Hash polarization** | Configure flow patterns whose hashing collides on a small subset of links. The simulation produces the imbalance; counters show it; the agent has to recognize the pattern. |
| **Link flap** | Script link state transitions at specific simulation timestamps. The simulation handles the consequences (route updates within the static topology, traffic redistribution). |
| **Buffer misconfiguration** | Configure a switch with intentionally wrong buffer allocations. The downstream effects (early drops, PFC behavior changes) emerge from the simulation. |
| **Silent packet drops** | Configure a port with a small probabilistic drop rate. The simulation produces the drops; flows experience the effects; the agent has to track them down. |

Each of these is implemented as scenario configuration, not as runtime API. The scenario author defines the conditions; the simulation produces the failure. This means scenarios are deterministic and replayable: the same scenario configuration always produces the same failure behavior.

### 5.3 Scenario Authorship

Scenarios are authored as Python files in the Doppelgänger repository. A scenario file declares:

- The topology (small, medium, large, or custom).
- The base configuration (PFC, ECN, DCQCN parameters).
- The traffic patterns (which flows run, when, between which hosts, at what rates).
- The failure injections (what conditions produce what failures, at what simulation times).
- The simulation duration.
- Metadata for HarnessIT consumption (the symptom the scenario is intended to produce, the root cause for ground truth, the difficulty class).

Scenarios compile to NS-3 configuration files and traffic generator inputs. The Python authoring layer never directly invokes NS-3 APIs; it produces text artifacts that the C++ simulator consumes.

---

## 6. Time and Determinism

Two properties of Doppelgänger that distinguish it from real fabric investigation are worth being explicit about: how time works, and what makes simulations reproducible.

### 6.1 Simulation Time vs. Wall Clock

NS-3 simulates network behavior at a different rate than wall clock. A 60-second simulation does not take 60 seconds to run; it might take 30 seconds (faster than real time, for small topologies) or 30 minutes (slower than real time, for large complex scenarios). Doppelgänger surfaces simulation time as the canonical timestamp on every observation. The wall-clock duration of the simulation run is exposed as scenario metadata but is not what the agent reasons about.

This is a real difference from live fabric investigation, where wall clock and observation time are the same. The series will discuss this explicitly when introducing Doppelgänger to readers.

### 6.2 Determinism

Doppelgänger simulations are deterministic given a fixed scenario and a fixed random seed. The same scenario configuration with the same seed produces the same trace, the same counters, the same events, every time. This is essential for two reasons:

- **Eval reproducibility.** Eval scenarios that the agent runs against need to produce consistent ground truth. A non-deterministic simulator would mean eval results drift over time even when no harness changes were made.
- **Investigation replay.** When debugging the agent, being able to replay the exact same fabric state repeatedly is invaluable. Determinism is what makes replay meaningful.

Scenarios may include controlled non-determinism (different random seeds for the same scenario produce different specific timings while preserving the qualitative failure behavior), but this is opt-in per scenario, not the default.

### 6.3 Eval-Time Comparison Discipline

Doppelgänger's determinism makes eval comparisons tractable, but a finding from the 2026-05-02 fork spike added a load-bearing constraint to *how* comparisons must be done. The naive comparison — averaging flow completion times across baseline and injected runs — actively misleads when flows fail to complete.

The spike injected silent drops at 0.1% on a 0.2-second simulation. Four flows in the injected run did not complete; their `fct.txt` records were absent entirely. The remaining 251 completed flows had a *lower* median FCT than the baseline's 255 flows because the four missing flows were the slowest. Aggregate FCT statistics reported "the injected run was 17% faster" — the precise opposite of the truth. The full account is in `spike/decision_memo.md` (Finding #1).

Three rules for comparing simulation runs in Doppelgänger:

**1. Flow-count delta is a primary failure signature.** Compare flow counts before comparing flow times. A run with fewer completed flows than baseline has lost flows; aggregate timing comparisons are not interpretable until the missing flows are accounted for. Consumers should treat a flow-count delta as an alert in its own right.

**2. Compare distributions, not means.** Tail behavior (p99, p99.9, max) is where fabric pathologies show up. Means are systematically pulled around by missing-flow censoring. Doppelgänger surfaces full distributions, not just summary statistics. The agent should default to distribution-aware comparison primitives.

**3. Annotate incomplete flows.** Doppelgänger's Per-Flow Records (§4.2) include a record for every flow the scenario *intended to run*, including those that did not complete. The agent (and human eval) can then ask "did this flow complete? if not, why not?" rather than silently treating absent records as nonexistent flows.

Eval scenarios should specify expected completion counts as part of their ground-truth metadata (§5.3). A divergence between expected and observed completions is itself a finding.

This discipline is not Doppelgänger-specific — it propagates to any post-mortem trace analysis with selection bias from incomplete operations. The HarnessIT Architecture document discusses the eval-discipline implications under "evals and ground truth"; the rules above are the substrate-level commitments that make those higher-level disciplines work.

---

## 7. The ProtoViz Relationship

Doppelgänger and ProtoViz are sibling projects, not parent and child. Both consume NS-3 simulation artifacts. Doppelgänger surfaces them as the operational view (counters, configs, flows, events) for HarnessIT consumption. ProtoViz surfaces them as the protocol view (frame-by-frame interactions, packet sequences, multi-device exchanges) for human and agent understanding.

The relationship works because they share a substrate. Both projects can read the same NS-3 trace files. A scenario authored in Doppelgänger can be visualized in ProtoViz without modification; a scenario authored for ProtoViz can be queried by Doppelgänger if the relevant traces are emitted.

### 7.1 What Doppelgänger Emits That ProtoViz Consumes

Doppelgänger configures NS-3 to emit packet-level traces in addition to the higher-level counters and events. These traces are what ProtoViz needs to render protocol interactions. The trace format follows NS-3 conventions; ProtoViz reads them via the same path it reads other NS-3 sources.

### 7.2 Future: ProtoViz as an MCP Tool

A future addendum to the HarnessIT series will explore exposing ProtoViz as an MCP tool the agent can call to generate protocol-level visualizations during investigation. The agent encounters a complex multi-frame interaction in the Doppelgänger fabric, calls a `visualize_protocol` tool, and reasons about the resulting visualization. This addendum is mentioned in the HarnessIT architecture document and is not in scope for Doppelgänger v0.2, but the architecture supports it: Doppelgänger emits the traces, ProtoViz renders them, HarnessIT consumes both.

---

## 8. Other Substrate Adapters

Doppelgänger is the first Substrate Adapter for HarnessIT (see §1.4). Subsequent adapters plug into the same MCP contract Doppelgänger does and let HarnessIT's investigation logic run unchanged across substrates with materially different underlying behaviors. Two adapters are in scope for the published series: the NVIDIA AIR adapter (covered below at length, because its semantic differences from Doppelgänger most clearly demonstrate why the abstraction matters) and any future adapter the HarnessIT extension demonstrates. A SONiC Adapter, for example, would target SONiC-VS in containers or SONiC running on hardware, plugging the same MCP tool surface into a different underlying substrate. The pattern, not the specific list, is the deliverable.

The remainder of this section specifies what swapping Doppelgänger for the NVIDIA AIR adapter means concretely. The same shape of analysis would apply to a SONiC or any other future adapter; AIR is the worked example because it is the next adapter in the published series.

### 8.1 What Stays the Same

- The MCP tool surface from §2.2. Same tool families, same tool names where applicable, same response envelope.
- The data model. A node from AIR looks like a node from Doppelgänger from the consumer's perspective.
- The subscription primitives. AIR delivers events differently underneath, but the consumer-facing API is the same.
- The agent's investigation logic. Skills, retrieval, memory, orchestration — none of these change when the substrate swaps.

### 8.2 What Changes Specifically with AIR

- **Time becomes wall-clock.** AIR is a real-time emulation; observation timestamps are wall-clock times. Doppelgänger's simulation-time semantics do not apply.
- **Confidence is no longer always high.** Real telemetry has gaps, source disagreements, measurement uncertainty. The confidence field starts carrying meaningful information. The agent's reasoning about confidence becomes load-bearing in a way it was not when investigating Doppelgänger.
- **Failure injection works differently.** AIR runs real Cumulus Linux instances. To produce a failure, you configure the actual switches the way a real failure would manifest, or you let real failures emerge from the workload. Scripted-magical injection is not available.
- **Replay is not free.** AIR scenarios can be repeated, but they are not deterministic in the way Doppelgänger scenarios are. The same workload twice may produce slightly different timings.

### 8.3 Why This Matters Pedagogically

The transition from Doppelgänger to AIR becomes one of the series' most instructive moments. Readers see the same agent doing the same investigation work in two materially different substrate environments, with the harness unchanged. That is the cleanest demonstration possible of why the substrate-abstraction discipline matters. The post that introduces the AIR adapter is a payoff the entire series has been earning.

---

## 9. Implementation Guidance

This section is light by design. Most implementation choices belong to the implementer; this section names the constraints that matter.

### 9.1 The C++/Python Boundary, and the Driver/Adapter Split

Doppelgänger has *three* distinct codebases, separated by two boundaries.

**C++ NS-3 modifications.** Live in the upstream substrate repository (`provandal/ns3-datacenter`, a fork of `inet-tub/ns3-datacenter`). New behavioral models, custom switch configurations, additional metrics emission — all of this lives in C++ in that repository, alongside the vendored NS-3.39 source. Behavioral extensions are added as dedicated modules where possible (rather than scattered diffs across the upstream codebase) to keep changes inspectable as a coherent unit.

**Python Driver.** Lives in this repository (`provandal/doppelganger`) under `doppelganger/driver/` (or equivalent). The Driver's job is to drive the C++ simulator: it compiles topology and scenario declarations into the substrate's text configuration files, invokes the simulator binary as a subprocess, parses the substrate's output trace files (`fct.txt`, `mix.tr`, `pfc.txt`, `qlen.txt`, etc.), and exposes the parsed data through plain-Python methods. The Driver is testable in isolation (no MCP scaffolding required), usable from a REPL for ad-hoc investigation, and reusable by sibling consumers — notably ProtoViz (§7), which reads simulation artifacts via the Driver without going through MCP.

**Python Adapter (the MCP server).** Lives in this repository under `doppelganger/adapter/` (or equivalent). The Adapter is a thin MCP server that imports the Driver and registers MCP tools that delegate to Driver methods. The Adapter implements the response envelope (§2.3) and subscription primitives (§2.4); it does not know NS-3.

The two boundaries:

- **C++ ↔ Python.** Text artifacts: configuration files going in, trace files coming out. No live binding, no in-process integration, no shared memory, no Python bindings (the substrate's `./waf configure` runs with `--disable-python`). This is the simplest, most portable, most field-tested approach. It is also the boundary that keeps Doppelgänger's Apache-2.0 source from becoming a derivative work of NS-3 (see §9.5).
- **Driver ↔ Adapter.** In-process Python: standard `import` and method calls. The split exists for testability, separation of concerns (substrate semantics versus protocol/transport), and reusability of the Driver across non-MCP consumers (ProtoViz, command-line debugging, future tooling).

### 9.2 The NS-3 Substrate: provandal/ns3-datacenter (forked from inet-tub/ns3-datacenter)

Doppelgänger uses [`inet-tub/ns3-datacenter`](https://github.com/inet-tub/ns3-datacenter) as its NS-3 substrate, mirrored at [`provandal/ns3-datacenter`](https://github.com/provandal/ns3-datacenter) and pinned at commit `4dd55d89a46e742e505a92dc7873f82ded6db638` — master HEAD as of 2026-05-02, the date of the fork spike. The fork-spike memo at `spike/decision_memo.md` documents the full decision; this section names the choice and its implementation consequences.

`inet-tub/ns3-datacenter` is a TU Berlin Internet Network Architectures research codebase built on NS-3.39, including the full `ns3-rdma` and HPCC heritage plus PowerTCP, ABM, Reverie, and Credence buffer-management additions. NS-3.39 is 22 versions newer than the alibaba-edu HPCC base; it is post-CMake-transition (the script-named `./waf` in this codebase is a thin CMake wrapper, not the historical Waf build system), gcc-9-or-later compatible, Python-3-only, and free of the documented compile failures that affect alibaba-edu/HPCC on modern toolchains. The trade is a less-cited code base. HPCC remains the de facto reference for the field, but its base codebase has unfixed compile errors against post-2020 toolchains and was last meaningfully maintained around 2019. Inet-tub is a current research codebase (NSDI 2022, SIGCOMM 2022, NSDI 2024) — it trades citation count for build-current-ness, and is the right choice for an implementation that has to actually run.

Other candidates considered and rejected: `alibaba-edu/High-Precision-Congestion-Control` (kept as documented backup; not used because of the modern-toolchain compile failures noted above); `conweave-project/conweave-ns3` (out of scope — its scope is load-balancing-with-in-network-reordering, not a general RDMA simulator).

Two implementation details surfaced by the fork spike that downstream Doppelgänger code (Driver, scenarios, build glue) and reader-facing documentation must respect:

- **RDMA stack location.** The RDMA stack lives in `src/point-to-point/`, not a separate `src/rdma/` module as a naive grep would suggest. Engineers reading the substrate codebase for the first time should be told this; the `src/rdma/` they expect does not exist, and an early "the simulator is broken" reaction is otherwise predictable.
- **No "Waf" terminology.** The build script is named `./waf` for backward compatibility but is a CMake wrapper. Reader-facing documentation, commit messages, and code comments should refer to "the CMake build" or "the build system," not "Waf." The community migrated past Waf around NS-3.36; persisting Waf terminology in v0.2 would be reader-facing wrong.

The implementer should expect to:

- **Use the pinned fork.** Doppelgänger's Dockerfile clones from `provandal/ns3-datacenter` at SHA `4dd55d89...` (the same SHA the spike validated). Subsequent commits to the upstream `inet-tub` repository or to our `provandal` fork can be re-pinned only after a re-validation run. The pinning is what gives Doppelgänger reproducibility.
- **Add C++ extensions in the substrate fork, not in this repository.** Any C++ extensions (new behavioral models, custom switch configurations, additional metrics emission) belong in `provandal/ns3-datacenter` and are committed there as separate modules where possible. C++ extensions do not live in `provandal/doppelganger`. This keeps the GPL-2.0 / Apache-2.0 license boundary clean (see §9.5).
- **Write the Driver and Adapter (§9.1) in this repository.** The Driver compiles topology declarations into the substrate's text configuration format, invokes the simulator as a subprocess, and parses the substrate's output trace files. The Adapter is a thin MCP shell that delegates to the Driver. Both are Python; both are Apache-2.0.
- **Build a Docker image** that packages the simulator and its build dependencies (Ubuntu 22.04 base; gcc, cmake, Python 3, libsqlite3, libxml2, libgsl, libboost, etc.). The image is the project's "genuinely cloneable" deliverable; setup discipline is in §9.3.
- **Drop the no-op `--disable-modules` flag** from the substrate's `./waf configure` invocation. The flag is silently ignored in modern NS-3 (modern equivalent is `-DNS3_ENABLED_MODULES`). The spike's Dockerfile carried it as carryover from the pre-spike candidate analysis; it produces no error but no effect.

### 9.3 Stage 0 Setup

Doppelgänger's setup is part of HarnessIT's stage 0 in the build plan. The setup must be:

- **Reproducible from a single command.** `docker build -t doppelganger -f Dockerfile .` (or equivalent) is sufficient. No external account, no API key, no registry pull. The 2026-05-02 fork spike confirmed cold-cache build of the inet-tub-fork-based image is approximately 5 minutes wall-clock — much faster than the pre-spike 20–40-minute estimate; image size is approximately 1.23 GB, much smaller than the pre-spike 4–6 GB estimate. The 30-minute clone-to-run target is therefore comfortably achievable from the Dockerfile alone.
- **Self-contained.** No registry pull required; the Dockerfile clones the substrate (`provandal/ns3-datacenter` at the pinned SHA) at build time. A pre-built image published to a registry (Docker Hub or GHCR) is a polish item, not a contract; it reduces the 5-minute build to a 30-second pull but is not required for the "genuinely cloneable" promise.
- **Documented end-to-end** in a README that takes a fresh-clone reader from zero to a running simulation in under thirty minutes.

The README must include the **Windows-host bind-mount workaround**: Git Bash for Windows mangles Docker `-v` paths unless the user invokes Docker with `MSYS_NO_PATHCONV=1` and uses `pwd -W`. For example:

```
MSYS_NO_PATHCONV=1 docker run -v "$(pwd -W):/work" doppelganger
```

Without this, host-side files appear empty after the container exits because Docker writes them to a path the host cannot resolve. PowerShell users do not hit this; Linux and macOS users do not hit this. A reader on Windows who skips this footnote will believe Doppelgänger does not work and abandon the series.

Setup friction is the most common reason readers abandon a series. Doppelgänger's setup is harder than a pure-Python project, and the documentation has to take this seriously.

### 9.4 Inspectability for Non-Native C++ Readers

The intended primary author of HarnessIT and its companion projects is more comfortable in Python than C++. The implementer should treat code inspectability as a first-class concern:

- Inline comments in non-obvious C++ sections, written for a reader who knows the domain but not the language.
- Prose summaries in commit messages explaining what changed and why, alongside the diff.
- Doppelgänger's C++ extensions kept in dedicated modules where possible, rather than scattered diffs across the upstream codebase. A reader should be able to view the full delta as a coherent unit.

### 9.5 License Boundary

Doppelgänger consists of two repositories with different licenses:

- **`provandal/doppelganger`** (this repository): Apache License 2.0. Contains the Driver (§9.1), the Adapter, scenario authoring, scenario libraries, parsers, the Dockerfile, and documentation. Does not contain NS-3 source code.
- **`provandal/ns3-datacenter`** (the substrate): GPL version 2 only. A fork of `inet-tub/ns3-datacenter`, which vendors NS-3.39 source under `simulator/ns-3.39/`. All C++ extensions Doppelgänger contributes are committed there.

The boundary between them is the Docker image build step: the Dockerfile clones the substrate from `provandal/ns3-datacenter` at the pinned SHA at build time. The repositories are never co-mingled at the source level, never depend on each other as a git submodule, and never include each other's code by reference.

Doppelgänger's Driver communicates with NS-3 via subprocess invocation and text-file exchange — not via dynamic or static linkage, not via Python bindings (the substrate's `./waf configure` runs with `--disable-python`). This is the FSF GPL FAQ's textbook arms-length boundary: separate programs communicating through pipes, sockets, or files do not produce a derivative work under GPL-2.0. Doppelgänger's Apache-2.0 source therefore does not become a derivative work of NS-3.

Built Doppelgänger Docker images contain both: GPL-2.0 NS-3 binaries and Apache-2.0 Doppelgänger Python. This is "mere aggregation" under GPL-2.0 §2 — GPL-2.0 terms apply to NS-3 components within the container; Apache-2.0 terms apply to Doppelgänger components within the container. The container's `NOTICE` file documents this composition explicitly.

One contributor rule that protects this boundary: **do not introduce GPL-licensed code, or code that statically or dynamically links against GPL libraries, into this repository.** C++ extensions to NS-3 belong in `provandal/ns3-datacenter`. Python scenarios, parsers, MCP tools, and orchestration belong here. `CONTRIBUTING.md` states this rule for external contributors.

Sibling Substrate Adapters (the AIR Adapter, a hypothetical SONiC Adapter) follow the same model with their own substrate licenses. NVIDIA AIR is a proprietary hosted service; the AIR Adapter's Driver communicates with it via REST and SSH and carries no GPL inheritance. SONiC is itself Apache-2.0; a SONiC Adapter would be Apache-2.0 throughout. The pattern — the Substrate Adapter is permissively licensed, the substrate carries its own license, the boundary is at runtime — generalizes.

---

## 10. Open Questions

The following are deliberately not specified in v0.2. The implementer or future iterations will resolve them.

**Trace file format choice.** NS-3 supports several trace formats. Which format Doppelgänger emits to disk affects parser complexity and ProtoViz compatibility. The implementer makes this call based on what works best for both consumers. *Note from spike:* the substrate's `mix.tr`, `pfc.txt`, and `qlen.txt` files were empty in the spike runs despite `ENABLE_TRACE 1`. `qlen.txt` is a documented config bug (`QLEN_MON_START` set past `SIMULATOR_STOP_TIME` in the substrate's example config); `mix.tr` and `pfc.txt` may need code-level trace hooks to populate. Stage 1 investigation backlog.

**Scenario versioning.** Scenarios will evolve. How they are versioned, whether eval results from old scenario versions are preserved, how scenarios deprecate — all of this is left to the implementer.

**Simulation caching.** Many investigations may run against the same scenario repeatedly. Caching simulation outputs to avoid re-running the same scenario every time is a real optimization. Whether v0.2 includes it or defers it is the implementer's call.

**Multi-scenario composition.** The agent might want to investigate scenarios that were not authored in advance — for example, taking an existing scenario and extending it with an additional failure injection. v0.2 does not commit to supporting this; later versions may.

**Largest topology runnable in <5 minutes wall-clock.** The fork spike ran a 256-node leaf-spine topology in 3.6 seconds wall-clock for 0.2 seconds simulated. Empirical numbers for the v0.2 §3.1 "Medium" (128-host) and "Large" (512-host) reference topologies, at the longer simulated durations real eval scenarios will use, are not yet established. Stage 1 investigation backlog.

**Failure-class to config-knob inventory.** Silent drops were one config-line change (`ERROR_RATE_PER_LINK 0.001`) in the spike — substantially simpler than the pre-spike assumption of a custom `RateErrorModel` C++ class. The other six failure classes from §5.2 may also have direct `config-burst.txt` knobs, in which case the v0.2 implementation guidance ("each is implemented as scenario configuration") becomes even cleaner than v0.2 anticipated. Stage 1 deliverable: inventory each of the seven classes against the substrate's available knobs and document any that need C++ extensions to the substrate fork.

**Algorithm-selection contract.** The substrate exposes both an `--algorithm=N` command-line override and a `CC_MODE` config-file setting. Their interaction (precedence, validity ranges, what happens if they disagree) is not documented in the substrate. Stage 1 investigation backlog.

**AI-fabric-specific gap closure.** §3.3 enumerates several AI-training-specific behaviors v0.2 does not model (NCCL collectives, GPUDirect, multi-rail, adaptive routing, app-layer-scheduled traffic, etc.). Whether any of these become in scope for v0.3 or beyond is determined by which scenarios the published series finds itself wanting to demonstrate.

*Resolved from v0.1's open-question list:* "Specific NS-3 version" is resolved; see §9.2.

---

## 11. Closing

Doppelgänger is the third foundational deliverable for HarnessIT. With the architecture overview, the build plan, and this design document, the foundational thinking is complete. Stage 0 of the build can begin.

The choice to use NS-3 with the inet-tub fork as the substrate, rather than building a behavioral simulator, is the most consequential decision in this document. It commits us to a steeper setup story and a less Python-native experience. It also commits us to a simulator that does not run out of fidelity at the place where we need it, and that lets us inherit years of validated RDMA implementation work that we have no business reproducing.

Inherit the simulation. Build the scenarios. Expose the results. Let the agent investigate.
