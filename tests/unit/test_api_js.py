"""Unit tests for the browser API helper module."""
import shutil
import subprocess
from pathlib import Path


def test_api_js_helpers():
    repo_root = Path(__file__).resolve().parents[2]
    node = shutil.which("node")

    assert node is not None, "Node.js is required to run api.js helper tests"

    result = subprocess.run(
        [
            node,
            "--experimental-vm-modules",
            "tests/unit/js/api_helpers.test.mjs",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
