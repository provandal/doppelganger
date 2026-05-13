# Doppelgänger — Design Document

**The NS-3 Substrate Adapter for HarnessIT**

Draft v0.3 · provandal.dev

**Companion to:** HarnessIT Architecture Overview v0.6

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

The split exists for testability (the Driver is reusable from a REPL or unit test without MCP scaffolding), separation of concerns (substrate semantics versus protocol/transport), and reusability of the Driver by sibling consumers like ProtoViz (see §7) that read substrate outputs directly without going through MCP. The architecture document (HarnessIT Architecture v0.6) covers the Substrate Adapter role at length; this section names Doppelgänger's place in it.

---

## 2. The Interface Contract

This section is the most important part of the document. The interface contract is what consumers see; it is the durable spec; everything else in this document can change without breaking it. Get this right and swapping NS-3 for AIR later is a matter of writing an adapter. Get it wrong and the AIR transition becomes a major rewrite.

### 2.1 Consumers

Doppelgänger has two consumer classes:

- **HarnessIT (primary).** The agentic harness consumes Doppelgänger as its sensing layer. HarnessIT does not know whether it is talking to Doppelgänger, an AIR adapter, or eventually a real fabric. It consumes the interface defined here.
- **ProtoViz (secondary).** The protocol visualization tool consumes the same simulation artifacts to render protocol-level interactions. ProtoViz is a sibling consumer, not a downstream one — it reads Doppelgänger's outputs directly rather than going through HarnessIT.

### 2.2 The Tool Surface

Doppelgänger exposes its state through MCP tools. v0.3 ships seven concrete tools, registered by the Adapter against the Driver (§9.1). They fall into four families.

| Tool | Family | Purpose |
|---|---|---|
| `list_scenarios` | Scenario metadata | Enumerate the named scenarios this Doppelgänger build knows how to run, with their declared symptom, ground-truth root cause, and difficulty class. |
| `run_scenario` | Scenario metadata | Run a named scenario to completion (or return the cached trace, see §6.4) and return a `run_id` + trace directory that subsequent tool calls reference. |
| `get_topology` | Topology | Return the fabric graph for a scenario — nodes (switches, hosts), ports, links, IP assignments, neighbor relationships. |
| `get_fabric_counters` | Counters and metrics | Return per-(switch, port, queue) interface counters, per-priority PFC pause counts, ECN-CN marks, and priority-group watermarks for a completed run. Substrate-side rollup; SONiC-shape (§4.1). |
| `get_flow_records` | Flows and traffic | Return one record per flow the scenario *intended to run*, including completed, late, and incomplete flows. Cross-references `intended.txt` against `fct.txt` (§4.2). |
| `get_host_counters` | Counters and metrics | Return per-host PHY-rx drop counts collected from the host-ingress NetDevice PhyRxDrop trace (§4.5). |
| `compare_runs` | Scenario metadata | Compare two runs of the same scenario class along the disciplines of §6.3 (flow-count delta first, then distribution comparison, with incomplete-flow accounting). |

The v0.2 tool surface was specified as six aspirational tool families with example tool names (`get_counter`, `tail_log`, `search_events`, `get_config_history`, etc.). v0.3 narrows this to the seven tools above — the tools required to support the §5.2 failure classes currently in scope and the eval discipline of §6.3. Tool families v0.2 reserved (Configuration history, Logs and events, packet-level flow tools) remain reserved; they will be added when a §5.2 scenario requires them, not before. The contract is "this is the surface today"; the agent-facing surface is the tools that exist, not the tool families that were imagined.

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

One v0.2-vs-v0.3 nuance the Stage-5a leak fix surfaced: response payloads must not carry the scenario name as a field on data records. The scenario name is a *request* parameter, not a *substrate observation*. Echoing it inside per-record fields lets a sufficiently clever agent shortcut diagnosis by reading the scenario name out of its own tool responses. v0.3 strips scenario-name from per-record fields across all seven tools. This is one example of a broader rule covered in §6.5: leak prevention is per-field and is structurally non-obvious.

### 2.4 Subscription Semantics

Doppelgänger supports the same subscription primitives HarnessIT expects from any sensing layer (state-change, threshold, stream, anomaly), with one important caveat: subscriptions over Doppelgänger replay simulation events at a configurable rate. The agent can request real-time replay (matching simulation time to wall clock), accelerated replay (faster than simulation time, useful for fast-forwarding to interesting moments), or paused replay (subscriber controls when the next event arrives, useful for stepwise investigation).

This is one of Doppelgänger's pedagogical advantages. A live fabric only flows at one speed. A simulated fabric can be paused, rewound, and replayed at the rate the investigator needs. When eval scenarios run in real-time replay mode (no rewind, advance gated on wall-clock time), the agent's exposure to the simulation has the *feel* of a live investigation — exercising the live-troubleshooting disciplines (counter clearing, propagation watching, time-pressure prioritization) that pure post-mortem analysis does not.

Subscriptions are designed-but-not-shipped in v0.3. The seven tools in §2.2 are call-and-return only. Subscription primitives are deferred until the first eval scenario requires them; the architecture supports them but the implementation does not yet expose them.

---

## 3. The Fabric Model

Doppelgänger v0.3 supports a single fabric topology class: leaf-spine RoCE fabrics in the small-to-medium size range relevant for AI training scenarios.

### 3.1 Topology Class

Leaf-spine fabrics with the following characteristics:

- Configurable number of leaf switches and spine switches.
- Configurable hosts per leaf (representing GPU servers).
- Configurable link speeds at leaf-host and leaf-spine boundaries.
- Configurable buffer sizes per switch (modeled on real ASIC characteristics; per-switch override is a v0.3 substrate gap — see §5.2 / §10).
- Configurable PFC, ECN, and DCQCN parameters per switch and per priority.

v0.3 ships with three reference topologies:

- **Small.** 4 leaves, 2 spines, 8 hosts per leaf. 32 hosts total. Useful for fast iteration during agent development and for small-scale eval scenarios. The four §5.2-active scenarios all run against this topology in the current eval set.
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

**AI-training-specific behaviors that v0.3 does not model.** The architecture document positions HarnessIT around AI fabric operations. Doppelgänger v0.3's actual scope is narrower than that framing implies: it covers fabric-layer congestion pathologies (PFC, ECN, DCQCN, queue dynamics, microbursts) on AI-style topologies. It does not model:

- **NCCL collective patterns.** Ring, tree, and double-binary-tree collectives produce specific traffic shapes (incast bursts at allreduce boundaries, persistent pairwise traffic during alltoall) that scenario authors must hand-author as flow patterns rather than emerging from a collective orchestrator.
- **GPUDirect RDMA semantics.** GPU memory pinning, NIC-GPU peer-to-peer, and timing artifacts where the GPU is the producer or consumer are not modeled. Pathologies that look like NIC stalls but originate at the GPU are invisible.
- **SEND vs WRITE vs READ verb differences.** The flow model is essentially WRITE — push bytes, signaled completion. SEND with receive-queue starvation, READ with response RTT, atomics, and the RDMA error semantics that distinguish them are not differentiated.
- **Out-of-order delivery and selective acknowledgement.** The RoCE model assumes in-order delivery with go-back-N retransmit. Modern ConnectX-6/7 NICs support out-of-order delivery with adaptive retransmit; that behavior is not modeled.
- **Multi-rail / dual-port topologies.** Real AI training fabrics often use multi-rail (each GPU has its own NIC, different rails are independent fabrics, traffic is striped). Doppelgänger v0.3 topologies are single-rail.
- **Receive-side scaling and NIC-internal queue pathologies.** Multi-QP RSS, completion queue overflow, and CQ moderation behavior are abstracted away.
- **Adaptive routing.** Spectrum-X and Tomahawk-5 adaptive routing differ materially from static ECMP; v0.3 models static ECMP only.
- **Application-layer congestion control that bypasses NIC CC.** Meta (SIGCOMM 2024) has reported disabling DCQCN for LLM training because it does not work well for low-flow-entropy / high-burstiness workloads, and instead implements traffic scheduling at the application layer; DeepSeek has done the same. Doppelgänger v0.3 covers DCQCN/PFC/ECN-based congestion control, which is still the dominant deployed pattern outside hyperscale LLM training. App-layer-scheduled traffic patterns are not modeled.

These limitations are not weaknesses; they are scope decisions. **Doppelgänger v0.3 is "AI fabric *congestion* troubleshooting," not the full AI-fabric operational surface.** Later versions can extend in any of these directions if specific scenarios require it. The interface contract in §2 is designed so extensions add new tool families and event types without breaking existing consumers.

---

## 4. What Doppelgänger Surfaces

This section enumerates what observers (HarnessIT, ProtoViz, eventually direct human inspection) see when they query a completed Doppelgänger simulation. It is not the implementation; it is the visible surface.

### 4.1 Per-Port Counters

For every port on every switch, Doppelgänger exposes a SONiC-shape per-(switch, port, queue) counter rollup as a single snapshot at end-of-simulation, with the substrate emitting `counters.txt` (fabric egress) and `ecn.txt` (ECN-CN marks) during the run. The per-tool response from `get_fabric_counters` is keyed by `(switch_id, port, priority_queue)` and carries:

- Bytes and packets transmitted on the queue.
- Drops by queue (buffer exhaustion at this priority).
- **Per-priority PFC pause frames transmitted and received.** Per-priority is load-bearing: PFC headroom only exhausts on the priority the scenario is congesting, and aggregate PFC counters wash that signal out.
- **ECN-CN marks transmitted** (CE-bit-set packets observed at the egress of this port).
- **Priority-group (PG) watermarks.** The highest queue depth observed for the priority during the run. PG watermarks are the dominant SONiC signal for "did this queue actually fill" and are surfaced as standalone fields, not derived from packet-level traces.

The "fabric counter" framing is intentional: this tool answers fabric-layer questions. Host-side drops (which a misconfigured RX path produces and a switch-egress counter set will never reveal) are a separate tool — see §4.5. The Stage-5a sweep made this gap concrete: silent drops at host ingress are invisible to switch-egress counters by construction, and pretending otherwise is a fidelity bug.

Counters are end-of-simulation snapshots in v0.3. Time-indexed counter series (point-in-time snapshots or time-range series) are designed-but-not-shipped; they require enabling additional substrate trace hooks and are deferred until a §5.2 scenario requires intra-run sampling.

### 4.2 Per-Flow Records

For every RDMA flow that the scenario *intended to run* — whether or not it completed successfully — Doppelgänger exposes:

- **Identifying fields.** Source and destination identifiers (IP and node-id), scenario-assigned flow ID, intended start time, intended byte count.
- **Outcome.** Completion status (`completed`, `incomplete`). For completed flows: actual completion timestamp, actual flow completion time (FCT), actual bytes transferred, source port.
- **Path-taken hints.** Source and destination IPs, source port for ECMP-hash recovery. Per-packet path traces are not surfaced in v0.3.
- **Congestion experience.** For completed flows: FCT versus ideal/slowdown. For incomplete flows: last-observed state from the intended-flow record only — packet-level congestion telemetry for incomplete flows is not extracted in v0.3.

The "every flow that the scenario intended to run" framing is load-bearing for eval discipline (see §6.3). Flows that fail to complete must produce a record, not silence — otherwise aggregate flow-time statistics on the surviving flows are systematically misleading.

**Implementation note (new in v0.3).** v0.2 committed to surfacing incomplete-flow records but the implementation was vacuous through 2026-05-11: `fct.txt` only contained completed-flow records, so the Driver had no way to know which flows had been intended. v0.3 plugs this by having the substrate emit `intended.txt` alongside `fct.txt` — a 5-column record per scenario-intended flow (`sip_hex dip_hex dport packets start_ns`) written from the C++ traffic generator at flow-injection time. The Driver cross-references `intended.txt` against `fct.txt` and emits a `PerFlowRecord` for every intended flow, with `sport = None` (not `0` or any other placeholder) on records that did not complete. The substrate-side write avoids the Python-side guessing about which flows the scenario was supposed to launch; the cross-reference is mechanical. Substrate commits `f004e9e` (intended.txt emission) and Doppelgänger commits `ae84b0d` (cross-reference) and `ee9da48` (sport-None fix) are the load-bearing changes.

The `sport = None` choice is itself a lesson: an earlier `sport = 0` placeholder leaked into agent reasoning as a "real" signal and the agent constructed a "library regression sport=0" story around it. `None` cannot be confused for a measurement; a `0` can. This is captured as a §6.5 design rule.

### 4.3 Discrete Events

Beyond counters and flows, Doppelgänger's interface contract reserves space for discrete events that an investigator would care about:

- PFC pause events (start, duration, source, propagation chain).
- ECN mark surge events (when marks exceed a threshold for a duration).
- Queue overflow events.
- Link state change events.
- Configuration change events (for scenarios that include mid-run config changes).
- Injected failure events (for traceability — every injected failure produces an event so the agent can correlate symptoms with the underlying cause when investigating).

In v0.3 these are not surfaced as a separate tool; the equivalent signal is reconstructible from `get_fabric_counters` (per-priority pause counts, ECN-CN marks, queue watermarks) plus scenario metadata. A `get_events` tool is added when the first §5.2 scenario in scope requires intra-run event semantics that counters cannot reconstruct.

### 4.4 Configuration Snapshots

For each device, Doppelgänger's contract surfaces the configuration as it was at the start of the simulation and at any point where it changed during the simulation. The configuration model includes per-port settings, buffer allocations, PFC and ECN configurations, and routing tables.

v0.3 surfaces *initial* configuration via `get_topology`. Mid-run configuration changes are not surfaced via a tool in v0.3 because no in-scope §5.2 scenario currently includes them. A `get_config_history` tool is added when a scenario requires it.

### 4.5 Host Counters (new in v0.3)

For every host in the fabric, Doppelgänger exposes a per-`(host_id, interface_index)` PHY-rx drop count covering drops observed at the host's ingress NetDevice. The substrate emits `host_counters.txt` at end-of-simulation, populated by a `PhyRxDrop` trace callback subscribed on host NetDevices (both source-side and destination-side hosts). The Adapter exposes the parsed result via `get_host_counters`.

The motivation is concrete: switch-egress counters by construction cannot report drops that happen at the host's PHY (cable cut at the host port, RX-path silent drop, NIC-internal drop). The Stage-5a §5.2 sweep made this gap visible — a "silent drops" scenario whose drops occur at host ingress is invisible to `get_fabric_counters`. Surfacing host counters as a separate tool closes the fault-class × tool-surface gap rather than overloading the fabric-counters response with cross-domain semantics.

The substrate-side implementation is two pieces: the `HOST_COUNTERS_OUTPUT_FILE` config knob (substrate commit `1a7b9d0`) and the `on_host_phy_rx_drop` callback subscribed on every host NetDevice's `PhyRxDrop` trace source. The Driver parses the resulting 3-column file (`host_id if_index drop_packets`). Host-side telemetry beyond PHY drops (rx-ring overflow, completion queue overflow, NIC-internal counters) is not modeled because the underlying NS-3 device model does not differentiate them; the v0.3 host-counters contract is "PHY drop counts only."

---

## 5. Failure Injection

This is where Doppelgänger genuinely earns its keep over alternatives. Real fabrics do not let you summon a microburst on demand. Doppelgänger does. The failure injection model is what makes the simulator useful for teaching and for eval work specifically.

### 5.1 Injection Philosophy

Doppelgänger's failure injection happens at scenario authorship time, not at investigation time. A scenario author defines the conditions that produce a failure, and the simulation produces the failure naturally as those conditions are met. This is honest to how failures actually work — they emerge from conditions — and it gives downstream effects that are mechanistically correct.

Concretely, you do not call `inject_microburst()` and have a microburst appear. You configure incast traffic at a specific moment, with specific flow timing and queue settings, and the simulation produces the microburst. This is more work than scripted-magical injection, but it produces more realistic propagation effects, and the agent investigating the result has to reason about the actual mechanism rather than the magical injection.

### 5.2 Failure Classes and v0.3 Coverage

| Failure class | Injection mechanism | v0.3 status |
|---|---|---|
| **Microburst** | Incast traffic patterns with synchronized start times produce a burst with associated buffer pressure and possible PFC propagation. | Substrate-supported; in-scope scenario shipped (`microburst_with_counters_tool`). |
| **PFC storm** | Persistent congestion on a downstream link; PFC pause propagates upstream naturally; agent traces source from per-priority pause counters. | Substrate-supported; in-scope scenario shipped. |
| **Asymmetric path performance** | Differential link characteristics on parallel paths; ECMP-hashed flows hit divergent performance. | Substrate-supported; in-scope scenario shipped (`asymmetric_path_with_counters_tool`). |
| **Hash polarization** | Flow patterns whose ECMP hashes collide on a small subset of links; bimodal FCT distribution is the visible signature. | Substrate-supported with caveat: bimodality requires `repetitions_per_pair ≥ 32` (default bumped from 4 to 32 in commit `b512a1b`). With the default-4 configuration the signal was a single mode and the failure was invisible to the agent. |
| **Link flap** | Substrate would need to schedule `NetDevice::SetDown` / `SetUp` at scripted simulation times. | **Substrate gap, deferred.** No mechanism currently exposed for in-run link state transitions. Scenario authors cannot inject this in v0.3. Closure is a v0.4 substrate task (see §10). |
| **Buffer misconfiguration** | Substrate would need a per-switch buffer-size override applied to a specific switch within an otherwise-default topology. | **Substrate gap, deferred.** Current substrate uses a global buffer-size constant; per-switch override is not exposed. Closure is a v0.4 substrate task. |
| **Silent packet drops** | `ERROR_RATE_PER_LINK` config knob produces probabilistic drops on a port; flows experience effects; agent traces them. | Substrate-supported; in-scope scenario shipped (`silent_drops_with_counters_tool`). Drops-per-million counter polish is a v0.4 task. |

Each in-scope failure class is implemented as scenario configuration, not as runtime API. The scenario author defines the conditions; the simulation produces the failure. Scenarios are deterministic and replayable: the same scenario configuration with the same seed always produces the same failure behavior.

The retraction of "all seven classes are equally available" relative to v0.2 is deliberate. The 2026-05-11 substrate-fidelity audit surfaced the two gaps above; pretending they were available was the kind of fidelity-bug the Stage-5a / Stage-5b work was specifically trying to root out. v0.3 names what is shipped and what is gapped; v0.4's substrate work closes the gaps.

### 5.3 Scenario Authorship

Scenarios are authored as Python files in the Doppelgänger repository. A scenario file declares:

- The topology (small, medium, large, or custom).
- The base configuration (PFC, ECN, DCQCN parameters).
- The traffic patterns (which flows run, when, between which hosts, at what rates). The v0.3 background-traffic shape uses a layered emitter (`layered_background`) so the foreground signal is set against realistic baseline congestion.
- The failure injections (what conditions produce what failures, at what simulation times).
- The simulation duration.
- Metadata for HarnessIT consumption (the symptom the scenario is intended to produce, the root cause for ground truth, the difficulty class, and the *expected completion count* for §6.3 incomplete-flow accounting).

Scenarios compile to NS-3 configuration files and traffic generator inputs. The Python authoring layer never directly invokes NS-3 APIs; it produces text artifacts that the C++ simulator consumes.

---

## 6. Time and Determinism

Two properties of Doppelgänger that distinguish it from real fabric investigation are worth being explicit about: how time works, and what makes simulations reproducible. A third property — session-level run caching, new in v0.3 — falls naturally in the same section because it is what makes determinism *operationally useful* during a HarnessIT eval run.

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

**3. Annotate incomplete flows.** Doppelgänger's Per-Flow Records (§4.2) include a record for every flow the scenario *intended to run*, including those that did not complete. The agent (and human eval) can then ask "did this flow complete? if not, why not?" rather than silently treating absent records as nonexistent flows. As of v0.3 this is non-vacuous: the substrate emits `intended.txt` and the Driver cross-references.

Eval scenarios specify expected completion counts as part of their ground-truth metadata (§5.3). A divergence between expected and observed completions is itself a finding.

This discipline is not Doppelgänger-specific — it propagates to any post-mortem trace analysis with selection bias from incomplete operations. The HarnessIT Architecture document discusses the eval-discipline implications under "evals and ground truth"; the rules above are the substrate-level commitments that make those higher-level disciplines work.

### 6.4 Session-Level Run Cache (new in v0.3)

A HarnessIT eval session typically runs multiple tools against the same scenario in sequence: `run_scenario` produces a trace, then `get_topology`, `get_fabric_counters`, `get_flow_records`, and `get_host_counters` each query that trace. With substrate runs taking tens of seconds for small topologies and minutes for medium/large, re-running the substrate per tool call would be 4-5× more expensive than necessary and would obscure the determinism property (every call would produce a "fresh" trace at a slightly different wall-clock).

v0.3 adds Driver-level idempotency keyed by `run_id`. The Driver's `simulation.run()` method checks whether `trace_dir` already contains the complete substrate output set before invoking the substrate. The completeness check looks for all six required files:

```
fct.txt, intended.txt, pfc.txt, ecn.txt, counters.txt, host_counters.txt
```

If all six are present and non-empty, the substrate is not re-invoked; the existing trace is returned. If any are missing, the substrate runs to completion. This is implemented at the Driver layer (substrate commits `28d9dd8`), so all four read-only tools that downstream of `run_scenario` benefit automatically.

Three properties of this design worth naming:

- **Required-file list is the contract.** The cache check is not "trace_dir exists" — that would short-circuit incomplete runs. The check is "all six required files exist," which is the actual completeness condition. Adding a new substrate output file in a future version means adding it to `_REQUIRED_OUTPUT_FILES`; partial migrations re-run the substrate, which is the correct behavior.
- **The cache is session-level, not persistent.** `run_id` is per-eval-session. Two different eval sessions of the same scenario produce different `run_id`s and therefore different `trace_dir`s and re-run the substrate. This preserves the "different sessions can vary seeds" affordance while making within-session tool calls free.
- **The cache is fail-loud on corruption.** If a `trace_dir` exists but is incomplete (substrate crashed mid-run, file system error, manual deletion), the next tool call re-runs the substrate rather than returning partial data. There is no half-cached state.

### 6.5 Data-Leakage Discipline

A subtle property of Doppelgänger that v0.3 makes explicit: tool responses must not carry information that would let the agent shortcut diagnosis by reading scenario identity out of substrate-shaped data. The 2026-05-10 Stage-5a SONiC work surfaced two distinct leak classes:

- **Structural leaks.** Per-record fields that echo the scenario name, ground-truth root cause, or scenario metadata. v0.3 strips these at the response-envelope layer; scenario name is a *request* parameter, never a *substrate observation*.
- **Volumetric / shape leaks.** Toy-shaped data (single-digit flow counts, two-row counter tables, conspicuous round numbers) lets an agent shortcut analysis even when no field literally names the cause. Realistic background traffic (the `layered_background` shape from §5.3) and realistic counter cardinality are part of the leak-prevention discipline.

Placeholder values in tool responses are a third, related class of trap. A "no source port observed" placeholder of `sport = 0` reads, to the agent, like a real measurement of a real sport-zero packet. v0.3 uses `None` (or domain-equivalent unknown markers) rather than zero or other valid-looking values for missing fields. The principle generalizes: every field's value range must be either real measurements or unambiguously distinguishable as "no observation," never both.

---

## 7. The ProtoViz Relationship

Doppelgänger and ProtoViz are sibling projects, not parent and child. Both consume NS-3 simulation artifacts. Doppelgänger surfaces them as the operational view (counters, configs, flows, events) for HarnessIT consumption. ProtoViz surfaces them as the protocol view (frame-by-frame interactions, packet sequences, multi-device exchanges) for human and agent understanding.

The relationship works because they share a substrate. Both projects can read the same NS-3 trace files. A scenario authored in Doppelgänger can be visualized in ProtoViz without modification; a scenario authored for ProtoViz can be queried by Doppelgänger if the relevant traces are emitted.

### 7.1 What Doppelgänger Emits That ProtoViz Consumes

Doppelgänger configures NS-3 to emit packet-level traces in addition to the higher-level counters and events. These traces are what ProtoViz needs to render protocol interactions. The trace format follows NS-3 conventions; ProtoViz reads them via the same path it reads other NS-3 sources.

### 7.2 Future: ProtoViz as an MCP Tool

A future addendum to the HarnessIT series will explore exposing ProtoViz as an MCP tool the agent can call to generate protocol-level visualizations during investigation. The agent encounters a complex multi-frame interaction in the Doppelgänger fabric, calls a `visualize_protocol` tool, and reasons about the resulting visualization. This addendum is mentioned in the HarnessIT architecture document and is not in scope for Doppelgänger v0.3, but the architecture supports it: Doppelgänger emits the traces, ProtoViz renders them, HarnessIT consumes both.

---

## 8. Other Substrate Adapters

Doppelgänger is the first Substrate Adapter for HarnessIT (see §1.4). Subsequent adapters plug into the same MCP contract Doppelgänger does and let HarnessIT's investigation logic run unchanged across substrates with materially different underlying behaviors. Two adapters are in scope for the published series: the NVIDIA AIR adapter (covered below at length, because its semantic differences from Doppelgänger most clearly demonstrate why the abstraction matters) and any future adapter the HarnessIT extension demonstrates. A SONiC Adapter, for example, would target SONiC-VS in containers or SONiC running on hardware, plugging the same MCP tool surface into a different underlying substrate. The pattern, not the specific list, is the deliverable.

The remainder of this section specifies what swapping Doppelgänger for the NVIDIA AIR adapter means concretely. The same shape of analysis would apply to a SONiC or any other future adapter; AIR is the worked example because it is the next adapter in the published series.

### 8.1 What Stays the Same

- The MCP tool surface from §2.2. Same tool names where applicable (`get_topology`, `get_fabric_counters`, `get_flow_records`, `get_host_counters`), same response envelope, same session-level cache semantics.
- The data model. A node from AIR looks like a node from Doppelgänger from the consumer's perspective.
- The subscription primitives. AIR delivers events differently underneath, but the consumer-facing API is the same.
- The agent's investigation logic. Skills, retrieval, memory, orchestration — none of these change when the substrate swaps.

### 8.2 What Changes Specifically with AIR

- **Time becomes wall-clock.** AIR is a real-time emulation; observation timestamps are wall-clock times. Doppelgänger's simulation-time semantics do not apply.
- **Confidence is no longer always high.** Real telemetry has gaps, source disagreements, measurement uncertainty. The confidence field starts carrying meaningful information. The agent's reasoning about confidence becomes load-bearing in a way it was not when investigating Doppelgänger.
- **Failure injection works differently.** AIR runs real Cumulus Linux instances. To produce a failure, you configure the actual switches the way a real failure would manifest, or you let real failures emerge from the workload. Scripted-magical injection is not available.
- **Replay is not free.** AIR scenarios can be repeated, but they are not deterministic in the way Doppelgänger scenarios are. The same workload twice may produce slightly different timings — which also means the §6.4 session cache becomes a different kind of artifact, since "re-run produces the same trace" no longer holds.

### 8.3 Why This Matters Pedagogically

The transition from Doppelgänger to AIR becomes one of the series' most instructive moments. Readers see the same agent doing the same investigation work in two materially different substrate environments, with the harness unchanged. That is the cleanest demonstration possible of why the substrate-abstraction discipline matters. The post that introduces the AIR adapter is a payoff the entire series has been earning.

---

## 9. Implementation Guidance

This section is light by design. Most implementation choices belong to the implementer; this section names the constraints that matter.

### 9.1 The C++/Python Boundary, and the Driver/Adapter Split

Doppelgänger has *three* distinct codebases, separated by two boundaries.

**C++ NS-3 modifications.** Live in the upstream substrate repository (`provandal/ns3-datacenter`, a fork of `inet-tub/ns3-datacenter`). New behavioral models, custom switch configurations, additional metrics emission — all of this lives in C++ in that repository, alongside the vendored NS-3.39 source. Behavioral extensions are added as dedicated modules where possible (rather than scattered diffs across the upstream codebase) to keep changes inspectable as a coherent unit.

**Python Driver.** Lives in this repository (`provandal/doppelganger`) under `doppelganger/driver/`. The Driver's job is to drive the C++ simulator: it compiles topology and scenario declarations into the substrate's text configuration files, invokes the simulator binary as a subprocess, parses the substrate's output trace files (`fct.txt`, `intended.txt`, `pfc.txt`, `ecn.txt`, `counters.txt`, `host_counters.txt`), and exposes the parsed data through plain-Python methods. The Driver is testable in isolation (no MCP scaffolding required), usable from a REPL for ad-hoc investigation, reusable by sibling consumers — notably ProtoViz (§7) — and idempotent on `run_id` (§6.4).

**Python Adapter (the MCP server).** Lives in this repository under `doppelganger/adapter/`. The Adapter is a thin MCP server that imports the Driver and registers seven MCP tools (§2.2) that delegate to Driver methods. The Adapter implements the response envelope (§2.3), the leak-prevention rules of §6.5, and (when shipped) the subscription primitives (§2.4); it does not know NS-3.

The two boundaries:

- **C++ ↔ Python.** Text artifacts: configuration files going in, trace files coming out. No live binding, no in-process integration, no shared memory, no Python bindings (the substrate's `./waf configure` runs with `--disable-python`). This is the simplest, most portable, most field-tested approach. It is also the boundary that keeps Doppelgänger's Apache-2.0 source from becoming a derivative work of NS-3 (see §9.5).
- **Driver ↔ Adapter.** In-process Python: standard `import` and method calls. The split exists for testability, separation of concerns (substrate semantics versus protocol/transport), and reusability of the Driver across non-MCP consumers (ProtoViz, command-line debugging, future tooling).

### 9.2 The NS-3 Substrate: provandal/ns3-datacenter (forked from inet-tub/ns3-datacenter)

Doppelgänger uses [`inet-tub/ns3-datacenter`](https://github.com/inet-tub/ns3-datacenter) as its NS-3 substrate, mirrored at [`provandal/ns3-datacenter`](https://github.com/provandal/ns3-datacenter) and pinned at commit **`1a7b9d0`** as of 2026-05-12. The fork-spike memo at `spike/decision_memo.md` documents the original 2026-05-02 fork decision; v0.3 advances the pin through the substrate-side work that Stages 3, 5a, and 5b required:

| Commit | Date | Purpose |
|---|---|---|
| `4dd55d8` | 2026-05-02 | Spike-validated fork HEAD (inherited from inet-tub upstream). |
| `bff3b9c` | 2026-05-05 | Fix three trace-output gaps in `powertcp-evaluation-burst`. |
| `9881be1` | 2026-05-05 | Drop surprise `argv[2]` suffix on `TRACE_OUTPUT_FILE` filename. |
| `da095c7` | 2026-05-07 | Add ECN-mark trace source and `ecn.txt` counter emission. |
| `640ea8d` | 2026-05-08 | Add per-port counter rollup emission (`counters.txt`). |
| `5f2ff4f` | 2026-05-10 | SONiC-shape per-(switch, port, queue) counter rollup + per-priority PFC trace + PG watermarks. |
| `f004e9e` | 2026-05-12 | Add `intended.txt` emission for incomplete-flow surfacing. |
| `1a7b9d0` | 2026-05-12 | Add host-ingress `PhyRxDrop` instrumentation. |

`inet-tub/ns3-datacenter` is a TU Berlin Internet Network Architectures research codebase built on NS-3.39, including the full `ns3-rdma` and HPCC heritage plus PowerTCP, ABM, Reverie, and Credence buffer-management additions. NS-3.39 is 22 versions newer than the alibaba-edu HPCC base; it is post-CMake-transition (the script-named `./waf` in this codebase is a thin CMake wrapper, not the historical Waf build system), gcc-9-or-later compatible, Python-3-only, and free of the documented compile failures that affect alibaba-edu/HPCC on modern toolchains. The trade is a less-cited code base. HPCC remains the de facto reference for the field, but its base codebase has unfixed compile errors against post-2020 toolchains and was last meaningfully maintained around 2019. Inet-tub is a current research codebase (NSDI 2022, SIGCOMM 2022, NSDI 2024) — it trades citation count for build-current-ness, and is the right choice for an implementation that has to actually run.

Other candidates considered and rejected: `alibaba-edu/High-Precision-Congestion-Control` (kept as documented backup; not used because of the modern-toolchain compile failures noted above); `conweave-project/conweave-ns3` (out of scope — its scope is load-balancing-with-in-network-reordering, not a general RDMA simulator).

Two implementation details surfaced by the fork spike that downstream Doppelgänger code (Driver, scenarios, build glue) and reader-facing documentation must respect:

- **RDMA stack location.** The RDMA stack lives in `src/point-to-point/`, not a separate `src/rdma/` module as a naive grep would suggest. Engineers reading the substrate codebase for the first time should be told this; the `src/rdma/` they expect does not exist, and an early "the simulator is broken" reaction is otherwise predictable.
- **No "Waf" terminology.** The build script is named `./waf` for backward compatibility but is a CMake wrapper. Reader-facing documentation, commit messages, and code comments should refer to "the CMake build" or "the build system," not "Waf." The community migrated past Waf around NS-3.36; persisting Waf terminology in v0.3 would be reader-facing wrong.

The implementer should expect to:

- **Use the pinned fork.** Doppelgänger's Dockerfile clones from `provandal/ns3-datacenter` at the SHA above (`1a7b9d0` as of v0.3). Subsequent commits to the upstream `inet-tub` repository or to our `provandal` fork can be re-pinned only after a re-validation run (the §6.4 session cache catches an incomplete re-run; the eval scorer catches a behaviorally-changed re-run).
- **Add C++ extensions in the substrate fork, not in this repository.** Any C++ extensions (new behavioral models, custom switch configurations, additional metrics emission) belong in `provandal/ns3-datacenter` and are committed there as separate modules where possible. C++ extensions do not live in `provandal/doppelganger`. This keeps the GPL-2.0 / Apache-2.0 license boundary clean (see §9.5).
- **Write the Driver and Adapter (§9.1) in this repository.** The Driver compiles topology declarations into the substrate's text configuration format, invokes the simulator as a subprocess, and parses the substrate's six output trace files. The Adapter is a thin MCP shell that registers the seven tools of §2.2 and delegates to the Driver. Both are Python; both are Apache-2.0.
- **Build a Docker image** that packages the simulator and its build dependencies (Ubuntu 22.04 base; gcc, cmake, Python 3, libsqlite3, libxml2, libgsl, libboost, etc.). The image is the project's "genuinely cloneable" deliverable; setup discipline is in §9.3.
- **Drop the no-op `--disable-modules` flag** from the substrate's `./waf configure` invocation. The flag is silently ignored in modern NS-3 (modern equivalent is `-DNS3_ENABLED_MODULES`).

### 9.3 Stage 0 Setup

Doppelgänger's setup is part of HarnessIT's stage 0 in the build plan. The setup must be:

- **Reproducible from a single command.** `docker build -t doppelganger -f Dockerfile .` (or equivalent) is sufficient. No external account, no API key, no registry pull. Cold-cache build of the inet-tub-fork-based image is approximately 5 minutes wall-clock; image size is approximately 1.23 GB. The 30-minute clone-to-run target is therefore comfortably achievable from the Dockerfile alone.
- **Self-contained.** No registry pull required; the Dockerfile clones the substrate (`provandal/ns3-datacenter` at the pinned SHA from §9.2) at build time. A pre-built image published to a registry (Docker Hub or GHCR) is a polish item, not a contract; it reduces the 5-minute build to a 30-second pull but is not required for the "genuinely cloneable" promise.
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

## 10. Open Questions and v0.4 Substrate Work

The following are deliberately not specified in v0.3. The implementer or future iterations will resolve them.

**Link-flap injection (substrate gap).** §5.2 lists link flap as a designed-for failure class; the substrate currently exposes no mechanism to schedule `NetDevice::SetDown` / `SetUp` at simulation times. Closure path: a `LINK_FLAP_SCHEDULE` config knob in the substrate's `powertcp-evaluation-burst` example, parsed into a list of `(node_a, node_b, down_time, up_time)` tuples, each emitting `Simulator::Schedule` calls against the NetDevice. v0.4 substrate task.

**Buffer-misconfiguration injection (substrate gap).** §5.2 lists buffer misconfig as a designed-for failure class; the substrate currently uses a global buffer-size constant with no per-switch override. Closure path: a `BUFFER_SIZE_OVERRIDE` config knob accepting `(switch_id, size_bytes)` tuples that override the global default for the named switch. v0.4 substrate task.

**Silent-drops "drops per million" counter.** The silent-drops scenario currently surfaces drops via host PHY-rx counts. A derived "drops per million packets" rate field would let the agent distinguish "elevated but normal" from "anomalous" drop rates without re-deriving the ratio every time. Closure path: either substrate-side (compute and emit in `host_counters.txt`) or Doppelgänger-side (compute in the Driver from observed tx counts). v0.4 polish task.

**Trace file format choice.** NS-3 supports several trace formats. Which format Doppelgänger emits to disk affects parser complexity and ProtoViz compatibility. The implementer makes this call based on what works best for both consumers. *Note from spike:* the substrate's `mix.tr` and `qlen.txt` files were empty in the spike runs despite `ENABLE_TRACE 1`. `qlen.txt` was a documented config bug (`QLEN_MON_START` set past `SIMULATOR_STOP_TIME`); `mix.tr` may need code-level trace hooks if a future scenario requires per-packet traces. v0.3 does not need either file.

**Scenario versioning.** Scenarios will evolve. How they are versioned, whether eval results from old scenario versions are preserved, how scenarios deprecate — all of this is left to the implementer.

**Cross-session simulation caching.** v0.3 caches at the session level (§6.4). A persistent cross-session cache — recognizing that the same `(scenario_name, scenario_version, seed)` tuple should produce the same trace regardless of session — is a real optimization for repeated eval runs. Whether v0.4 includes it or defers it is the implementer's call.

**Multi-scenario composition.** The agent might want to investigate scenarios that were not authored in advance — for example, taking an existing scenario and extending it with an additional failure injection. v0.3 does not commit to supporting this; later versions may.

**Largest topology runnable in <5 minutes wall-clock.** The fork spike ran a 256-node leaf-spine topology in 3.6 seconds wall-clock for 0.2 seconds simulated. Empirical numbers for the v0.3 §3.1 "Medium" (128-host) and "Large" (512-host) reference topologies, at the longer simulated durations real eval scenarios will use, are not yet established.

**Algorithm-selection contract.** The substrate exposes both an `--algorithm=N` command-line override and a `CC_MODE` config-file setting. Their interaction (precedence, validity ranges, what happens if they disagree) is not documented in the substrate.

**AI-fabric-specific gap closure.** §3.3 enumerates several AI-training-specific behaviors v0.3 does not model (NCCL collectives, GPUDirect, multi-rail, adaptive routing, app-layer-scheduled traffic, etc.). Whether any of these become in scope for v0.4 or beyond is determined by which scenarios the published series finds itself wanting to demonstrate.

*Resolved from v0.2's open-question list:* "Specific NS-3 version" remains resolved (see §9.2). "Failure-class to config-knob inventory" is partly resolved: five of seven classes from §5.2 have working substrate paths; the two outstanding gaps are named explicitly above. "Simulation caching" is partly resolved: session-level caching is shipped (§6.4); cross-session is still open.

---

## 11. Closing

Doppelgänger v0.3 closes the substrate-side gap between v0.2's interface contract and what an agent investigating a §5.2 failure class actually needs. The seven tools in §2.2 are what the eval work surfaced as load-bearing; the host-counters surface in §4.5 is what made the silent-drops fault class non-vacuous; the session-level cache in §6.4 is what made running four-tool sweeps tractable. Two failure classes (link flap, buffer misconfig) remain substrate gaps; closure is named in §10.

The choice to use NS-3 with the inet-tub fork as the substrate, rather than building a behavioral simulator, continues to pay off: every substrate-side commit between v0.2 and v0.3 added a surface that already existed inside NS-3's mechanism — we exposed it; we did not have to invent it.

Inherit the simulation. Expose the surfaces the agent needs. Annotate what is and is not modeled. Let the agent investigate.
