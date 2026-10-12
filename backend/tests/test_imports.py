"""Every process entry point must import cleanly in a fresh interpreter.

Test modules import things in their own order, which can hide circular imports that only
break when the worker or scheduler starts. Each module here runs in its own subprocess.
"""

import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]

ENTRY_POINTS = [
    "app.main",  # api
    "app.worker.broker",  # worker (broker arg)
    "app.worker.tasks",  # worker and scheduler (task modules)
    "app.worker.scheduler",  # scheduler
    "app.worker.healthcheck",
    "app.cli",  # migrate (seed-sources)
]


@pytest.mark.parametrize("module", ENTRY_POINTS)
def test_entry_point_imports_cleanly(module: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
