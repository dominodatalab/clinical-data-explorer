"""Governance API helpers.

Extracted from `backend/app.py` (REFACTOR_PLAN.md §1, step 1.4). For now
this only owns `get_governance_api_url`. The bundle/finding HTTP helpers
are still inlined inside the `/governance/*` route handlers in
`backend/app.py`; they'll move into `backend/routes/governance.py` in
step 1.5b (and any reusable bits should be lifted into this module at
that time).

`get_governance_api_url` reads the Flask `request` (only when called from
inside a request context) for header-based diagnostics, so this module
does import Flask. It otherwise does no HTTP — it just builds the
externally-reachable governance base URL.
"""
import logging

from flask import request

from backend.auth import get_domino_external_url

logger = logging.getLogger(__name__)


def get_governance_api_url():
    """
    Get the governance API base URL.

    IMPORTANT: Governance APIs (/api/governance/v1/*) are only exposed via the
    external ingress URL, not the internal nucleus-frontend service that
    DOMINO_API_HOST points to. Hitting them via DOMINO_API_HOST returns
    404 "Public api endpoint ... not found". So we must use the external URL.
    """
    external_url = get_domino_external_url()
    if external_url:
        return f"{external_url}/api/governance/v1"

    # Help diagnosis: log which forwarding headers the reverse proxy is actually sending
    try:
        from flask import has_request_context
        if has_request_context():
            relevant = {k: v for k, v in request.headers.items()
                        if k.lower() in ('host', 'x-forwarded-host', 'x-forwarded-proto',
                                         'x-forwarded-for', 'x-forwarded-prefix',
                                         'x-original-host', 'x-original-uri', 'referer')}
            logger.warning(f"External Domino URL not available. Request headers for diagnosis: {relevant}")
        else:
            logger.warning("External Domino URL not available - governance features will not work")
    except Exception:
        logger.warning("External Domino URL not available - governance features will not work")
    return None
