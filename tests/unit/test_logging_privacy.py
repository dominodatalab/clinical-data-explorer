import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

PYTHON_LOG_ROOTS = (
    ROOT / "backend",
    ROOT / "mcp_server",
)
PYTHON_LOG_FILES = (
    ROOT / "chat_agent.py",
)

NON_ERROR_LOG_LEVELS = {"debug", "info", "warning"}

SENSITIVE_PYTHON_LOG_SNIPPETS = (
    "result.output",
    "user_message",
    "dataset_display_name",
    "ds_name",
    "vol_name",
    "request.headers",
    "headers.items",
    "expression",
    "pandas_expr",
)

def _python_files():
    files = list(PYTHON_LOG_FILES)
    for root in PYTHON_LOG_ROOTS:
        files.extend(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)
    return sorted(files)


def test_error_response_logging_helper_is_absent():
    helper_name = "log_" + "error_response"
    offenders = []
    for path in _python_files():
        if helper_name in path.read_text():
            offenders.append(path.relative_to(ROOT))

    assert offenders == []


def test_non_error_python_logs_do_not_include_user_payloads():
    offenders = []

    for path in _python_files():
        source = path.read_text()
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in NON_ERROR_LOG_LEVELS:
                continue
            if not isinstance(node.func.value, ast.Name) or node.func.value.id != "logger":
                continue

            segment = ast.get_source_segment(source, node) or ""
            for snippet in SENSITIVE_PYTHON_LOG_SNIPPETS:
                if snippet in segment:
                    offenders.append((path.relative_to(ROOT), node.lineno, snippet, segment))

    assert offenders == []
