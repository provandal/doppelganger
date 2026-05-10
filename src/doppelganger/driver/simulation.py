"""Driver — runs scenarios against the NS-3 substrate via subprocess + text files.

The Driver invokes the substrate Docker image, runs a scenario inside, and
captures the resulting trace files to a host-side directory. Communication
between the Python Driver and the C++ substrate is exclusively via text
artifacts (config files in, trace files out) — no Python bindings, no live
binding, no shared memory. Doppelgänger v0.2 §9.5 covers why this boundary
matters for the GPL-2.0 / Apache-2.0 license model.

``Driver.run_scenario`` accepts two input shapes:

* **Built-in scenario name** (``str``). Runs the substrate's bundled
  ``examples/PowerTCP/config-burst.txt`` via the substrate's own example
  binary. ``"spike-burst"`` is the one built-in name today; it reproduces
  the 2026-05-02 fork spike's end-to-end run.
* **Scenario object** (``doppelganger.scenarios.Scenario``). The Driver
  compiles the Scenario into a ``config-burst.txt`` file inside the
  per-run trace directory, bind-mounts it into the substrate container,
  and invokes the simulator with ``--conf=`` pointing at the compiled
  config.

The Scenario path is what enables failure-injection variants (silent drops
via ``link_error_rate``, simulation-duration tuning, ECN/buffer knobs).
The built-in name path stays available as the simplest possible smoke test.
"""

from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Union

from doppelganger.driver.parsers.fct import parse_fct_file
from doppelganger.driver.types import PerFlowRecord
from doppelganger.scenarios.compiler import compile_scenario
from doppelganger.scenarios.topology import compile_topology
from doppelganger.scenarios.traffic import compile_traffic
from doppelganger.scenarios.types import Scenario

DEFAULT_SUBSTRATE_IMAGE = "doppelganger-substrate"
SUBSTRATE_NS3_DIR = "/opt/ns3-datacenter/simulator/ns-3.39"

# Built-in scenario shell commands. Each runs against the substrate's bundled
# example config; trace files land in ``mix/`` relative to the NS-3 root.
_BUILTIN_SCENARIOS: dict[str, str] = {
    "spike-burst": (
        "./waf --run 'powertcp-evaluation-burst "
        "--conf=examples/PowerTCP/config-burst.txt'"
    ),
}

# When the Driver runs a Scenario object, the compiled config-burst.txt is
# written here inside the bind-mounted trace directory. The substrate sees
# it at the same path (the bind mount maps host trace_dir → /traces).
_COMPILED_CONFIG_NAME = "config-burst.txt"

ScenarioInput = Union[str, Scenario]


@dataclass
class SimulationResult:
    """The artifacts and parsed records from a single scenario run."""

    scenario: str
    trace_dir: Path
    flows: list[PerFlowRecord] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    wall_clock_seconds: float = 0.0
    compiled_config_path: Path | None = None


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

    def run_scenario(
        self,
        scenario: ScenarioInput,
        run_id: str | None = None,
    ) -> SimulationResult:
        """Run a scenario end-to-end and return parsed Per-Flow Records.

        Parameters
        ----------
        scenario:
            Either the name of a built-in scenario (str; see
            :py:meth:`list_scenarios`) or a :class:`Scenario` object that
            the Driver compiles into ``config-burst.txt`` for this run.
        run_id:
            Optional label for the run's trace directory. Defaults to
            ``"<scenario-name>-<unix-timestamp>"``.

        Raises
        ------
        DriverError:
            If the substrate image is not present locally, if the simulation
            subprocess returns non-zero, or if no trace files are produced.
        """
        scenario_name, sim_command, compiled_config_path, trace_dir = (
            self._prepare_run(scenario, run_id)
        )

        self._verify_image_present()

        full_command = (
            f"cd {SUBSTRATE_NS3_DIR} && "
            f"mkdir -p mix && "
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
            scenario=scenario_name,
            trace_dir=trace_dir,
            flows=flows,
            stdout=completed.stdout,
            stderr=completed.stderr,
            wall_clock_seconds=elapsed,
            compiled_config_path=compiled_config_path,
        )

    def _prepare_run(
        self,
        scenario: ScenarioInput,
        run_id: str | None,
    ) -> tuple[str, str, Path | None, Path]:
        """Resolve scenario input → (name, shell command, compiled-config path, trace dir)."""
        if isinstance(scenario, str):
            if scenario not in _BUILTIN_SCENARIOS:
                raise DriverError(
                    f"Unknown scenario {scenario!r}. "
                    f"Known: {sorted(_BUILTIN_SCENARIOS)}"
                )
            scenario_name = scenario
            # Auto-generated run_id MUST NOT embed scenario_name — the
            # adapter surfaces run_id (and trace_dir.name) on tool
            # responses, so any string the agent sees must not leak the
            # answer key. Erik flagged the leak in the Stage 5a-realistic
            # closing-test response (2026-05-09): the agent literally
            # quoted "Scenario tag in the counter dump reads pfc-storm-16h."
            run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
            trace_dir = self.traces_root / run_id
            trace_dir.mkdir(parents=True, exist_ok=True)
            return (
                scenario_name,
                _BUILTIN_SCENARIOS[scenario],
                None,
                trace_dir,
            )

        if isinstance(scenario, Scenario):
            scenario_name = scenario.name
            # See note above re: scenario-name leak via run_id / trace_dir.
            run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
            trace_dir = self.traces_root / run_id
            trace_dir.mkdir(parents=True, exist_ok=True)

            # Compile any custom topology / traffic into the trace dir.
            # The substrate sees them at /traces/topology.txt and
            # /traces/flow.txt via the bind mount; the config-burst.txt
            # compiler emits TOPOLOGY_FILE / FLOW_FILE pointing there.
            if scenario.custom_topology is not None:
                compile_topology(scenario.custom_topology, trace_dir / "topology.txt")
            if scenario.custom_traffic is not None:
                compile_traffic(scenario.custom_traffic, trace_dir / "flow.txt")

            compiled_path = trace_dir / _COMPILED_CONFIG_NAME
            compile_scenario(scenario, compiled_path)
            # The substrate's powertcp-evaluation-burst has a known footgun
            # at line 717 of its main(): `cc_mode = algorithm;` unconditionally
            # overrides whatever CC_MODE the config-burst.txt set, with the
            # cmd-line --algorithm default of 3. Pass --algorithm explicitly
            # so the override matches scenario.cc_mode rather than silently
            # collapsing to 3.
            sim_command = (
                f"./waf --run 'powertcp-evaluation-burst "
                f"--conf=/traces/{_COMPILED_CONFIG_NAME} "
                f"--algorithm={scenario.cc_mode}'"
            )
            return scenario_name, sim_command, compiled_path, trace_dir

        raise DriverError(
            f"scenario must be a str or Scenario, got {type(scenario).__name__}"
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
