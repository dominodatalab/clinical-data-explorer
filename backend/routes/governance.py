"""Governance blueprint — proxies the Domino governance API.

Extracted from `backend/app.py` (REFACTOR_PLAN.md §1, step 1.5b). Owns
all six `/governance/*` endpoints (attachment-overviews, bundles,
bundles/.../stages, findings, current-user, project-collaborators).

The bundle/finding HTTP calls remain inlined here rather than being
hoisted into `backend/services/governance.py`: the six handlers each
have different request/response envelopes and status-code conventions,
and per ground rule #2 we preserve those exactly. `services/governance.py`
keeps just the URL builder (`get_governance_api_url`).
"""
import logging
import os

import requests
from flask import Blueprint, jsonify, request

from backend.auth import get_domino_api_host, get_passthrough_token
from backend.services.governance import get_governance_api_url

logger = logging.getLogger(__name__)

bp = Blueprint('governance', __name__)


@bp.route('/governance/attachment-overviews', methods=['GET'])
def get_attachment_overviews():
    """Query attachment overviews to find bundles containing a dataset file"""
    governance_url = get_governance_api_url()
    if not governance_url:
        return jsonify({'error': 'DOMINO_API_HOST not configured', 'items': [], 'available': False})

    token = get_passthrough_token()
    if not token:
        return jsonify({'error': 'Authentication required', 'items': [], 'available': False}), 401

    try:
        # Forward query parameters to the governance API
        params = {}
        for key in ['identifier.filename', 'identifier.datasetId', 'identifier.snapshotId',
                    'identifier.volumeId', 'identifier.version',
                    'identifier.source', 'identifier.name', 'type', 'search', 'limit', 'offset']:
            if request.args.get(key):
                params[key] = request.args.get(key)

        headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'}

        response = requests.get(
            f"{governance_url}/attachment-overviews",
            params=params,
            headers=headers,
            timeout=30
        )

        if response.status_code == 200:
            return jsonify(response.json())
        else:
            logger.debug(f"Governance API returned {response.status_code}: {response.text[:200]}")
            return jsonify({'error': 'Failed to query governance API', 'items': [], 'available': False}), response.status_code
    except requests.exceptions.ConnectionError:
        logger.warning("Governance API not available")
        return jsonify({'error': 'Governance API not available', 'items': [], 'available': False})
    except Exception as e:
        logger.error(f"Error querying attachment overviews: {e}")
        return jsonify({'error': str(e), 'items': []}), 500


@bp.route('/governance/bundles/<bundle_id>', methods=['GET'])
def get_bundle_details(bundle_id):
    """Get detailed bundle information including stages, approvals, and evidence"""
    governance_url = get_governance_api_url()
    if not governance_url:
        return jsonify({'error': 'DOMINO_API_HOST not configured', 'available': False})

    token = get_passthrough_token()
    if not token:
        return jsonify({'error': 'Authentication required', 'available': False}), 401

    try:
        headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'}

        response = requests.get(
            f"{governance_url}/bundles/{bundle_id}",
            headers=headers,
            timeout=30
        )

        if response.status_code == 200:
            return jsonify(response.json())
        else:
            logger.error(f"Governance API error: {response.status_code} - {response.text}")
            return jsonify({'error': 'Failed to get bundle details'}), response.status_code
    except requests.exceptions.ConnectionError:
        logger.warning("Governance API not available")
        return jsonify({'error': 'Governance API not available', 'available': False})
    except Exception as e:
        logger.error(f"Error getting bundle details: {e}")
        return jsonify({'error': str(e)}), 500


@bp.route('/governance/bundles/<bundle_id>/stages', methods=['GET'])
def get_bundle_stages(bundle_id):
    """Get bundle stages with approvals for finding creation"""
    governance_url = get_governance_api_url()
    if not governance_url:
        return jsonify({'error': 'DOMINO_API_HOST not configured', 'available': False})

    token = get_passthrough_token()
    if not token:
        return jsonify({'error': 'Authentication required', 'available': False}), 401

    try:
        headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'}

        # Get the bundle to access its stages and approvals
        response = requests.get(
            f"{governance_url}/bundles/{bundle_id}",
            headers=headers,
            timeout=30
        )

        if response.status_code == 200:
            bundle = response.json()

            # Extract stages from the bundle
            stages = []
            if bundle.get('stages'):
                for stage in bundle['stages']:
                    stage_info = {
                        'stageId': stage.get('stageId'),
                        'stageName': stage.get('stage', {}).get('name', 'Unknown Stage'),
                        'approvals': []
                    }
                    stages.append(stage_info)

            # POST /findings needs the real Approval record id, not the StageApproval
            # template id that lives on the bundle. Fetch /bundles/{id}/approvals and
            # key the instances by stageApprovalId so we can pair each template with
            # its actual approval record.
            approval_instances_by_stage = {}
            try:
                approvals_resp = requests.get(
                    f"{governance_url}/bundles/{bundle_id}/approvals",
                    headers=headers,
                    timeout=30,
                )
                if approvals_resp.status_code == 200:
                    for inst in approvals_resp.json() or []:
                        stage_approval_id = inst.get('stageApprovalId')
                        if stage_approval_id and inst.get('id'):
                            approval_instances_by_stage[stage_approval_id] = inst.get('id')
                else:
                    logger.warning(
                        f"Could not fetch bundle approvals ({approvals_resp.status_code}): "
                        f"{approvals_resp.text}"
                    )
            except requests.exceptions.RequestException as e:
                logger.warning(f"Could not fetch bundle approvals: {e}")

            # Extract approvals from stageApprovals (at bundle level, not per-stage)
            # and resolve each to the real Approval record id via stageApprovalId.
            approvals = []
            designated_approvers = []  # Approvers designated for the bundle
            if bundle.get('stageApprovals'):
                for approval in bundle['stageApprovals']:
                    stage_approval_id = approval.get('id')
                    approval_instance_id = approval_instances_by_stage.get(stage_approval_id)
                    if not approval_instance_id:
                        # No materialized Approval record — skip so the UI can't
                        # send a stageApprovalId that /findings will reject.
                        continue
                    approval_info = {
                        'id': approval_instance_id,
                        'name': approval.get('name', 'Approval'),
                        'evidenceId': approval.get('evidence', {}).get('id'),
                        'evidenceName': approval.get('evidence', {}).get('name', ''),
                        'evidenceDescription': approval.get('evidence', {}).get('description', '')
                    }
                    approvals.append(approval_info)

                    # Collect approvers from this approval
                    for approver in approval.get('approvers', []):
                        approver_info = {
                            'id': approver.get('id'),
                            'name': approver.get('name', '')
                        }
                        # Avoid duplicates
                        if approver_info not in designated_approvers:
                            designated_approvers.append(approver_info)

            return jsonify({
                'stages': stages,
                'approvals': approvals,
                'designatedApprovers': designated_approvers,
                'bundleId': bundle_id,
                'policyVersionId': bundle.get('policyVersionId', ''),
                'currentStage': bundle.get('stage', ''),
                # Needed to build the project-scoped "View in Governance" link.
                # attachment-overviews returns a trimmed bundle that omits these.
                'projectOwner': bundle.get('projectOwner', ''),
                'projectName': bundle.get('projectName', ''),
                'projectId': bundle.get('projectId', '')
            })
        else:
            logger.error(f"Governance API error: {response.status_code} - {response.text}")
            return jsonify({'error': 'Failed to get bundle stages'}), response.status_code
    except requests.exceptions.ConnectionError:
        logger.warning("Governance API not available")
        return jsonify({'error': 'Governance API not available', 'available': False})
    except Exception as e:
        logger.error(f"Error getting bundle stages: {e}")
        return jsonify({'error': str(e)}), 500


@bp.route('/governance/findings', methods=['POST'])
def create_finding():
    """Create a new finding in the governance system"""
    governance_url = get_governance_api_url()
    if not governance_url:
        return jsonify({'error': 'DOMINO_API_HOST not configured', 'available': False}), 503

    token = get_passthrough_token()
    if not token:
        return jsonify({'error': 'Authentication required', 'available': False}), 401

    try:
        finding_data = request.json
        if not finding_data:
            return jsonify({'error': 'No finding data provided'}), 400

        # Validate required fields (approvalId is optional per user feedback)
        required_fields = ['bundleId', 'name', 'severity']
        missing = [f for f in required_fields if not finding_data.get(f)]
        if missing:
            return jsonify({'error': f'Missing required fields: {", ".join(missing)}'}), 400

        # Remove empty optional fields to avoid API validation errors
        optional_fields = ['approvalId', 'evidenceId', 'stageId', 'description', 'dueDate']
        for field in optional_fields:
            if field in finding_data and not finding_data[field]:
                del finding_data[field]

        # Convert date-only dueDate to full ISO 8601 datetime format
        # API expects format like "2026-01-28T00:00:00Z" not just "2026-01-28"
        if finding_data.get('dueDate') and 'T' not in finding_data['dueDate']:
            finding_data['dueDate'] = f"{finding_data['dueDate']}T23:59:59Z"

        headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'}

        response = requests.post(
            f"{governance_url}/findings",
            json=finding_data,
            headers=headers,
            timeout=30
        )

        if response.status_code in [200, 201]:
            return jsonify(response.json())
        else:
            logger.error(f"Governance API error creating finding: {response.status_code} - {response.text}")
            return jsonify({'error': 'Failed to create finding', 'detail': response.text}), response.status_code
    except requests.exceptions.ConnectionError:
        logger.warning("Governance API not available")
        return jsonify({'error': 'Governance API not available', 'available': False}), 503
    except Exception as e:
        logger.error(f"Error creating finding: {e}")
        return jsonify({'error': str(e)}), 500


@bp.route('/governance/current-user', methods=['GET'])
def get_current_user():
    """Get current user info (ObjectId + display name) for finding assignee/approver fields.

    Uses /v4/users/self with the visiting user's passthrough token. This works for
    any authenticated user and does NOT require ManageCollaborators on the project,
    unlike the projectSettingsCollaborators lookup used previously.
    """
    domino_api_host = get_domino_api_host()
    if not domino_api_host:
        return jsonify({'error': 'DOMINO_API_HOST not configured'}), 503

    token = get_passthrough_token()
    if not token:
        return jsonify({'error': 'Authentication required'}), 401

    try:
        headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'}
        response = requests.get(
            f"{domino_api_host}/v4/users/self",
            headers=headers,
            timeout=30,
        )
        if response.status_code == 200:
            person = response.json()
            username = person.get('userName', '')
            return jsonify({
                'id': person.get('id', ''),
                'name': person.get('fullName') or username,
            })
        logger.error(f"Error getting current user from /v4/users/self: {response.status_code} - {response.text}")
        return jsonify({'error': 'Failed to get current user'}), response.status_code
    except requests.exceptions.ConnectionError:
        return jsonify({'error': 'Domino API not available'}), 503
    except Exception as e:
        logger.error(f"Error getting current user: {e}")
        return jsonify({'error': str(e)}), 500


@bp.route('/governance/project-collaborators', methods=['GET'])
def get_project_collaborators():
    """Get project collaborators for approver/assignee selection"""
    domino_api_host = get_domino_api_host()
    project_id = os.environ.get('DOMINO_PROJECT_ID')

    if not domino_api_host:
        return jsonify({'error': 'DOMINO_API_HOST not configured', 'collaborators': []})

    if not project_id:
        return jsonify({'error': 'DOMINO_PROJECT_ID not configured', 'collaborators': []})

    token = get_passthrough_token()
    if not token:
        return jsonify({'error': 'Authentication required', 'collaborators': []}), 401

    try:
        headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'}

        # Use /v4/projects/{projectId}/collaborators instead of projectSettingsCollaborators:
        # the latter requires ManageCollaborators (owner/admin only), while the former
        # is readable by any project member and returns Person objects directly.
        response = requests.get(
            f"{domino_api_host}/v4/projects/{project_id}/collaborators",
            params={'getUsers': 'true'},
            headers=headers,
            timeout=30
        )

        if response.status_code == 200:
            people = response.json()
            users = []
            for person in people:
                user_id = person.get('id', '')
                if not user_id:
                    continue
                users.append({
                    'id': user_id,
                    'name': person.get('fullName') or person.get('userName', ''),
                    'role': '',
                })
            return jsonify({'collaborators': users})
        else:
            logger.error(f"Error getting collaborators: {response.status_code} - {response.text}")
            return jsonify({'error': 'Failed to get collaborators', 'collaborators': []})
    except requests.exceptions.ConnectionError:
        logger.warning("Domino API not available for collaborators")
        return jsonify({'error': 'Domino API not available', 'collaborators': []})
    except Exception as e:
        logger.error(f"Error getting project collaborators: {e}")
        return jsonify({'error': str(e), 'collaborators': []}), 500
