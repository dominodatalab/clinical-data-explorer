"""Chat tool settings admin page and API.

Lets editors of the extension's project browse every data tool the chat agent can
reach through the MCP server, try each against a built-in clinical sample, and set
one access mode per tool (see `backend/chat_tools/`).

Every route here is behind `require_chat_tool_admin`; the page URL itself carries
no protection. The standalone page is served at `/admin/chat-tools` (it does not
run the SPA's launch-context redirect, so the owner can open it at the bare App URL).
"""
import asyncio
import logging
from typing import Dict, List, Optional

from flask import Blueprint, g, jsonify, request, send_from_directory
from pydantic import BaseModel, Field, ValidationError

from backend.auth import get_passthrough_token
from backend.chat_tools import catalog, preview_sample
from backend.chat_tools.admin_auth import require_chat_tool_admin
from backend.chat_tools.policy import (
    ChatToolPolicy,
    PolicyConflict,
    PolicyInvalid,
    PolicyStoreUnavailable,
    ToolMode,
    ToolPolicyEntry,
    default_policy,
    get_policy_store,
)

logger = logging.getLogger(__name__)

bp = Blueprint("chat_tools_admin", __name__)

MAX_POLICY_TOOLS = 1000
CONFLICT_MESSAGE = "Someone else saved; reload to see their changes."


class PolicyUpdate(BaseModel):
    expected_revision: int
    default_mode: ToolMode
    tools: Dict[str, ToolPolicyEntry] = Field(default_factory=dict)


class ResetRequest(BaseModel):
    expected_revision: Optional[int] = None


def _authorization_header() -> Optional[str]:
    token = get_passthrough_token()
    return f"Bearer {token}" if token else None


def _json_body() -> dict:
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _policy_summary(policy: ChatToolPolicy) -> dict:
    return {
        "revision": policy.revision,
        "default_mode": policy.default_mode.value,
        "updated_at": policy.updated_at.isoformat() if policy.updated_at else None,
        "updated_by": policy.updated_by,
        "change_log": [
            {"at": change.at.isoformat(), "by": change.by, "changes": change.changes}
            for change in reversed(policy.change_log)
        ],
    }


def _load_catalog() -> tuple:
    """(tools, error). The page still works, read-only, when the MCP server is down."""
    authorization = _authorization_header()
    if not authorization:
        return [], "Authentication is required to read the tool catalog"
    try:
        return asyncio.run(catalog.list_catalog(authorization)), None
    except Exception as exc:
        logger.warning("Could not read the MCP tool catalog: %s", exc)
        return [], f"Could not connect to the MCP server: {exc}"


@bp.route("/admin/chat-tools", methods=["GET"])
@require_chat_tool_admin(page=True)
def admin_page():
    return send_from_directory("chat_ui/admin", "chat-tools.html")


@bp.route("/admin/api/chat-tools", methods=["GET"])
@require_chat_tool_admin
def list_chat_tools():
    store = get_policy_store()
    policy_error = None
    try:
        policy = store.load()
    except PolicyInvalid as exc:
        policy, policy_error = default_policy(), str(exc)

    tools, catalog_error = _load_catalog()
    known = {tool.name for tool in tools}
    examples = catalog.load_preview_examples(known) if tools else {}
    never_saved = policy.revision == 0

    tool_rows = []
    for tool in tools:
        entry = policy.tools.get(tool.name)
        tool_rows.append({
            "name": tool.name,
            "title": tool.title,
            "description": tool.description,
            "input_schema": tool.input_schema,
            "declared_output_example": tool.declared_output_example,
            "example_args": examples.get(tool.name),
            "mode": policy.mode_for(tool.name).value,
            "note": entry.note if entry else None,
            "is_default": entry is None,
            # "New" = added since the policy was last saved; nothing is new before the first save.
            "is_new": entry is None and not never_saved,
        })
    stale = [
        {"name": name, "mode": entry.mode.value, "note": entry.note}
        for name, entry in sorted(policy.tools.items())
        if catalog_error is None and name not in known
    ]

    return jsonify({
        "tools": tool_rows,
        "stale": stale,
        "policy": _policy_summary(policy) if policy_error is None else {**_policy_summary(policy), "revision": None},
        "policy_error": policy_error,
        "catalog_error": catalog_error,
        "store": store.status().model_dump(),
        "sample": {"label": preview_sample.SAMPLE_LABEL},
    })


@bp.route("/admin/api/chat-tools/policy", methods=["PUT"])
@require_chat_tool_admin
def save_policy():
    try:
        update = PolicyUpdate.model_validate(_json_body())
    except ValidationError as exc:
        return jsonify({"error": "Invalid policy", "details": exc.errors(include_url=False, include_input=False)}), 400
    if len(update.tools) > MAX_POLICY_TOOLS:
        return jsonify({"error": f"Too many tools (maximum {MAX_POLICY_TOOLS})"}), 400

    actor = g.chat_tool_admin["username"]
    try:
        saved = get_policy_store().save(
            ChatToolPolicy(default_mode=update.default_mode, tools=update.tools),
            update.expected_revision,
            actor,
        )
    except PolicyConflict:
        return jsonify({"error": CONFLICT_MESSAGE, "code": "conflict"}), 409
    except PolicyInvalid as exc:
        return jsonify({"error": str(exc), "code": "policy_invalid"}), 409
    except PolicyStoreUnavailable as exc:
        return jsonify({"error": str(exc), "code": "store_unavailable"}), 503
    logger.info("Chat tool policy saved by %s (revision %s)", actor, saved.revision)
    return jsonify({"policy": _policy_summary(saved)})


@bp.route("/admin/api/chat-tools/policy/reset", methods=["POST"])
@require_chat_tool_admin
def reset_policy():
    try:
        body = ResetRequest.model_validate(_json_body())
    except ValidationError as exc:
        return jsonify({"error": "Invalid request", "details": exc.errors(include_url=False, include_input=False)}), 400

    actor = g.chat_tool_admin["username"]
    try:
        saved = get_policy_store().reset(body.expected_revision, actor)
    except PolicyConflict:
        return jsonify({"error": CONFLICT_MESSAGE, "code": "conflict"}), 409
    except PolicyStoreUnavailable as exc:
        return jsonify({"error": str(exc), "code": "store_unavailable"}), 503
    logger.info("Chat tool policy reset to defaults by %s (revision %s)", actor, saved.revision)
    return jsonify({"policy": _policy_summary(saved)})


@bp.route("/admin/api/chat-tools/preview/dataset", methods=["POST"])
@require_chat_tool_admin
def preview_dataset():
    try:
        loaded = preview_sample.ensure_preview_dataset_loaded()
    except Exception as exc:
        logger.warning("Could not load the preview sample: %s", exc)
        return jsonify({"error": f"Could not load the preview sample: {exc}"}), 502
    return jsonify({
        "columns": loaded.get("columns", []),
        "numeric_columns": loaded.get("numeric_columns", []),
        "categorical_columns": loaded.get("categorical_columns", []),
        "date_columns": loaded.get("date_columns", []),
        "num_rows": loaded.get("num_rows"),
        "label": preview_sample.SAMPLE_LABEL,
    })


@bp.route("/admin/api/chat-tools/<name>/preview", methods=["POST"])
@require_chat_tool_admin
def preview_tool(name):
    """Run one tool against the sample, whatever its mode: the admin is evaluating it."""
    args = _json_body().get("args", {})
    if not isinstance(args, dict):
        return jsonify({"error": "args must be an object"}), 400
    authorization = _authorization_header()
    if not authorization:
        return jsonify({"error": "Authentication is required"}), 401

    try:
        preview_sample.ensure_preview_dataset_loaded()
        result = asyncio.run(catalog.call_tool(authorization, name, args))
    except Exception as exc:
        logger.warning("Tool preview for %s failed: %s", name, exc)
        return jsonify({"error": f"Could not run the tool: {exc}"}), 502
    return jsonify(result)
