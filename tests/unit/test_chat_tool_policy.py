import errno
import json
import logging
import os
import threading
from pathlib import Path

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
        "DOMINO_PROJECT_NAME",
        "DOMINO_DATASETS_DIR",
        "DOMINO_IS_GIT_BASED",
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


def _raise_on_stat(monkeypatch, error):
    monkeypatch.setattr(Path, "stat", lambda self, **kw: (_ for _ in ()).throw(error))


@pytest.mark.parametrize("error", [
    PermissionError(errno.EACCES, "Permission denied"),
    OSError(errno.ENOTCONN, "Transport endpoint is not connected"),  # a broken network mount
    OSError(errno.ESTALE, "Stale file handle"),
    OSError(errno.EIO, "Input/output error"),
])
def test_unreachable_file_without_last_known_good_uses_defaults(store, monkeypatch, caplog, error):
    _raise_on_stat(monkeypatch, error)

    with caplog.at_level("WARNING"):
        assert store.load() == default_policy()

    assert "built-in defaults" in caplog.text


def test_unreachable_file_is_logged_once_not_on_every_request(store, monkeypatch, caplog):
    _raise_on_stat(monkeypatch, OSError(errno.ENOTCONN, "Transport endpoint is not connected"))

    with caplog.at_level("WARNING"):
        for _ in range(5):
            store.load()

    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


def test_last_known_good_is_only_used_for_the_same_file(tmp_path, monkeypatch):
    paths = iter([tmp_path / "a.json", tmp_path / "b.json"])
    current = {"path": next(paths)}
    store = ChatToolPolicyStore(lambda: str(current["path"]))
    store.save(_policy(a="disabled"), 0, "alice")
    store.load()

    current["path"] = next(paths)  # e.g. CHAT_TOOL_POLICY_PATH changed
    _raise_on_stat(monkeypatch, OSError(errno.ENOTCONN, "Transport endpoint is not connected"))

    assert store.load() == default_policy()


def test_path_through_a_file_uses_defaults(tmp_path):
    (tmp_path / "dataset").write_text("not a folder")
    store = ChatToolPolicyStore(lambda: str(tmp_path / "dataset" / ".clinical-data-explorer" / "chat_tool_policy.json"))

    assert store.load() == default_policy()
    assert store.status().durable is False


def test_path_that_is_a_folder_uses_defaults_and_refuses_saves(tmp_path):
    store = ChatToolPolicyStore(lambda: str(tmp_path))

    assert store.load() == default_policy()
    status = store.status()
    assert status.durable is False
    assert "folder, not a file" in status.error
    assert str(tmp_path / "chat_tool_policy.json") in status.error
    with pytest.raises(PolicyStoreUnavailable):
        store.save(default_policy(), 0, "alice")


def test_file_that_is_not_utf8_fails_closed(store, tmp_path):
    (tmp_path / "chat_tool_policy.json").write_bytes(b"\xff\xfe{")

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


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_status_not_durable_when_the_file_is_unreadable(store, tmp_path):
    store.save(default_policy(), 0, "alice")
    (tmp_path / "chat_tool_policy.json").chmod(0o200)
    try:
        status = store.status()
        assert status.durable is False
        assert "can't be read" in status.error
    finally:
        (tmp_path / "chat_tool_policy.json").chmod(0o600)


def test_status_never_raises_on_a_broken_mount(store, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError(errno.ENOTCONN, "Transport endpoint is not connected")
    monkeypatch.setattr(ChatToolPolicyStore, "_status", broken)

    status = store.status()

    assert status.durable is False
    assert "can't be reached" in status.error
    assert "built-in defaults" in status.error


def test_status_not_durable_when_directory_missing(tmp_path):
    store = ChatToolPolicyStore(lambda: str(tmp_path / "missing" / "deeper" / "chat_tool_policy.json"))

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


def test_explicit_path_wins_over_a_mounted_dataset(monkeypatch, tmp_path):
    git_root, _ = _roots(monkeypatch, tmp_path)
    (git_root / "My-Project").mkdir()
    monkeypatch.setenv("CHAT_TOOL_POLICY_PATH", str(tmp_path / "elsewhere" / "settings.json"))

    assert config.get_chat_tool_policy_path() == str(tmp_path / "elsewhere" / "settings.json")


def test_explicit_path_is_trimmed_and_expands_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CHAT_TOOL_POLICY_PATH", "  ~/settings/chat_tool_policy.json  ")

    assert config.get_chat_tool_policy_path() == str(tmp_path / "settings" / "chat_tool_policy.json")


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_blank_explicit_path_counts_as_unset(monkeypatch, tmp_path, blank):
    git_root, _ = _roots(monkeypatch, tmp_path)
    monkeypatch.setenv("CHAT_TOOL_POLICY_PATH", blank)

    assert config.get_chat_tool_policy_path_override() is None
    assert config.get_chat_tool_policy_path() == _policy_file(git_root / "My-Project")


def test_local_dev_uses_gitignored_local_file():
    assert config.get_chat_tool_policy_path() == ".chat_tool_policy.json"


def _roots(monkeypatch, tmp_path, project="My-Project"):
    """Fake Domino mounts: a Git-style root and a DFS-style root, nothing mounted yet."""
    git_root, dfs_root = tmp_path / "mnt_data", tmp_path / "domino_datasets_local"
    git_root.mkdir()
    dfs_root.mkdir()
    monkeypatch.setattr(config, "GIT_DATASETS_ROOT", str(git_root))
    monkeypatch.setattr(config, "DFS_DATASETS_ROOT", str(dfs_root))
    monkeypatch.setenv("DOMINO_RUN_ID", "run")
    monkeypatch.setenv("DOMINO_PROJECT_NAME", project)
    return git_root, dfs_root


def _policy_file(dataset_dir):
    return str(dataset_dir / ".clinical-data-explorer" / "chat_tool_policy.json")


def test_defaults_to_the_projects_default_dataset_named_after_the_project(monkeypatch, tmp_path):
    git_root, _ = _roots(monkeypatch, tmp_path)
    (git_root / "My-Project").mkdir()

    assert config.get_chat_tool_policy_dataset() == "My-Project"
    assert config.get_chat_tool_policy_path() == _policy_file(git_root / "My-Project")


def test_git_based_project_uses_datasets_dir(monkeypatch, tmp_path):
    git_root, _ = _roots(monkeypatch, tmp_path)
    monkeypatch.setenv("DOMINO_IS_GIT_BASED", "true")
    monkeypatch.setenv("DOMINO_DATASETS_DIR", str(git_root))
    (git_root / "My-Project").mkdir()

    assert config.get_chat_tool_policy_path() == _policy_file(git_root / "My-Project")


def test_dfs_project_dataset_is_found_under_local(monkeypatch, tmp_path):
    _, dfs_root = _roots(monkeypatch, tmp_path)
    monkeypatch.setenv("DOMINO_IS_GIT_BASED", "false")
    (dfs_root / "My-Project").mkdir()

    assert config.get_chat_tool_policy_path() == _policy_file(dfs_root / "My-Project")


def test_dfs_datasets_dir_env_is_honoured(monkeypatch, tmp_path):
    _roots(monkeypatch, tmp_path)
    datasets_dir = tmp_path / "domino_datasets"
    (datasets_dir / "local" / "My-Project").mkdir(parents=True)
    monkeypatch.setenv("DOMINO_IS_GIT_BASED", "false")
    monkeypatch.setenv("DOMINO_DATASETS_DIR", str(datasets_dir))

    assert config.get_chat_tool_policy_path() == _policy_file(datasets_dir / "local" / "My-Project")


def test_whichever_root_has_the_dataset_wins_whatever_the_project_type_says(monkeypatch, tmp_path):
    git_root, dfs_root = _roots(monkeypatch, tmp_path)
    monkeypatch.setenv("DOMINO_IS_GIT_BASED", "true")  # says Git, but the dataset is only under the DFS root
    (dfs_root / "My-Project").mkdir()

    assert config.get_chat_tool_policy_path() == _policy_file(dfs_root / "My-Project")


def test_explicit_dataset_name_overrides_the_project_default(monkeypatch, tmp_path):
    git_root, _ = _roots(monkeypatch, tmp_path)
    monkeypatch.setenv("CHAT_TOOL_POLICY_DATASET", "my-settings")
    (git_root / "my-settings").mkdir()
    (git_root / "My-Project").mkdir()

    assert config.get_chat_tool_policy_path() == _policy_file(git_root / "my-settings")


def test_project_names_with_odd_characters_are_used_verbatim(monkeypatch, tmp_path):
    git_root, _ = _roots(monkeypatch, tmp_path, project="CDE test_v2 (QA)")
    (git_root / "CDE test_v2 (QA)").mkdir()

    assert config.get_chat_tool_policy_path() == _policy_file(git_root / "CDE test_v2 (QA)")


def test_without_a_project_name_the_legacy_dataset_name_is_used(monkeypatch, tmp_path):
    git_root, _ = _roots(monkeypatch, tmp_path)
    monkeypatch.delenv("DOMINO_PROJECT_NAME")

    assert config.get_chat_tool_policy_dataset() == "chat-tool-settings"
    assert config.get_chat_tool_policy_path() == _policy_file(git_root / "chat-tool-settings")


def test_unmounted_on_domino_reports_expected_path_and_refuses_saves(monkeypatch, tmp_path):
    git_root, _ = _roots(monkeypatch, tmp_path)
    monkeypatch.setenv("DOMINO_IS_GIT_BASED", "true")
    store = ChatToolPolicyStore()

    status = store.status()

    assert status.path == _policy_file(git_root / "My-Project")
    assert status.durable is False
    assert "My-Project" in status.error and "not mounted" in status.error
    assert str(git_root / "My-Project") in status.error
    with pytest.raises(PolicyStoreUnavailable):
        store.save(default_policy(), 0, "alice")
    assert not (git_root / "My-Project").exists()  # never writes to ephemeral disk
    assert store.load().revision == 0


# ----- the policy folder and seeding -----

def test_first_save_creates_the_policy_folder_inside_the_mounted_dataset(monkeypatch, tmp_path):
    git_root, _ = _roots(monkeypatch, tmp_path)
    (git_root / "My-Project").mkdir()
    store = ChatToolPolicyStore()

    assert store.status().durable is True  # the folder is created on demand
    store.save(_policy(a="user_only"), 0, "alice")

    assert (git_root / "My-Project" / ".clinical-data-explorer" / "chat_tool_policy.json").is_file()
    assert store.load().mode_for("a") is ToolMode.USER_ONLY


def test_seed_creates_the_default_file_without_the_owner_doing_anything(monkeypatch, tmp_path):
    git_root, _ = _roots(monkeypatch, tmp_path)
    (git_root / "My-Project").mkdir()
    store = ChatToolPolicyStore()

    assert store.seed_if_missing() is True

    seeded = json.loads((git_root / "My-Project" / ".clinical-data-explorer" / "chat_tool_policy.json").read_text())
    assert seeded["revision"] == 0
    assert seeded["tools"] == {DATASET_TOOL: {"mode": "disabled", "note": None}}
    assert store.load().mode_for(DATASET_TOOL) is ToolMode.DISABLED
    # a seeded file behaves like "never saved": the first save still expects revision 0
    assert store.save(_policy(a="disabled"), 0, "alice").revision == 1
    assert [p.name for p in (git_root / "My-Project" / ".clinical-data-explorer").iterdir()] == ["chat_tool_policy.json"]


def test_seed_never_overwrites_an_existing_file(store, tmp_path):
    store.save(_policy(a="user_only"), 0, "alice")

    assert store.seed_if_missing() is False

    assert store.load().mode_for("a") is ToolMode.USER_ONLY
    assert store.load().revision == 1


def test_seed_loses_a_race_harmlessly(store, tmp_path, monkeypatch):
    real_link = os.link

    def other_replica_wins(src, dst):
        (tmp_path / "chat_tool_policy.json").write_text(default_policy().model_dump_json())
        return real_link(src, dst)  # now raises FileExistsError

    monkeypatch.setattr(os, "link", other_replica_wins)

    assert store.seed_if_missing() is False
    assert [p.name for p in tmp_path.iterdir()] == ["chat_tool_policy.json"]  # no temp file left


def test_seed_skips_when_the_dataset_is_not_mounted(monkeypatch, tmp_path):
    git_root, _ = _roots(monkeypatch, tmp_path)
    store = ChatToolPolicyStore()

    assert store.seed_if_missing() is False
    assert not (git_root / "My-Project").exists()


def test_no_dataset_and_no_override_on_domino_runs_on_defaults(monkeypatch, tmp_path):
    """The graceful case: nothing mounted at all and no override still gives the built-in defaults."""
    from backend.chat_tools import policy as policy_module

    _roots(monkeypatch, tmp_path)
    monkeypatch.setenv("DOMINO_DATASETS_DIR", str(tmp_path / "not-there"))
    store = ChatToolPolicyStore()

    assert policy_module.seed_default_policy() is False
    assert store.load() == default_policy()
    status = store.status()
    assert status.durable is False
    assert "not mounted" in status.error and "built-in defaults" in status.error


def test_seed_default_policy_hook_ignores_a_blank_override_in_local_dev(monkeypatch, tmp_path):
    from backend.chat_tools import policy as policy_module

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHAT_TOOL_POLICY_PATH", "   ")

    assert policy_module.seed_default_policy() is False
    assert list(tmp_path.iterdir()) == []


def test_seed_default_policy_hook_is_skipped_in_plain_local_dev(monkeypatch, tmp_path):
    from backend.chat_tools import policy as policy_module

    monkeypatch.chdir(tmp_path)

    assert policy_module.seed_default_policy() is False
    assert list(tmp_path.iterdir()) == []


def test_seed_default_policy_hook_seeds_on_domino_and_never_raises(monkeypatch, tmp_path):
    from backend.chat_tools import policy as policy_module

    git_root, _ = _roots(monkeypatch, tmp_path)
    (git_root / "My-Project").mkdir()

    assert policy_module.seed_default_policy() is True
    assert policy_module.seed_default_policy() is False  # already there

    monkeypatch.setattr(
        ChatToolPolicyStore, "seed_if_missing", lambda self: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    assert policy_module.seed_default_policy() is False


def test_admin_usernames_are_trimmed_and_lowercased(monkeypatch):
    assert config.get_chat_tool_admin_usernames() == set()
    monkeypatch.setenv("CHAT_TOOL_ADMIN_USERNAMES", " Alice, bob ,,CAROL ")

    assert config.get_chat_tool_admin_usernames() == {"alice", "bob", "carol"}


def test_user_only_result_cap_defaults_to_256kb(monkeypatch):
    assert config.get_chat_user_only_result_max_bytes() == 256 * 1024
    monkeypatch.setenv("CHAT_USER_ONLY_RESULT_MAX_BYTES", "1000")
    assert config.get_chat_user_only_result_max_bytes() == 1000
