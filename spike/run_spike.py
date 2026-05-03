"""
Doppelgänger fork spike — Python sim runner + trace parser.

This is a STARTING POINT. The spike's job is to validate that:
  1. The chosen NS-3 fork builds in Docker
  2. A simple simulation runs end-to-end
  3. We can parse one trace file format in Python without C++ source-diving

This script handles steps 2 and 3. Step 1 is in the Dockerfile.

Run from the host (not inside the container):
  python run_spike.py inet-tub
  python run_spike.py alibaba-edu-hpcc

The script will:
  - docker run the candidate image
  - exec the simulation example inside the container
  - copy the resulting trace file out to ./traces/
  - parse one row of it and print the structure

Edit FORK_CONFIG below as you discover what the actual sim entry point and
trace file location are inside each container. The current values are educated
guesses; the spike's first job is to find the right ones.
"""

import sys
import subprocess
import os
import re
from pathlib import Path

# Spike configuration per fork. Update these as you discover the truth inside each container.
FORK_CONFIG = {
    "inet-tub": {
        "image": "doppelganger-spike-inet-tub",
        "ns3_dir": "/opt/ns3-datacenter/simulator/ns-3.39",
        # The actual binary is powertcp-evaluation-burst; it reads a config file via --conf=
        # The upstream config-burst.txt expects to be run from ns-3.39 root and writes to ./mix/
        "sim_command": "mkdir -p mix && ./waf --run 'powertcp-evaluation-burst --conf=examples/PowerTCP/config-burst.txt'",
        # Trace files land in mix/ relative to ns-3.39 root; fct.txt is the populated one
        "trace_glob": "/opt/ns3-datacenter/simulator/ns-3.39/mix/*",
        # Per powertcp-evaluation-burst.cc:184 — space-separated, 8 columns
        "trace_format_hint": "fct.txt: sip(hex8) dip(hex8) sport dport size_bytes start_ns fct_ns standalone_fct_ns",
    },
    "alibaba-edu-hpcc": {
        "image": "doppelganger-spike-hpcc",
        "ns3_dir": "/opt/hpcc/simulation",
        # HPCC scratch programs live in scratch/; the canonical small example is third.cc
        # (you may need to find or write a hello-world that uses RDMA + a small topology)
        "sim_command": "./waf --run 'scratch/third'",
        "trace_glob": "/opt/hpcc/simulation/*.tr",
        "trace_format_hint": "HPCC ASCII trace: per-event lines with timestamp, node, action",
    },
}


def run_in_container(image: str, command: str) -> tuple[int, str, str]:
    """Run a shell command inside a fresh container of `image`. Returns (returncode, stdout, stderr)."""
    full_command = ["docker", "run", "--rm", "-v", f"{os.getcwd()}/traces:/traces", image, "bash", "-c", command]
    print(f"[run] {' '.join(full_command)}")
    result = subprocess.run(full_command, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


def main(fork_key: str):
    if fork_key not in FORK_CONFIG:
        print(f"Unknown fork '{fork_key}'. Choices: {list(FORK_CONFIG.keys())}")
        sys.exit(1)

    cfg = FORK_CONFIG[fork_key]
    print(f"\n=== Spike: {fork_key} ===")
    print(f"Image: {cfg['image']}")
    print(f"NS-3 dir inside container: {cfg['ns3_dir']}")
    print(f"Simulation command: {cfg['sim_command']}")
    print(f"Trace format hint: {cfg['trace_format_hint']}\n")

    Path("traces").mkdir(exist_ok=True)

    # Step 1: verify the image exists
    rc, _, _ = subprocess.run(["docker", "image", "inspect", cfg["image"]], capture_output=True).returncode, "", ""
    inspect = subprocess.run(["docker", "image", "inspect", cfg["image"]], capture_output=True, text=True)
    if inspect.returncode != 0:
        print(f"Image {cfg['image']} not found locally. Build it first:")
        print(f"  docker build -t {cfg['image']} -f {fork_key}.Dockerfile .")
        sys.exit(1)
    print(f"[ok] Image {cfg['image']} exists")

    # Step 2: run the simulation inside the container, copy any trace files to /traces
    sim_command = (
        f"cd {cfg['ns3_dir']} && "
        f"{cfg['sim_command']} && "
        f"echo '--- looking for traces ---' && "
        f"ls {cfg['trace_glob']} 2>/dev/null || echo 'no traces matched glob' && "
        f"cp {cfg['trace_glob']} /traces/ 2>/dev/null || true"
    )
    rc, out, err = run_in_container(cfg["image"], sim_command)
    print(f"\n=== Simulation stdout ===\n{out}\n=== Simulation stderr ===\n{err}\n")
    if rc != 0:
        print(f"[FAIL] Simulation returned {rc}. Investigate above.")
        print("Common things to try:")
        print("  - Drop into the container manually: docker run -it --rm <image> bash")
        print("  - Find an actual existing example: ls <ns3_dir>/examples/")
        print("  - Read the upstream repo's README for the canonical hello-world entry point")
        sys.exit(1)

    # Step 3: list captured traces
    trace_files = list(Path("traces").iterdir())
    if not trace_files:
        print("[WARN] No trace files captured. Update FORK_CONFIG['trace_glob'] in run_spike.py")
        print("       and re-run after you find where the simulation actually writes its output.")
        sys.exit(2)

    print(f"\n=== Captured {len(trace_files)} trace file(s) ===")
    for tf in trace_files:
        print(f"  {tf} ({tf.stat().st_size} bytes)")

    # Step 4: parse the first trace file and show its structure
    first = trace_files[0]
    print(f"\n=== First 20 lines of {first.name} ===")
    with first.open() as f:
        for i, line in enumerate(f):
            if i >= 20:
                break
            print(f"  {line.rstrip()}")

    print(f"\n[OK] Spike succeeded for {fork_key}.")
    print(f"     Trace file: {first}")
    print(f"     Now: write a real parser for this format, then add a RateErrorModel for silent drops.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])
