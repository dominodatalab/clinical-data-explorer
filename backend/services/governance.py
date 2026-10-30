"""Build the Governance API URL at the public Domino deployment origin."""
import logging

from backend.auth import get_domino_external_url

logger = logging.getLogger(__name__)


def get_governance_api_url():
    """Return the Governance endpoint exposed through Domino's public ingress."""
    external_url = get_domino_external_url()
    if external_url:
        return f"{external_url}/api/governance/v1"

    logger.warning("External Domino URL is not configured - governance features will not work")
    return None
