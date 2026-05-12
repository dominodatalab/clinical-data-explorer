import importlib

import pytest

import backend.config as config_module
import backend.services.file_size_limits as file_size_limits_module


def test_enforce_raises_when_file_exceeds_configured_limit():
    try:
        file_size_limits_module.enforce("too-big.csv", file_size_limits_module.DATA_FILE_SIZE_LIMIT + 1)
        assert False, "expected DataFileTooLarge"
    except file_size_limits_module.DataFileTooLarge as exc:
        assert str(exc) == (
            f"too-big.csv must be less than or equal to {file_size_limits_module.DATA_FILE_SIZE_LIMIT} bytes to be processable"
        )


def test_enforce_raises_when_estimated_dataframe_would_exceed_remaining_memory(monkeypatch):
    monkeypatch.setattr(file_size_limits_module, "_get_container_memory_usage_bytes", lambda: 90)
    monkeypatch.setattr(file_size_limits_module, "_get_container_memory_limit_bytes", lambda: 1000)

    try:
        file_size_limits_module.enforce("tight.csv", 300)
        assert False, "expected DataFileTooLarge"
    except file_size_limits_module.DataFileTooLarge as exc:
        assert "There's not enough space to process tight.csv." in str(exc)


def test_enforce_returns_when_memory_cgroup_values_are_unavailable(monkeypatch):
    monkeypatch.setattr(file_size_limits_module, "_get_container_memory_usage_bytes", lambda: None)
    monkeypatch.setattr(file_size_limits_module, "_get_container_memory_limit_bytes", lambda: None)

    assert file_size_limits_module.enforce("ok.csv", 1024) is None


def test_file_size_limit_module_uses_environment_configuration(monkeypatch):
    monkeypatch.setenv("DATA_FILE_SIZE_LIMIT_B", "7")

    importlib.reload(config_module)
    reloaded_module = importlib.reload(file_size_limits_module)

    assert reloaded_module.DATA_FILE_SIZE_LIMIT == 7

    monkeypatch.delenv("DATA_FILE_SIZE_LIMIT_B", raising=False)
    importlib.reload(config_module)
    importlib.reload(reloaded_module)
