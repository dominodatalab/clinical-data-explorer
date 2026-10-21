import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def _fake_uv(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    capture_file = tmp_path / "uv-environment"
    uv = bin_dir / "uv"
    uv.write_text(
        """#!/bin/sh
{
    printf 'url=%s\\n' "$DOMINO_LLM_GATEWAY_URL"
    printf 'model=%s\\n' "$DOMINO_LLM_GATEWAY_MODEL"
    if [ -n "${API_KEY_OVERRIDE:-}" ]; then
        printf 'api_key=set\\n'
    else
        printf 'api_key=unset\\n'
    fi
    printf 'args='
    printf '%s|' "$@"
    printf '\\n'
} > "$CAPTURE_FILE"
exit "${FAKE_UV_EXIT_CODE:-0}"
"""
    )
    uv.chmod(0o755)
    return bin_dir, capture_file


def _run_test_external(tmp_path: Path, **overrides: str) -> tuple[subprocess.CompletedProcess, str]:
    bin_dir, capture_file = _fake_uv(tmp_path)
    env = os.environ.copy()
    for name in (
        "API_KEY_OVERRIDE",
        "DOMINO_LLM_GATEWAY_URL",
        "DOMINO_LLM_GATEWAY_MODEL",
        "FAKE_UV_EXIT_CODE",
    ):
        env.pop(name, None)
    env.update(overrides)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["CAPTURE_FILE"] = str(capture_file)

    result = subprocess.run(
        ["make", "--no-print-directory", "test-external"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    capture = capture_file.read_text() if capture_file.exists() else ""
    return result, capture


def test_external_target_supplies_dogfood_defaults_and_warns_without_pat(tmp_path):
    result, capture = _run_test_external(tmp_path)

    assert result.returncode == 0
    assert "url=https://apps.cloud-dogfood.domino.tech/apps/llm_gateway" in capture
    assert "model=sonnet5-5" in capture
    assert "api_key=unset" in capture
    assert "args=run|--locked|pytest|-m|external|" in capture
    assert "The Cloud Dogfood Gateway is selected" in result.stderr
    assert "Domino PAT issued by cloud-dogfood" in result.stderr
    assert "API_KEY_OVERRIDE is not set" in result.stderr


@pytest.mark.parametrize(
    "gateway_url",
    [
        "https://apps.cloud-dogfood.domino.tech/apps/llm_gateway",
        "https://apps.cloud-dogfood.domino.tech/apps/llm_gateway/",
        "https://apps.cloud-dogfood.domino.tech/apps/llm_gateway/v1",
        "https://apps.cloud-dogfood.domino.tech/apps/llm_gateway/v1/",
    ],
)
def test_external_target_identifies_required_pat_origin_for_dogfood_url(
    tmp_path, gateway_url
):
    result, capture = _run_test_external(
        tmp_path,
        API_KEY_OVERRIDE="dummy-pat",
        DOMINO_LLM_GATEWAY_URL=gateway_url,
    )

    assert result.returncode == 0
    assert "api_key=set" in capture
    assert "The Cloud Dogfood Gateway is selected" in result.stderr
    assert "Domino PAT issued by cloud-dogfood" in result.stderr
    assert "API_KEY_OVERRIDE is not set" not in result.stderr


def test_external_target_preserves_explicit_settings(tmp_path):
    result, capture = _run_test_external(
        tmp_path,
        API_KEY_OVERRIDE="dummy-pat",
        DOMINO_LLM_GATEWAY_URL="https://domino.example/apps/custom-gateway",
        DOMINO_LLM_GATEWAY_MODEL="custom-model",
    )

    assert result.returncode == 0
    assert "url=https://domino.example/apps/custom-gateway" in capture
    assert "model=custom-model" in capture
    assert "api_key=set" in capture
    assert "The Cloud Dogfood Gateway is selected" not in result.stderr
    assert "API_KEY_OVERRIDE is not set" not in result.stderr


def test_external_target_propagates_test_failures(tmp_path):
    result, _capture = _run_test_external(
        tmp_path,
        API_KEY_OVERRIDE="dummy-pat",
        FAKE_UV_EXIT_CODE="1",
    )

    assert result.returncode != 0
    assert "Error 1" in result.stderr
