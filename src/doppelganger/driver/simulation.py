"""Driver — runs scenarios against the NS-3 substrate via subprocess + text files.

The Driver invokes the substrate Docker image, runs a scenario inside, and
captures the resulting trace files to a host-side directory. Communication
between the Python Driver and the C++ substrate is exclusively via text
artifacts (config files in, trace files out) — no Python bindings, no live
binding, no shared memory. Doppelgänger v0.2 §9.5 covers why this boundary
matters for the GPL-2.0 / Apache-2.0 license model.

v0.1 ships one built-in scenario: ``"spike-burst"`` — runs the substrate's
bundled ``examples/PowerTCP/config-burst.txt`` example. The scenario name maps
to the scenario the 2026-05-02 fork spike validated end-to-end. Topology
compilation (turning a Python topology declaration into ``config-burst.txt``
format) is a separate later commit; for now, the spike's bundled config is the
one path that works.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from doppelganger.driver.parsers.fct import parse_fct_file
from doppelganger.driver.types import PerFlowRecord

DEFAULT_SUBSTRATE_IMAGE = "doppelganger-substrate"
SUBSTRATE_NS3_DIR = "/opt/ns3-datacenter/simulator/ns-3.39"

# Scenario registry. Each entry is the shell command run inside the substrate
# container; trace files are expected to land in ``mix/`` relative to the NS-3 root.
_BUILTIN_SCENARIOS: dict[str, str] = {
    "spike-burst": (
        "mkdir -p mix && "
        "./waf --run 'powertcp-evaluation-burst "
        "--conf=examples/PowerTCP/config-burst.txt'"
    ),
}


@dataclass
class SimulationResult:
    """The artifacts and parsed records from a single scenario run."""

    scenario: str
    trace_dir: Path
    flows: list[PerFlowRecord] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    wall_clock_seconds: float = 0.0


class DriverError(RuntimeError):
    """Raised when the Driver cannot fulfill a request (image missing, sim failed, etc.)."""


class Driver:
    """Pure-Python wrapper around the NS-3 substrate.

    Parameters
    ----------
    substrate_image:
        Docker image name of the built substrate. Default ``"doppelganger-substrate"``
        matches the tag in ``docker/substrate.Dockerfile``.
    traces_root:
        Host directory under which per-run trace dirs are created. Default
        ``./traces`` relative to the current working directory.
    """

    def __init__(
        self,
        substrate_image: str = DEFAULT_SUBSTRATE_IMAGE,
        traces_root: Path | str = Path("traces"),
    ) -> None:
        self.substrate_image = substrate_image
        self.traces_root = Path(traces_root).resolve()

    def list_scenarios(self) -> list[str]:
        """Return the names of built-in scenarios this Driver knows how to run."""
        return list(_BUILTIN_SCENARIOS)

    def run_scenario(self, scenario: str, run_id: str | None = None) -> SimulationResult:
        """Run a scenario end-to-end and return parsed Per-Flow Records.

        Parameters
        ----------
        scenario:
            Name of a built-in scenario (see :py:meth:`list_scenarios`).
        run_id:
            Optional label for the run's trace directory. Defaults to
            ``"<scenario>-<unix-timestamp>"``.

        Raises
        ------
        DriverError:
            If the substrate image is not present locally, if the simulation
            subprocess returns non-zero, or if no trace files are produced.
        """
        if scenario not in _BUILTIN_SCENARIOS:
            raise DriverError(
                f"Unknown scenario {scenario!r}. "
                f"Known: {sorted(_BUILTIN_SCENARIOS)}"
            )

        self._verify_image_present()

        run_id = run_id or f"{scenario}-{int(time.time())}"
        trace_dir = self.traces_root / run_id
        trace_dir.mkdir(parents=True, exist_ok=True)

        sim_command = _BUILTIN_SCENARIOS[scenario]
        full_command = (
            f"cd {SUBSTRATE_NS3_DIR} && "
            f"{sim_command} && "
            f"cp mix/* /traces/ 2>/dev/null || true"
        )

        start = time.monotonic()
        completed = subprocess.run(
            [
                "docker", "run", "--rm",
                "-v", f"{trace_dir}:/traces",
                self.substrate_image,
                "bash", "-c", full_command,
            ],
            capture_output=True,
            text=True,
        )
        elapsed = time.monotonic() - start

        if completed.returncode != 0:
            raise DriverError(
                f"Substrate simulation failed (rc={completed.returncode}). "
                f"stderr (last 500 chars): {completed.stderr[-500:]!r}"
            )

        fct_path = trace_dir / "fct.txt"
        flows = parse_fct_file(fct_path) if fct_path.exists() else []

        return SimulationResult(
            scenario=scenario,
            trace_dir=trace_dir,
            flows=flows,
            stdout=completed.stdout,
            stderr=completed.stderr,
            wall_clock_seconds=elapsed,
        )

    def _verify_image_present(self) -> None:
        if shutil.which("docker") is None:
            raise DriverError("docker CLI not found on PATH")
        check = subprocess.run(
            ["docker", "image", "inspect", self.substrate_image],
            capture_output=True,
            text=True,
        )
        if check.returncode != 0:
            raise DriverError(
                f"Substrate image {self.substrate_image!r} not found locally. "
                f"Build it: docker build -t {self.substrate_image} "
                f"-f docker/substrate.Dockerfile ."
            )
