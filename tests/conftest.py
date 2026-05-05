"""Pytest fixtures and skip helpers for Driver tests."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from doppelganger.driver.simulation import DEFAULT_SUBSTRATE_IMAGE


def _substrate_image_present() -> bool:
    """Return True iff the doppelganger-substrate Docker image is built locally."""
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(
        ["docker", "image", "inspect", DEFAULT_SUBSTRATE_IMAGE],
        capture_output=True,
    )
    return result.returncode == 0


@pytest.fixture(scope="session")
def substrate_available() -> bool:
    return _substrate_image_present()
