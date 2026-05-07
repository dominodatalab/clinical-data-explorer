"""Backend configuration module.

Holds module-level constants that the rest of the backend reads. The
important property: consumers MUST import the module (`from backend import
config`) and access `config.MCP_SERVER_URL` at call time — NOT bind the
value with `from backend.config import MCP_SERVER_URL` — so tests (and
`monkeypatch.setattr(config, "MCP_SERVER_URL", ...)`) can swap the value
without re-importing every consumer.

The MCP_SERVER_URL env var is honored to make this seam usable from tests
and from production deployments where the MCP server runs on a different
host or port.
"""
import os

MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://localhost:3333")
MCP_REQUEST_TIMEOUT_SECONDS = float(os.environ.get("MCP_REQUEST_TIMEOUT_SECONDS", "300"))
