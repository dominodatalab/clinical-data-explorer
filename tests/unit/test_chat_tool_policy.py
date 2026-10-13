import json
import os
import threading

import pytest
from pydantic import ValidationError

from backend import config
from backend.chat_tools.policy import (
    CHANGE_LOG_LIMIT,
    DEFAULT_MODE_CHANGE_KEY,
    ChatToolPolicy,
    ChatToolPolicyStore,
    PolicyConflict,
    PolicyInvalid,
    PolicyStoreUnavailable,
    ToolMode,
    ToolPolicyEntry,
    default_policy,
)

DATASET_TOOL = "get_dataset_data_dataset_data_get"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (
        "DOMINO_RUN_ID",
        "CHAT_TOOL_POLICY_PATH",
        "CHAT_TOOL_POLICY_DATASET",
        "CHAT_TOOL_ADMIN_USERNAMES",
        "CHAT_USER_ONLY_RESULT_MAX_BYTES",
    ):
        monkeypatch.delenv(name, raising=False)


def _policy(default=ToolMode.ENABLED, **modes):
    return ChatToolPolicy(
        default_mode=default,
        tools={name: ToolPolicyEntry(mode=ToolMode(mode)) for name, mode in modes.items()},
    )


@pytest.fixture
def store(tmp_path):
    return ChatToolPolicyStore(lambda: str(tmp_path / "chat_tool_policy.json"))


# ----- model -----

def test_default_mode_applies_to_unknown_tools():
    policy = _policy(default=ToolMode.DISABLED, known="user_only")

    assert policy.mode_for("known") is ToolMode.USER_ONLY
    assert policy.mode_for("brand_new_tool") is ToolMode.DISABLED


def test_invalid_mode_is_a_validation_error():
    with pytest.raises(ValidationError):
        ChatToolPolicy.model_validate({"tools": {"t": {"mode": "sometimes"}}})
    with pytest.raises(ValidationError):
        ChatToolPolicy.model_validate({"default_mode": "maybe"})


def test_built_in_defaults_disable_only_whole_dataset_tool():
    policy = default_policy()

    assert policy.default_mode is ToolMode.ENABLED
    assert policy.mode_for(DATASET_TOOL) is ToolMode.DISABLED
    assert policy.mode_for("get_dataset_info_dataset_info_get") is ToolMode.ENABLED
    assert default_policy() is not default_policy()


# ----- store: load -----

def test_missing_file_loads_defaults(store):
    policy = store.load()

    assert policy.revision == 0
    assert policy.mode_for(DATASET_TOOL) is ToolMode.DISABLED


def test_save_writes_atomically_and_bumps_revision(store, tmp_path):
    saved = store.save(_policy(a="user_only"), expected_revision=0, actor="alice")

    assert saved.revision == 1
    assert saved.updated_by == "alice"
    assert saved.updated_at is not None
    on_disk = json.loads((tmp_path / "chat_tool_policy.json").read_text())
    assert on_disk["revision"] == 1
    assert on_disk["tools"]["a"]["mode"] == "user_only"
    assert [p.name for p in tmp_path.iterdir()] == ["chat_tool_policy.json"]  # no temp files left
    assert store.load().revision == 1


def test_save_with_stale_revision_conflicts(store):
    store.save(_policy(a="disabled"), 0, "alice")

    with pytest.raises(PolicyConflict):
        store.save(_policy(a="enabled"), 0, "bob")

    assert store.load().mode_for("a") is ToolMode.DISABLED


def test_load_cache_reloads_when_file_changes(store, tmp_path):
    store.save(_policy(a="disabled"), 0, "alice")
    assert store.load().mode_for("a") is ToolMode.DISABLED

    # Another replica rewrites the file.
    other = ChatToolPolicyStore(lambda: str(tmp_path / "chat_tool_policy.json"))
    other.save(_policy(a="user_only"), 1, "bob")

    assert store.load().mode_for("a") is ToolMode.USER_ONLY
    assert store.load().revision == 2


def test_load_uses_cache_when_file_unchanged(store, monkeypatch):
    store.save(_policy(a="disabled"), 0, "alice")
    store.load()
    reads = []
    original = ChatToolPolicyStore._parse
    monkeypatch.setattr(ChatToolPolicyStore, "_parse", lambda self, path: reads.append(path) or original(self, path))

    store.load()
    store.load()

    assert reads == []


def test_load_returns_independent_copies(store):
    store.save(_policy(a="disabled"), 0, "alice")

    first = store.load()
    first.tools["a"] = ToolPolicyEntry(mode=ToolMode.ENABLED)

    assert store.load().mode_for("a") is ToolMode.DISABLED


@pytest.mark.parametrize("content", ["{not json", "[]", '{"default_mode": "nope"}', '{"schema_version": 2}'])
def test_corrupt_file_fails_closed(store, tmp_path, content):
    (tmp_path / "chat_tool_policy.json").write_text(content)

    with pytest.raises(PolicyInvalid):
        store.load()


def test_save_refuses_to_overwrite_corrupt_file_but_reset_recovers(store, tmp_path):
    path = tmp_path / "chat_tool_policy.json"
    path.write_text("{not json")

    with pytest.raises(PolicyInvalid):
        store.save(_policy(a="disabled"), 0, "alice")

    reset = store.reset(None, "alice")

    assert reset.mode_for(DATASET_TOOL) is ToolMode.DISABLED
    assert store.load().revision == reset.revision == 1


def test_read_error_after_good_load_uses_last_known_good(store, monkeypatch, caplog):
    store.save(_policy(a="disabled"), 0, "alice")
    good = store.load()
    monkeypatch.setattr(
        type(store.path()), "stat", lambda self, **kw: (_ for _ in ()).throw(PermissionError("nfs hiccup"))
    )

    with caplog.at_level("WARNING"):
        assert store.load() == good

    assert "last known good" in caplog.text


def test_read_error_without_last_known_good_fails_closed(store, monkeypatch):
    monkeypatch.setattr(
        type(store.path()), "stat", lambda self, **kw: (_ for _ in ()).throw(PermissionError("denied"))
    )

    with pytest.raises(PolicyInvalid):
        store.load()


def test_reset_restores_defaults_and_checks_revision(store):
    store.save(_policy(a="disabled"), 0, "alice")

    with pytest.raises(PolicyConflict):
        store.reset(0, "bob")

    reset = store.reset(1, "bob")
    assert reset.tools.keys() == {DATASET_TOOL}
    assert reset.revision == 2


# ----- store: change log -----

def test_change_log_records_old_and_new_modes(store):
    store.save(_policy(a="user_only"), 0, "alice")
    saved = store.save(_policy(default=ToolMode.DISABLED, a="enabled"), 1, "bob")

    assert [entry.by for entry in saved.change_log] == ["alice", "bob"]
    assert saved.change_log[0].changes == {"a": ["enabled", "user_only"], DATASET_TOOL: ["disabled", "enabled"]}
    assert saved.change_log[1].changes[DEFAULT_MODE_CHANGE_KEY] == ["enabled", "disabled"]
    assert saved.change_log[1].changes["a"] == ["user_only", "enabled"]


def test_change_log_skips_saves_that_change_nothing(store):
    store.save(default_policy(), 0, "alice")
    saved = store.save(default_policy(), 1, "alice")

    assert saved.revision == 2
    assert saved.change_log == []


def test_change_log_is_capped(store):
    revision = 0
    for i in range(CHANGE_LOG_LIMIT + 10):
        mode = "user_only" if i % 2 == 0 else "enabled"
        revision = store.save(_policy(a=mode), revision, f"user{i}").revision

    log = store.load().change_log
    assert len(log) == CHANGE_LOG_LIMIT
    assert log[-1].by == f"user{CHANGE_LOG_LIMIT + 9}"


def test_concurrent_saves_with_same_revision_only_one_wins(store):
    results = []

    def attempt(name):
        try:
            store.save(_policy(**{name: "disabled"}), 0, name)
            results.append("ok")
        except PolicyConflict:
            results.append("conflict")

    threads = [threading.Thread(target=attempt, args=(f"t{i}",)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == ["conflict"] * 7 + ["ok"]


# ----- store: status / durability -----

def test_status_durable_when_directory_writable(store):
    assert store.status().durable is True
    assert store.status().error is None


def test_status_not_durable_when_directory_missing(tmp_path):
    store = ChatToolPolicyStore(lambda: str(tmp_path / "missing" / "chat_tool_policy.json"))

    status = store.status()

    assert status.durable is False
    assert "missing" in status.error
    with pytest.raises(PolicyStoreUnavailable):
        store.save(default_policy(), 0, "alice")
    assert store.load().revision == 0  # reads still fall back to defaults


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_status_not_durable_when_read_only(store, tmp_path):
    tmp_path.chmod(0o500)
    try:
        assert store.status().durable is False
        with pytest.raises(PolicyStoreUnavailable):
            store.save(default_policy(), 0, "alice")
    finally:
        tmp_path.chmod(0o700)


def test_write_failure_is_reported_and_leaves_no_temp_file(store, tmp_path, monkeypatch):
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))

    with pytest.raises(PolicyStoreUnavailable):
        store.save(default_policy(), 0, "alice")

    assert list(tmp_path.iterdir()) == []


# ----- path resolution -----

def test_explicit_path_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("DOMINO_RUN_ID", "run")
    monkeypatch.setenv("CHAT_TOOL_POLICY_PATH", str(tmp_path / "x.json"))

    assert config.get_chat_tool_policy_path() == str(tmp_path / "x.json")


def test_local_dev_uses_gitignored_local_file():
    assert config.get_chat_tool_policy_path() == ".chat_tool_policy.json"


def _roots(monkeypatch, tmp_path, git=True, dfs=True):
    git_root, dfs_probe, dfs_parent = tmp_path / "mnt_data", tmp_path / "domino_datasets", tmp_path / "domino_datasets" / "local"
    if git:
        git_root.mkdir()
    if dfs:
        dfs_parent.mkdir(parents=True)
    monkeypatch.setattr(
        config, "DOMINO_DATASET_ROOTS", ((str(git_root), str(git_root)), (str(dfs_probe), str(dfs_parent)))
    )
    monkeypatch.setenv("DOMINO_RUN_ID", "run")
    return git_root, dfs_parent


def test_git_based_project_dataset_path(monkeypatch, tmp_path):
    git_root, _ = _roots(monkeypatch, tmp_path, dfs=False)
    (git_root / "chat-tool-settings").mkdir()

    assert config.get_chat_tool_policy_path() == str(git_root / "chat-tool-settings" / "chat_tool_policy.json")


def test_dfs_project_dataset_path(monkeypatch, tmp_path):
    _, dfs_parent = _roots(monkeypatch, tmp_path, git=False)
    (dfs_parent / "chat-tool-settings").mkdir()

    assert config.get_chat_tool_policy_path() == str(dfs_parent / "chat-tool-settings" / "chat_tool_policy.json")


def test_mounted_dataset_chosen_over_first_existing_root(monkeypatch, tmp_path):
    git_root, dfs_parent = _roots(monkeypatch, tmp_path)
    monkeypatch.setenv("CHAT_TOOL_POLICY_DATASET", "my-settings")
    (dfs_parent / "my-settings").mkdir()

    assert config.get_chat_tool_policy_path() == str(dfs_parent / "my-settings" / "chat_tool_policy.json")


def test_unmounted_on_domino_reports_expected_path_and_refuses_saves(monkeypatch, tmp_path):
    git_root, _ = _roots(monkeypatch, tmp_path)
    store = ChatToolPolicyStore()

    status = store.status()

    assert status.path == str(git_root / "chat-tool-settings" / "chat_tool_policy.json")
    assert status.durable is False
    assert "chat-tool-settings" in status.error
    assert "not mounted" in status.error
    with pytest.raises(PolicyStoreUnavailable):
        store.save(default_policy(), 0, "alice")
    assert not (git_root / "chat-tool-settings").exists()  # never writes to ephemeral disk
    assert store.load().revision == 0


def test_admin_usernames_are_trimmed_and_lowercased(monkeypatch):
    assert config.get_chat_tool_admin_usernames() == set()
    monkeypatch.setenv("CHAT_TOOL_ADMIN_USERNAMES", " Alice, bob ,,CAROL ")

    assert config.get_chat_tool_admin_usernames() == {"alice", "bob", "carol"}


def test_user_only_result_cap_defaults_to_256kb(monkeypatch):
    assert config.get_chat_user_only_result_max_bytes() == 256 * 1024
    monkeypatch.setenv("CHAT_USER_ONLY_RESULT_MAX_BYTES", "1000")
    assert config.get_chat_user_only_result_max_bytes() == 1000
