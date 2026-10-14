"""Chat tool access policy: the model, and the file-backed store.

One JSON file (`chat_tool_policy.json`) holds the app-wide policy. On Domino it
lives in a Dataset of the extension's project (mounted network storage, so every
app replica sees a save on its next request); in local dev it is a gitignored
file in the working directory. See `backend.config.get_chat_tool_policy_path`.

Failure semantics (deterministic):

- file missing (incl. dataset not mounted) -> built-in defaults
- file can't be reached (broken mount,     -> last known good policy if this process
  no permission, path not a folder, ...)      read one, else built-in defaults; WARNING
- file corrupt / fails validation          -> `PolicyInvalid` (callers fail closed)
- dataset not mounted / not writable       -> saves are refused, and `status()` says why

So a missing or flaky dataset never takes the assistant's tools away; only a file
that was read and found invalid does, because it holds settings an admin chose.
"""
import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, ValidationError

from backend import config

logger = logging.getLogger(__name__)

CHANGE_LOG_LIMIT = 50
DEFAULT_MODE_CHANGE_KEY = "(default for new tools)"
# Appended to `status()` errors for a policy file that can't be reached.
FALLBACK_NOTE = "Until then, the chat assistant uses the last settings it read, or the built-in defaults."


class ToolMode(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    USER_ONLY = "user_only"


class ToolPolicyEntry(BaseModel):
    mode: ToolMode
    note: Optional[str] = Field(default=None, max_length=500)


class PolicyChange(BaseModel):
    at: datetime
    by: Optional[str] = None
    # tool name -> [old mode, new mode]
    changes: Dict[str, List[str]] = Field(default_factory=dict)


class ChatToolPolicy(BaseModel):
    schema_version: Literal[1] = 1
    revision: int = 0
    default_mode: ToolMode = ToolMode.ENABLED
    tools: Dict[str, ToolPolicyEntry] = Field(default_factory=dict)
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None
    change_log: List[PolicyChange] = Field(default_factory=list)

    def mode_for(self, tool_name: str) -> ToolMode:
        entry = self.tools.get(tool_name)
        return entry.mode if entry is not None else self.default_mode


def default_policy() -> ChatToolPolicy:
    """Built-in defaults: everything On except `get_dataset_data`, which returns
    the whole DataFrame (token blow-up and full raw-data exposure)."""
    return ChatToolPolicy(
        default_mode=ToolMode.ENABLED,
        tools={"get_dataset_data_dataset_data_get": ToolPolicyEntry(mode=ToolMode.DISABLED)},
    )


class PolicyInvalid(Exception):
    """The policy file was read but its content can't be trusted. Callers must fail closed."""


class PolicyConflict(Exception):
    """The policy changed since the caller read it (optimistic concurrency)."""


class PolicyStoreUnavailable(Exception):
    """The policy can't be written (dataset not mounted, read-only, I/O error)."""


class StoreStatus(BaseModel):
    durable: bool
    path: str
    error: Optional[str] = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _diff_modes(old: ChatToolPolicy, new: ChatToolPolicy) -> Dict[str, List[str]]:
    changes: Dict[str, List[str]] = {}
    if old.default_mode != new.default_mode:
        changes[DEFAULT_MODE_CHANGE_KEY] = [old.default_mode.value, new.default_mode.value]
    for name in sorted(set(old.tools) | set(new.tools)):
        before, after = old.mode_for(name), new.mode_for(name)
        if before != after:
            changes[name] = [before.value, after.value]
    return changes


class ChatToolPolicyStore:
    """Reads and writes the policy file.

    `load()` costs one `stat()` per call (the parsed policy is cached by path,
    mtime and size), so it is cheap enough to run on every chat request.

    `save()` is guarded by a `threading.Lock` plus an optimistic `revision`
    check. Replicas don't share a lock, but admin edits are rare and a lost
    race surfaces as a 409 on the other writer's next save.
    """

    def __init__(self, path_provider: Optional[Callable[[], str]] = None):
        self._path_provider = path_provider or config.get_chat_tool_policy_path
        self._lock = threading.Lock()
        self._cache_key: Optional[Tuple[str, int, int]] = None
        self._cached: Optional[ChatToolPolicy] = None
        self._last_good: Optional[ChatToolPolicy] = None
        self._last_good_path: Optional[str] = None
        self._unreachable_logged: Optional[str] = None

    # ----- paths / status -----

    def path(self) -> Path:
        return Path(self._path_provider())

    def status(self) -> StoreStatus:
        path = self.path()
        try:
            return self._status(path)
        except OSError as exc:  # e.g. a broken network mount
            return StoreStatus(
                durable=False,
                path=str(path),
                error=f"The policy file location can't be reached: {exc}. {FALLBACK_NOTE}",
            )

    def _status(self, path: Path) -> StoreStatus:
        override = config.get_chat_tool_policy_path_override()
        parent = path.parent
        # The policy folder is created on first write, so only its parent has to exist.
        folder_creatable = not parent.is_dir() and parent.parent.is_dir()
        if not parent.is_dir() and not folder_creatable:
            if config.get_domino_run_id() and not override:
                dataset = config.get_chat_tool_policy_dataset()
                error = (
                    f"The dataset '{dataset}' that holds the chat tool settings is not mounted "
                    f"(expected {parent.parent}). By default this is the project's default dataset; "
                    "check that the app has dataset mounting enabled, or point CHAT_TOOL_POLICY_DATASET "
                    "or CHAT_TOOL_POLICY_PATH at another location. Changes can't be saved until then."
                )
            else:
                error = f"The directory for the policy file does not exist: {parent}."
            return StoreStatus(durable=False, path=str(path), error=f"{error} {FALLBACK_NOTE}")
        if path.is_dir():
            setting = "CHAT_TOOL_POLICY_PATH" if override else "The policy file path"
            return StoreStatus(
                durable=False,
                path=str(path),
                error=(
                    f"{setting} points at a folder, not a file: {path}. Use a file path such as "
                    f"{path / config.CHAT_TOOL_POLICY_FILE_NAME}. {FALLBACK_NOTE}"
                ),
            )
        writable_dir = parent.parent if folder_creatable else parent
        if path.exists() and not os.access(path, os.R_OK):
            return StoreStatus(
                durable=False,
                path=str(path),
                error=f"The policy file can't be read (permission denied): {path}. {FALLBACK_NOTE}",
            )
        if path.exists():
            writable = os.access(path, os.W_OK) and os.access(parent, os.W_OK)
        else:
            writable = os.access(writable_dir, os.W_OK)
        if not writable:
            return StoreStatus(
                durable=False,
                path=str(path),
                error=f"The policy file location is not writable: {path}",
            )
        return StoreStatus(durable=True, path=str(path))

    # ----- reading -----

    def load(self) -> ChatToolPolicy:
        """Return the current policy (a copy the caller may keep for one request).

        Raises `PolicyInvalid` only when the file was read and is corrupt or fails
        validation. A file that can't be reached gives the last known good policy,
        else the built-in defaults.
        """
        path = self.path()
        try:
            stat = path.stat()
        except FileNotFoundError:
            self._cache_key = None
            self._cached = None
            return default_policy()
        except OSError as exc:
            return self._last_good_or_defaults(path, exc)

        key = (str(path), stat.st_mtime_ns, stat.st_size)
        with self._lock:
            if self._cache_key == key and self._cached is not None:
                return self._cached.model_copy(deep=True)

        try:
            policy = self._parse(path)
        except FileNotFoundError:
            return default_policy()
        except OSError as exc:
            return self._last_good_or_defaults(path, exc)

        with self._lock:
            self._cache_key, self._cached = key, policy
            self._last_good, self._last_good_path = policy, str(path)
            self._unreachable_logged = None
        return policy.model_copy(deep=True)

    def _parse(self, path: Path) -> ChatToolPolicy:
        """Read and validate the file. I/O errors (OSError) propagate to the caller."""
        try:
            return ChatToolPolicy.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, ValidationError) as exc:  # JSONDecodeError and UnicodeDecodeError are ValueErrors
            logger.error("Chat tool policy file %s is invalid: %s", path, exc)
            raise PolicyInvalid(f"The chat tool policy file {path} is invalid: {exc}") from exc

    def _last_good_or_defaults(self, path: Path, exc: OSError) -> ChatToolPolicy:
        """The file can't be reached (broken or missing mount, no permission, a path
        component that isn't a folder). Keep the app working: use the policy this process
        last read, else the built-in defaults. Logged once per distinct error, not on
        every chat request."""
        last_good = self._last_good if self._last_good_path == str(path) else None
        fallback = "last known good policy" if last_good is not None else "built-in defaults"
        message = f"Could not read chat tool policy {path} ({exc}); using the {fallback}"
        if self._unreachable_logged != message:
            logger.warning(message)
            self._unreachable_logged = message
        if last_good is not None:
            return last_good.model_copy(deep=True)
        return default_policy()

    # ----- seeding -----

    def seed_if_missing(self) -> bool:
        """Create the policy file with the built-in defaults when there is none.

        Lets the app set itself up (the owner can then edit it in the dataset or on the
        settings page). Never overwrites: the file is written to a temp file and then
        hard-linked into place, so a replica that starts at the same time either loses
        the race harmlessly or sees the complete file, never a partial one. Returns True
        when this call created it.
        """
        with self._lock:
            path = self.path()
            if path.exists():
                return False
            status = self.status()
            if not status.durable:
                logger.info("Not seeding the chat tool policy: %s", status.error)
                return False
            tmp_name = None
            try:
                path.parent.mkdir(exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
                ) as tmp:
                    tmp_name = tmp.name
                    tmp.write(default_policy().model_dump_json(indent=2))
                    tmp.flush()
                    os.fsync(tmp.fileno())
                os.link(tmp_name, path)
            except FileExistsError:
                return False  # another replica seeded it first
            except OSError as exc:
                logger.warning("Could not seed the chat tool policy at %s: %s", path, exc)
                return False
            finally:
                if tmp_name:
                    try:
                        os.unlink(tmp_name)
                    except OSError:
                        pass
            logger.info("Seeded the chat tool policy with defaults at %s", path)
            return True

    # ----- writing -----

    def save(self, new_policy: ChatToolPolicy, expected_revision: int, actor: Optional[str]) -> ChatToolPolicy:
        """Replace `default_mode` and `tools` with `new_policy`'s, bumping the revision.

        Raises `PolicyConflict` when `expected_revision` isn't the stored one,
        `PolicyStoreUnavailable` when the file can't be written, and
        `PolicyInvalid` when the stored file is corrupt (use `reset`).
        """
        with self._lock:
            current = self._read_current_locked()
            if current.revision != expected_revision:
                raise PolicyConflict(
                    f"Policy is at revision {current.revision}, not {expected_revision}"
                )
            return self._write_locked(current, new_policy, actor)

    def reset(self, expected_revision: Optional[int], actor: Optional[str]) -> ChatToolPolicy:
        """Restore the built-in defaults. A corrupt file is overwritten regardless of
        `expected_revision`, since it has no trustworthy revision."""
        with self._lock:
            try:
                current = self._read_current_locked()
            except PolicyInvalid:
                current = ChatToolPolicy(revision=self._last_good.revision if self._last_good else 0)
            else:
                if expected_revision is not None and current.revision != expected_revision:
                    raise PolicyConflict(
                        f"Policy is at revision {current.revision}, not {expected_revision}"
                    )
            return self._write_locked(current, default_policy(), actor)

    def _read_current_locked(self) -> ChatToolPolicy:
        path = self.path()
        try:
            return self._parse(path)
        except FileNotFoundError:
            return default_policy()
        except OSError as exc:
            raise PolicyStoreUnavailable(f"Could not read {path}: {exc}") from exc

    def _write_locked(
        self, current: ChatToolPolicy, new_policy: ChatToolPolicy, actor: Optional[str]
    ) -> ChatToolPolicy:
        status = self.status()
        if not status.durable:
            raise PolicyStoreUnavailable(status.error or "The policy file can't be written")

        now = _now()
        change_log = list(current.change_log)
        changes = _diff_modes(current, new_policy)
        if changes:
            change_log.append(PolicyChange(at=now, by=actor, changes=changes))
        saved = ChatToolPolicy(
            revision=current.revision + 1,
            default_mode=new_policy.default_mode,
            tools=new_policy.tools,
            updated_at=now,
            updated_by=actor,
            change_log=change_log[-CHANGE_LOG_LIMIT:],
        )
        self._atomic_write(self.path(), saved)
        stat = self.path().stat()
        self._cache_key = (str(self.path()), stat.st_mtime_ns, stat.st_size)
        self._cached = self._last_good = saved
        self._last_good_path = str(self.path())
        return saved.model_copy(deep=True)

    @staticmethod
    def _atomic_write(path: Path, policy: ChatToolPolicy) -> None:
        """Write to a temp file in the same directory, then `os.replace` it in."""
        payload = policy.model_dump_json(indent=2)
        tmp_name = None
        try:
            path.parent.mkdir(exist_ok=True)  # the policy folder inside the dataset
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
            ) as tmp:
                tmp_name = tmp.name
                tmp.write(payload)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_name, path)
        except OSError as exc:
            if tmp_name:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
            raise PolicyStoreUnavailable(f"Could not write {path}: {exc}") from exc


_store: Optional[ChatToolPolicyStore] = None
_store_lock = threading.Lock()


def get_policy_store() -> ChatToolPolicyStore:
    """Process-wide store (path resolved from config on every call)."""
    global _store
    with _store_lock:
        if _store is None:
            _store = ChatToolPolicyStore()
        return _store


def seed_default_policy() -> bool:
    """App-start hook: create the default policy file where it will live on Domino.

    Skipped in plain local development (no Domino run and no explicit path), so running the
    app or tests locally doesn't drop files into the working directory. Never raises.
    """
    if not (config.get_domino_run_id() or config.get_chat_tool_policy_path_override()):
        return False
    try:
        return get_policy_store().seed_if_missing()
    except Exception:
        logger.exception("Could not seed the chat tool policy")
        return False
