# Doppelgänger Fork Spike — Session Brief

**Working directory:** `C:\Users\gid_f\OneDrive\Projects\AI Related\HarnessIT\_reviews\doppelganger\`
**Time budget:** 2 days
**Output artifact:** `decision_memo.md` committing Doppelgänger v0.2 to a single NS-3 fork.

## What this is

Doppelgänger is the simulated RoCE fabric that HarnessIT will investigate. It's a Python scenario authoring layer over NS-3 with RDMA extensions inherited from an HPCC-derived fork. The choice of which HPCC-derived fork drives the next two years of build, maintenance, and reader-onboarding pain — so we're spiking it before committing.

Read for full context (top to bottom):
- `../../NEXT_STEPS.md` — overall project status
- `../00_synthesis.md` §6 Priority 3 items 17–18 — what this spike is supposed to settle
- `../05_doppelganger_v0.2_updates_pending.md` — the v0.2 doc narrative this spike feeds into
- `../03_rdma_ns3_expert.md` — RDMA/NS-3 expertise from the four-reviewer pass

## The candidates (research already done — see `../05_doppelganger_v0.2_updates_pending.md`)

**Primary: `inet-tub/ns3-datacenter`**
- NS-3.39 base, Waf build, ~210 commits, active research codebase through 2024 (NSDI 2022, SIGCOMM 2022, NSDI 2024 papers ride on it)
- Inherits `ns3-rdma` + HPCC, adds PowerTCP, ABM, Reverie, Credence buffer-management algorithms
- Supports RDMA + TCP/IP stacks simultaneously
- 22 NS-3 versions newer than alibaba-edu/HPCC; gcc 9+ supported, Python 3-only

**Backup: `alibaba-edu/High-Precision-Congestion-Control`**
- NS-3.17 base, Waf build, ~65 commits, 44 open issues
- Canonical DCQCN reference but with documented unfixed compile failures:
  - Issue #4: Python 3 print-statement syntax error in `wscript`
  - Issue #6: `CommandLine` const-qualifier error on Ubuntu 16.04 / gcc 5.4
  - Issue #8: `operator<<` ambiguity on ns-3.30.1 / Ubuntu 20.04
- Falls back here only if `inet-tub/ns3-datacenter` lacks DCQCN coverage we need

**Explicitly NOT a candidate: `conweave-project/conweave-ns3`** — older NS-3 (3.19), narrower scope (load balancing only), 17 commits. Don't waste time on it.

## Acceptance criteria

The spike succeeds when ALL of these are true for one fork:

1. **Clean Docker build** on Ubuntu 22.04 base, without disabling `-Werror`, without backporting Python 2.
2. **Hello-world simulation runs** on a small leaf-spine topology (4 leaves, 2 spines, 8 hosts/leaf is fine — match the v0.1 "Small" topology).
3. **One failure-injection class works end-to-end.** Recommendation: silent packet drops via NS-3's `RateErrorModel` on a `PointToPointNetDevice`. This is the cleanest of the seven failure classes per the RDMA expert review (no module-level changes needed).
4. **One trace file format readable in Python** without C++ source-diving. The flow-completion-time output or queue-depth output is ideal — text-format, structured, sufficient for "did the failure manifest in the trace."

The spike does NOT need to validate:
- All seven failure injection classes (one is enough)
- All three reference topologies (Small only)
- HPCC-PINT or PowerTCP correctness (out of scope)
- Long-running simulations (30 seconds is plenty)
- MCP tool surface (this is the simulator layer, not the harness layer)
- DCQCN parameter sweeps (out of scope)

## Suggested sequence

### Day 1: `inet-tub/ns3-datacenter`

1. Build the Docker image from `inet-tub.Dockerfile` (starter scaffolding already provided — expect to debug).
2. Inside the container, run one of the upstream examples (`simulator/ns-3.39/examples/PowerTCP/` is mentioned in their README; try the simplest one).
3. If that works, modify it to a small leaf-spine topology (or use whatever upstream small-scale example exists).
4. Add a `RateErrorModel` to one egress port at 0.1% drop rate. Run for 30 simulated seconds with one steady flow.
5. Parse the output trace file in Python (use `run_spike.py` as a starting point). Confirm the drops show up.

**If day 1 succeeds, COMMIT to `inet-tub/ns3-datacenter` and skip day 2.** Use day 2 to start sketching Doppelgänger's Python wrapper layer instead.

**If day 1 fails after a reasonable debug effort (4–6 hours of build/runtime issues), document the failure mode and proceed to day 2.**

### Day 2: `alibaba-edu/High-Precision-Congestion-Control` (backup)

1. Build from `alibaba-edu-hpcc.Dockerfile` (workarounds for issues #4, #6, #8 are pre-applied — verify they still work).
2. Repeat the day 1 exercise: small topology, silent drops, parse one trace.
3. **Time-box the workarounds at 4 hours.** If you can't get a clean simulation in 4 hours, HPCC is too painful and we either fall back to `shellqiqi/HPCC` (less-known fork that may be cleaner) or accept that the project starts with NS-3.19-class build pain.

## How to write the decision memo

Use `decision_memo_TEMPLATE.md` as the structure. It should answer:

- Which fork? (`inet-tub/ns3-datacenter` or `alibaba-edu/HPCC`)
- What worked, what didn't, what was the build time on cold-cache?
- What's the Dockerfile that actually worked? (paste it in)
- What trace output format are we going to parse, and what does a sample row look like?
- What surprised you? (this becomes a Surprise entry in the eventual journal.md)
- What's the recommended size of the pre-built image for `docker pull` distribution?
- What follow-up work does Doppelgänger v0.2 inherit? (e.g., "we'll need to write a parser for X format," "we'll need to add Y custom module")

## Constraints

- **Erik's stack:** Python-comfortable, less native in C++. If a problem requires deep NS-3 C++ surgery, flag it loudly rather than patching it; we may want to pick the other fork instead.
- **Reproducibility is the currency.** A working build that requires 6 manual steps and gcc 7.5 specifically is worse than a working build that requires only `docker build`. The 30-minute clone-to-run promise in Doppelgänger §9.3 rests on a clean Dockerfile.
- **Avoid scope creep.** This is a fork-validation spike, not a Doppelgänger v0.1 build. Resist the temptation to start writing the Python scenario layer or the MCP tools. That's stage 1.
- **Capture as you go.** Every surprise gets a one-line note in the eventual `journal.md` (which doesn't exist yet but will after Session B's capture-protocol decision). For now, jot them at the bottom of `decision_memo.md` under a "Surprises" heading.

## When you're done

The decision memo is the artifact. It commits Doppelgänger v0.2 to a fork. The Dockerfile that worked is the second artifact — it's the basis for the eventual published image. Hand both back to Erik; he and Session B will fold them into Doppelgänger v0.2 and Architecture v0.5.

## Tools you'll likely need

- Docker Desktop (Erik already has it; verify it's running before starting)
- A Linux terminal in the container — `docker exec -it <container> bash` is fine
- Python 3.10+ on the host for running `run_spike.py`
- Disk space: NS-3.39 build artifacts can hit 5–10 GB; HPCC's NS-3.17 build is ~3–5 GB. Confirm 30+ GB free before starting.
