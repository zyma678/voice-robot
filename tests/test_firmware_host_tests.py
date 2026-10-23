"""Run the firmware host C unit tests as part of the Python suite.

This keeps the on-device C port (vr_routing / vr_retrieval, validated against
tests/golden/memory_contract.json) honest on every `pytest -q`, without an
ESP32. Skipped automatically where no C compiler is available.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "firmware" / "test" / "run_host_tests.sh"


@pytest.mark.skipif(shutil.which("cc") is None, reason="no C compiler (cc) available")
def test_firmware_host_tests_pass() -> None:
    result = subprocess.run(
        ["bash", str(RUNNER)], capture_output=True, text=True, cwd=str(ROOT)
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "all firmware host tests passed" in result.stdout
