"""Unit tests for browser-side JavaScript modules."""
import shutil
import subprocess
from pathlib import Path


def test_api_js_helpers():
    repo_root = Path(__file__).resolve().parents[2]
    node = shutil.which("node")
    js_test_paths = sorted(str(path.relative_to(repo_root)) for path in (repo_root / "tests/unit/js").glob("*.test.mjs"))

    assert node is not None, "Node.js is required to run api.js helper tests"
    assert js_test_paths, "Expected at least one JavaScript unit test"

    result = subprocess.run(
        [
            node,
            "--experimental-vm-modules",
            *js_test_paths,
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
