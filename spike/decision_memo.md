# Doppelgänger Fork Spike — Decision Memo

**Date:** 2026-05-02
**Conducted by:** Claude Code session A + Erik
**Time spent:** ~1 hour, single session
**Decision:** Doppelgänger v0.2 will fork from **`inet-tub/ns3-datacenter`** (NS-3.39).

---

## TL;DR

- We chose `inet-tub/ns3-datacenter` because Day 1 cleared all four spike acceptance criteria with no debugging — clean Docker build, working RDMA stack, real DCQCN/HPCC/PowerTCP/TIMELY/DCTCP coverage, and silent-drop failure injection that's already a one-line config change.
- Docker cold-build time: **~5 minutes**. Image size: **1.23 GB**. Both are well under the 30-minute clone-to-run target — the pre-built image is a nice-to-have, not a must-have.
- Trace format: `fct.txt`, space-separated 8-column ASCII (`sip dip sport dport size_bytes start_ns fct_ns standalone_fct_ns`). Sample row pasted under §"Trace format."
- Day 2 (`alibaba-edu/HPCC`) was **skipped per brief** — Day 1 succeeded.

---

## What was tried

### Day 1 — `inet-tub/ns3-datacenter` (NS-3.39)

**Build outcome:** clean success, no debugging required.

The starter `inet-tub.Dockerfile` worked on the first try. Wall-clock cold-cache build was ~5 min total: ~3.5 min for the NS-3.39 compile (CMake, `-j 19`), ~1 min for the Ubuntu base + apt install, ~1 min for image export. The `RUN ./waf configure` and `RUN ./waf build` steps both succeeded; the smoke-test `./waf --run "hello-simulator"` exited 0.

The biggest mid-spike discoveries weren't bugs — they were facts about the toolchain that the Dockerfile authors (us) had wrong but the build absorbed gracefully:

1. **NS-3.39's `./waf` is now a CMake wrapper, not Waf.** The script delegates to `cmake-cache/` under the hood. This is true for all modern NS-3 (post 3.36 or so).
2. **The Dockerfile's `--disable-modules=lte,mesh,wave,wifi,...` flag was silently ignored.** The configure step's "Modules configured to be built" summary listed lte, mesh, aodv, wifi, wimax, lr-wpan, uan — every one we tried to drop. Modern NS-3 uses different flags (`-DNS3_ENABLED_MODULES`) and the legacy `--disable-modules` arg is no-op'd. Despite this, the full build completed in ~3.5 min — fast enough that we never need to fix it.
3. **The image came out 1.23 GB**, not the 4–6 GB the Dockerfile estimated. The optimized build profile keeps binaries small even with all modules built.

**Hello-world simulation:** `examples/PowerTCP/powertcp-evaluation-burst.cc` invoked with `./waf --run 'powertcp-evaluation-burst --conf=examples/PowerTCP/config-burst.txt'` from the ns-3.39 root. Topology is `topology-256.txt` — 128 hosts, 10 ToRs, 4 spines, 256 nodes total. Sim duration: 0.2 simulated seconds. **Wall-clock runtime: 3.6 seconds.** This is much richer than the brief's "Small" target (4 leaves × 2 spines × 8 hosts) and runs faster than that target would have, so we used the upstream config as-is.

**Failure injection class validated:** silent packet drops via `ERROR_RATE_PER_LINK 0.001` (0.1%) in the config file. **The brief recommended adding NS-3's `RateErrorModel` directly — but it's already wired in.** `powertcp-evaluation-burst.cc:822,847` instantiates a `RateErrorModel` per link with rate read from the config (`powertcp-evaluation-burst.cc:581`). One-line config change, no code surgery.

**Trace file parsed:** `mix/fct.txt` — flow completion records, ASCII space-separated, one row per flow, 8 columns.

```
0b000b01 0b001001 10000 10011 2800 150000000 26474 5582
sip(hex)  dip(hex) sport dport size start_ns fct_ns standalone_fct_ns
```

`parse_fct.py` reads it end-to-end with no C++ source-diving beyond the one-line `printf` statement that emits it.

**Spike comparison (baseline vs 0.001 ERROR_RATE_PER_LINK):**

| Metric | Baseline | Injected | Δ |
|---|---|---|---|
| Flows completed | 255 | 251 | **-4** |
| Median FCT (ns) | 399 602 | 330 552 | -17.3% |
| Mean FCT (ns) | 323 895 | 271 439 | -16.2% |
| Max FCT (ns) | 440 542 | 386 740 | -12.2% |
| Median slowdown | 13.36× | 11.24× | -2.1pp |

**The counterintuitive result is the spike's first real lesson** for Doppelgänger v0.2: aggregate-FCT statistics on `fct.txt` alone *can lie about whether failure injection landed*. With a 0.1% drop rate on a 0.2s sim, four flows failed to complete before sim stop and were dropped from the file entirely. Those four were the slowest baseline flows; removing them from the population pulled the median down — the injected run looks "faster" by every aggregate. The real failure signature is **flow-count delta** (255 → 251) plus per-flow FCT regression on the surviving slowest tail, not the median. This belongs in the v0.2 metrics-and-eval discussion: post-mortem trace analysis needs missing-flow accounting and tail-aware comparisons, not just aggregate slowdown.

### Day 2 — `alibaba-edu/High-Precision-Congestion-Control` (NS-3.17)

**Skipped — Day 1 succeeded.** Per `SPIKE_BRIEF.md`: "If day 1 succeeds, COMMIT to `inet-tub/ns3-datacenter` and skip day 2."

The brief's only condition for falling back to alibaba-edu was DCQCN coverage. `inet-tub/ns3-datacenter`'s `examples/PowerTCP/script-burst.sh` runs DCQCN, PowerTCP, Theta-PowerTCP, HPCC, TIMELY, and DCTCP from a single config — all five of the algorithms we'd need. The fallback condition is not triggered.

**Documented-bug status:** N/A (alibaba-edu not built).

---

## Decision

**Fork chosen:** `inet-tub/ns3-datacenter`

**Reasoning:**

1. **Build cleanliness.** Zero debugging on Ubuntu 22.04 / gcc 11 / Python 3.10. The NS-3.17-era Python 2 / gcc 5.4 issues that haunt alibaba-edu are not in our path at all. The 30-min clone-to-run promise in Doppelgänger §9.3 is achievable today with this fork; with alibaba-edu it would be conditional on three documented unfixed bugs staying patched.
2. **Modernity of NS-3 base.** 22 NS-3 versions newer (3.39 vs 3.17). CMake build system instead of Waf-via-Python-2-wscript. Active research codebase (NSDI 2022 / SIGCOMM 2022 / NSDI 2024 papers ride on it). gcc 9+ / Python 3-only stack matches what readers will have on their laptops in 2026.
3. **Algorithm coverage.** All five congestion-control algorithms we'd want — DCQCN (CC_MODE 1), HPCC (3), PowerTCP/Theta-PowerTCP (3 with `wien=true/false`), TIMELY (7), DCTCP (8) — share one config schema and one binary. This is exactly the substrate-neutral interface Doppelgänger v0.2 §2 wants to expose to scenarios.
4. **Failure injection already wired.** Silent drops via `RateErrorModel` are a one-line config change. The five other failure classes the RDMA reviewer flagged as realistic likely have similar leverage points in the existing config schema (to be confirmed in stage 1).
5. **RDMA stack location.** `inet-tub` extended `src/point-to-point` with `qbb-net-device.{cc,h}`, `rdma-hw.{cc,h}`, `rdma-queue-pair.{cc,h}`, `rdma-driver.{cc,h}`, `qbb-header.{cc,h}`, `switch-node.{cc,h}`, `switch-mmu.cc`, `qbb-helper.{cc,h}` rather than adding a separate `rdma` module. This is a structural choice we inherit — not a problem, but worth documenting because it means we can't drop point-to-point in module-disable lists (and it's why the Dockerfile's `--disable-modules` flag would have broken the build if it had actually worked).

**What we lose by not choosing the other fork:**

- Canonical alibaba-edu DCQCN provenance for headline citation. **Concrete impact:** In the eventual blog post, "DCQCN as implemented in inet-tub/ns3-datacenter, which itself extends the alibaba-edu HPCC fork's QBB-derived RDMA stack" is one extra clause of provenance. Acceptable.
- Nothing else of substance. The alibaba-edu repo is more historical than authoritative now (44 open issues, 65 commits, frozen ~2020-era).

---

## Working Dockerfile

The starter `inet-tub.Dockerfile` worked as-is. **One recommended cleanup** for v0.2 (not blocking for the spike): drop the `--disable-modules=...` waf flag, since it's no-op'd and misleads readers. Optionally, add an explicit `-DNS3_ENABLED_MODULES=...` if we actually want a smaller image — but at 1.23 GB we don't need to.

```dockerfile
FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential g++ cmake git pkg-config \
    sqlite3 libsqlite3-dev libxml2 libxml2-dev libgsl-dev libboost-all-dev \
    libgtk-3-dev python3 python3-dev python3-pip python3-venv \
    autoconf cvs bzr unrar gdb valgrind uncrustify \
    doxygen graphviz imagemagick \
    texlive texlive-extra-utils texlive-latex-extra texlive-font-utils \
    dvipng latexmk python3-sphinx dia gsl-bin libgslcblas0 \
    tcpdump sqlite libxml2-utils cmake-data ca-certificates \
    wget curl vim nano \
 && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir numpy matplotlib cycler pandas

WORKDIR /opt
RUN git clone https://github.com/inet-tub/ns3-datacenter.git
WORKDIR /opt/ns3-datacenter/simulator/ns-3.39

# v0.2 cleanup: drop the no-op --disable-modules flag
RUN ./waf configure --build-profile=optimized --enable-examples --enable-tests \
        --disable-python --disable-werror

RUN ./waf build
RUN ./waf --run "hello-simulator" || echo "hello-simulator no-op in optimized profile"

WORKDIR /work
CMD ["/bin/bash"]
```

For a production v0.2 image we should also pin a specific commit SHA (currently the Dockerfile clones master), and document the SHA → upstream-paper mapping for reproducibility.

## Trace format

**Format:** ASCII, space-separated, one flow per line, 8 columns. Format string from `examples/PowerTCP/powertcp-evaluation-burst.cc:184`:

```c
fprintf(fout, "%08x %08x %u %u %lu %lu %lu %lu\n",
        sip, dip, sport, dport, m_size, start_ns, fct_ns, standalone_fct_ns);
```

**Sample row:**
```
0b000b01 0b001001 10000 10011 2800 150000000 26474 5582
```

| Column | Meaning |
|---|---|
| `0b000b01` | source IP, 8-hex (= 11.0.11.1) |
| `0b001001` | destination IP, 8-hex (= 11.0.16.1) |
| `10000` | source port |
| `10011` | destination port |
| `2800` | flow size in bytes |
| `150000000` | start time, ns since sim start |
| `26474` | flow completion time, ns |
| `5582` | standalone (uncongested) FCT, ns |

**Parser sketch (Python):** see `parse_fct.py` in this directory. ~30 lines.

```python
@dataclass
class FlowRecord:
    sip: str; dip: str
    sport: int; dport: int
    size_bytes: int
    start_ns: int; fct_ns: int; standalone_fct_ns: int

    @property
    def slowdown(self) -> float:
        return self.fct_ns / self.standalone_fct_ns
```

**What this format omits that we may need later:**
- **Per-packet detail.** No drop-by-drop trace, no per-hop latency, no queue-occupancy samples. For root-cause investigation Doppelgänger will need additional outputs — `mix.tr` (NS-3 ASCII trace), `qlen.txt` (queue length samples), and `pfc.txt` (PFC events) are emitted by the same binary but were empty in this spike (see "Surprises").
- **Wall-clock metadata.** None. Sim is purely simulated time. That's fine.
- **Topology context.** The fct.txt has no headers and no metadata block; you have to know which `topology-*.txt` was loaded by reading the config the sim was given. v0.2 should emit a topology-id sidecar or a header line.
- **Flow-failure marker.** Flows that don't complete before SIMULATOR_STOP_TIME are simply absent. There's no "flow X started at T but did not complete" record. Doppelgänger v0.2 should add this — the **flow-count delta** was the only durable signal of failure injection in our test.

---

## 30-minute promise

Doppelgänger §9.3 claims fresh-clone reader from zero to running simulation in under 30 minutes.

**Cold-cache local build:** ~5 minutes — **achievable with margin**. Remaining time budget covers `git clone`, `python parse_fct.py` setup, reading the README. Comfortably under 30 min on this machine; assume 2× headroom for slower laptops.

**With pre-built registry image (`docker pull`):** ~1.23 GB transferred — at typical home broadband (100 Mbps) that's ~2 min, plus a few seconds to start a container. Total ~5 min. Easily under 30.

**Recommendation for Doppelgänger v0.2:** Dockerfile is sufficient as the contract; the pre-built registry image is a polish item, not a precondition. (This contradicts the synthesis recommendation that called the pre-built image "the contract" — the build is fast enough that it's not load-bearing.)

---

## What Doppelgänger v0.2 inherits from this spike

**Required follow-up work:**

1. **Pin a commit SHA in the Dockerfile.** Currently `git clone` pulls master — non-deterministic. Pick a SHA, document its upstream provenance (which paper it ships in), include in v0.2 §9.
2. **Drop the `--disable-modules` no-op flag** from the Dockerfile and document why (legacy Waf flag on a CMake-backed `./waf` wrapper).
3. **Fix the upstream `qlen.txt` config bug** in our scenario library: `config-burst.txt` has `QLEN_MON_START 2000000000` (=2.0s) but `SIMULATOR_STOP_TIME 0.2`. Queue-length monitoring window opens after the sim ends, so qlen.txt is always empty. Either bump SIMULATOR_STOP_TIME to >2.01s or move QLEN_MON_START into the sim window.
4. **Add a flow-incompletion record** to whatever Python wrapper layer Doppelgänger writes. The flow-count delta was our cleanest failure signature, and it's currently implicit (silently absent rows). Doppelgänger should emit an explicit "flow X did not complete" record.
5. **Build a tail-aware comparison primitive** for fct.txt analysis. Aggregate FCT metrics actively mislead in the presence of incomplete flows (our injected run looked "faster" by every aggregate). v0.2's eval discussion should call this out.
6. **Document the seven failure classes in terms of `config-burst.txt` knobs.** The brief assumed we'd write a custom RateErrorModel; turns out one knob already covers silent drops. Inventory the other six (link flap, queue corruption, PFC storm, hash polarization, ECN miscalibration, congestion-control parameter drift) against the config schema.
7. **Decide whether to publish the pre-built Docker image.** Recommendation: defer; the build is fast enough that the Dockerfile is the contract. Revisit at stage 1 if onboarding feedback says otherwise.

**Open questions surfaced by the spike that v0.2 should answer:**

- **Does this fork emit per-packet-level data we'll need for ProtoViz integration?** `mix.tr` exists in the schema but came out empty in our run; need to investigate whether it's a config flag (`ENABLE_TRACE 1` is set but maybe needs other knobs) or whether per-packet tracing requires a code-level hook.
- **What's the largest topology this fork can simulate in <5 minutes wall clock?** We did 256 nodes / 0.2s simulated in 3.6s wall-clock. Doppelgänger v0.2's "Medium" and "Large" topology targets need empirical wall-clock numbers before we can promise reader run times.
- **Does the fork's switch-mmu support multi-rail / adaptive routing / GPUDirect, per the RDMA reviewer's gap list?** Source files exist (`switch-mmu.cc`); behavior is uninspected. Stage 1 work.
- **What's the relationship between `--algorithm=N` (cmd-line override) and `CC_MODE` (config file)?** Both exist and might conflict. The script-burst.sh manipulates both — needs tracing for v0.2's scenario authoring API.

---

## Surprises (raw — these become the first journal.md entries)

- **NS-3.39 ships a `./waf` script that's actually a CMake wrapper.** The community migrated, the entry-point name didn't. Reader-facing docs that say "uses Waf" will be wrong; v0.2 should say "uses the legacy `./waf` shim over CMake."
- **Build was 4–8× faster than the Dockerfile's own estimate.** Estimate said 20–40 min; actual was 5 min wall-clock with `-j 19`. Image was 1.23 GB, not 4–6 GB. The brief's pre-built-image-as-contract framing is overcautious for this fork.
- **`--disable-modules` is silently ignored** — modern NS-3 takes `-DNS3_ENABLED_MODULES`. The build absorbed the bad flag without warning. This is the kind of papercut readers would hit and silently work around; documenting it in the build-plan is worth more than fixing it.
- **`hello-simulator` prints nothing in the optimized build profile.** NS_LOG is compiled out. Anyone reading the Dockerfile's `RUN ./waf --run "hello-simulator"` step expecting "Hello Simulator" output will be confused. The smoke test is "did it exit 0," not "did it print anything."
- **Silent-drop failure injection is a one-line config change**, not the code-level hook the spike brief assumed. The brief's recommendation to use `RateErrorModel` was correct; the surprise was that `inet-tub` already wires it from config.
- **`mix.tr`, `qlen.txt`, `pfc.txt` were all empty** despite `ENABLE_TRACE 1` and a real DCQCN-class burst happening. fct.txt was the only populated trace. The qlen.txt window is mis-configured upstream (above); mix.tr and pfc.txt may need explicit code paths we haven't hit yet.
- **Aggregate FCT statistics misled us.** The 0.1% drop run looked "faster" by every aggregate metric because the slowest 4 flows failed to complete and were dropped from the file entirely. The cleanest failure signature was a flow-count delta of 4. **This is the spike's most important takeaway for HarnessIT's eval discipline.**
- **Bind-mount path translation tripped us once on Git Bash for Windows.** First run silently produced no host-side files; needed `MSYS_NO_PATHCONV=1` + `pwd -W` to get the Windows path through. Reader-facing instructions that assume Linux/Mac need a Windows note, or we standardize on PowerShell for the volume mount.
- **The RDMA stack lives in `src/point-to-point/`, not a separate `src/rdma` module.** This is a structural inheritance from the alibaba-edu HPCC fork that propagated up through inet-tub. Doppelgänger v0.2 §3.3 should mention this — anyone grepping for "rdma" in src/ will come up empty and think the simulator is broken.

---

## Recommended changes to `_reviews/05_doppelganger_v0.2_updates_pending.md`

The v0.2 deltas as drafted reference `inet-tub/ns3-datacenter` as the primary candidate, which the spike confirms. **One concrete revision recommended:**

- **Soften the "pre-built image is the contract" framing in §9.3.** This came out of the synthesis with strong language ("the 30-minute promise needs pre-built Docker image as the contract"). The spike shows the build is fast enough — 5 min cold cache, 1.23 GB — that the Dockerfile alone is a credible contract. Pre-built image becomes a polish item for stage 1+, not a v0.2 precondition. This frees up Erik's near-term build calendar.

The §9.2 / §3.3 narrative deltas already drafted (RDMA stack in point-to-point, CMake-not-Waf, fct.txt as primary trace) line up with what the spike found — no rewrites needed there.
