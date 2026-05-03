# Doppelgänger Fork Spike — Decision Memo

**Date:** YYYY-MM-DD
**Conducted by:** Claude Code session A + Erik
**Time spent:** X hours over Y days
**Decision:** Doppelgänger v0.2 will fork from `[FORK_NAME]`.

---

## TL;DR (3 bullets)

- We chose `[FORK_NAME]` because [one-sentence reason].
- Docker cold-build time: `[N minutes]`. Image size: `[N GB]`. Both are below / above the 30-minute clone-to-run target — see §"30-minute promise" below.
- The trace format we'll be parsing in Doppelgänger v0.2 is `[FORMAT]` — sample row pasted under §"Trace format."

---

## What was tried

### Day 1 — `inet-tub/ns3-datacenter` (NS-3.39)

**Build outcome:** [success / partial / failure]

[Narrative of what happened. What worked the first time. What broke. What the fix was. How long the cold build took. Image size.]

**Hello-world simulation:** [name of example used, command, runtime]

**Failure injection class validated:** [which one — recommendation was silent drops via RateErrorModel]

**Trace file parsed:** [path, format, sample row]

### Day 2 — `alibaba-edu/High-Precision-Congestion-Control` (NS-3.17) — only if day 1 failed

**Build outcome:** [success / partial / failure]

[Same structure. If day 1 succeeded and we skipped day 2, just write "Skipped — day 1 succeeded." here.]

**Documented-bug status:**
- Issue #4 (Python 3 print): [hit / dodged / workaround applied]
- Issue #6 (CommandLine const): [hit / dodged / workaround applied]
- Issue #8 (operator<< ambiguity): [hit / dodged / workaround applied]

---

## Decision

**Fork chosen:** `[FORK_NAME]`

**Reasoning:**
- [Reason 1 — likely about build cleanliness]
- [Reason 2 — likely about NS-3 base modernity]
- [Reason 3 — likely about what coverage we need from the fork]

**What we lose by not choosing the other fork:**
- [If chose inet-tub, we lose canonical alibaba-edu DCQCN provenance — concrete impact?]
- [If chose alibaba-edu, we lose 22 NS-3 versions of tooling improvements + PowerTCP/ABM — concrete impact?]

---

## Working Dockerfile

```dockerfile
[Paste the actual Dockerfile that worked, including any post-spike adjustments to the starter scaffolding]
```

## Trace format

**Format:** [text / binary, line-based / structured, etc.]

**Sample row:**
```
[paste a real row from the simulation output]
```

**Parser sketch (Python):**
```python
[a few lines showing how to parse one row into a structured record]
```

**What this format omits that we may need later:**
- [e.g., per-packet detail not captured at this granularity]
- [e.g., wall-clock metadata included but not useful for our purposes]

---

## 30-minute promise

Doppelgänger §9.3 claims fresh-clone reader from zero to running simulation in under 30 minutes.

**Cold-cache local build:** `[N minutes]` — [achievable / not achievable]

**With pre-built registry image (`docker pull`):** `[N minutes]` estimated — [achievable / not achievable]

**Recommendation for Doppelgänger v0.2:** [pre-built image required / Dockerfile sufficient / commitment must be padded to N minutes]

---

## What Doppelgänger v0.2 inherits from this spike

**Required follow-up work:**
- [e.g., write a parser for the [FORMAT] trace file format]
- [e.g., add a custom NS-3 module for X behavior the chosen fork doesn't have]
- [e.g., resolve a build-time-flag tradeoff between optimization profile and debug observability]

**Open questions surfaced by the spike that v0.2 should answer:**
- [e.g., does the chosen fork emit per-packet data we'll need for ProtoViz integration?]
- [e.g., what's the largest topology the chosen fork can simulate in <5 minutes wall clock?]

---

## Surprises (raw — these become the first journal.md entries)

- [Things that surprised you about the build, the docs, the NS-3 toolchain, the chosen fork's quirks]
- [Anything that's worth remembering when we eventually write the Doppelgänger blog post]

---

## Recommended changes to `_reviews/05_doppelganger_v0.2_updates_pending.md`

[If the spike surfaced anything that should change the §9.2 / §3.3 deltas already drafted in 05_doppelganger_v0.2_updates_pending.md, name it here. If nothing, write "No changes — the v0.2 deltas as drafted are correct."]
