"""Errors that tell the chat agent's policy layer what is safe to show a model.

Chat tools can be set to "run, hide output from AI". Their output never reaches
the model, but a failure message can: `FastApiMCP` turns every non-2xx response
into text the model sees. Most `detail` strings only echo the model's own
arguments (`Column 'X' not found`), but some embed pandas/exception text that can
quote cell values. So safety is opt-in and fails closed:

- `detail` is unchanged, so the REST clients (the Table/Explore/Filter UIs) keep
  showing exactly the messages they always did.
- `model_safe_detail` is an *extra* field in the JSON body. When present, the
  backend may show that text to a model for a hidden-output tool. When absent
  (a plain `HTTPException`, or a `ToolError` without it) the model only gets a
  generic "the tool failed" message.
"""
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse


class ToolError(HTTPException):
    """`HTTPException` that can carry a description that is safe to show a model."""

    def __init__(
        self,
        status_code: int,
        detail: Any = None,
        headers: Optional[Dict[str, str]] = None,
        model_safe_detail: Optional[str] = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.model_safe_detail = model_safe_detail

    @classmethod
    def safe(cls, status_code: int, detail: str, headers: Optional[Dict[str, str]] = None) -> "ToolError":
        """For messages that only repeat the caller's own arguments, so `detail` is model-safe."""
        return cls(status_code, detail, headers=headers, model_safe_detail=detail)


def error_body(exc: HTTPException) -> Dict[str, Any]:
    """JSON body for an HTTP error: FastAPI's `{"detail": ...}` plus `model_safe_detail` if set."""
    body: Dict[str, Any] = {"detail": exc.detail}
    model_safe_detail = getattr(exc, "model_safe_detail", None)
    if model_safe_detail is not None:
        body["model_safe_detail"] = model_safe_detail
    return body


async def tool_error_handler(request: Request, exc: ToolError) -> JSONResponse:
    return JSONResponse(error_body(exc), status_code=exc.status_code, headers=exc.headers)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ToolError, tool_error_handler)
