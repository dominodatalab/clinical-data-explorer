// Governance bundle / finding workflow for the Data Explorer frontend.
//
// Owns the end-to-end "is this snapshot file in a governance bundle?" flow:
//   - On dataset load: `checkGovernanceBundles(ctx)` → query the proxy at
//     /governance/attachment-overviews, dedupe to active bundles, and either
//     reveal the "Governed" badge (`showGovernanceIndicator`) or the
//     "Not Governed" badge (`showUngovernedIndicator`).
//   - When the user opens the popover: render bundle name/state, count,
//     and the "View in Governance" deep link (`updateGovernanceDisplay`).
//   - When the user opens the finding modal: populate the approver, assignee,
//     stage, and approval selectors from cached bundle metadata
//     (`populateFindingSelectors` + `loadProjectCollaborators` +
//     `loadCurrentUser` + `loadBundleStages`).
//   - On submit: POST to /governance/findings (`createFinding`).
//
// Deliberate edges (kept in script.js, not moved here):
//   - `generatePermalink()` lives in script.js because it reads
//     `tableState.filters` (table-view-internal state — moves with
//     `modules/table-view.js` in plan box 4.4g / P14). The finding-submit
//     button click handler also lives in script.js so it can call
//     `generatePermalink()` locally and pass the resulting permalink into
//     `createFinding(findingData, permalink)` — that's the only signature
//     change vs. pre-extraction. Internal behavior is byte-equivalent.
//   - `showToast()` lives in `core/dom.js` so this module and script.js's
//     `copyPermalink()` can both depend on a single source.
//
// `populateFindingStageSelector` and `populateFindingApprovalSelector` are
// kept as exports even though they're internal-deprecated wrappers — the
// originals were preserved in script.js for "compatibility", so we keep
// them here for the same reason. Per ground rule #5 we don't delete code
// we haven't fully traced.
//
// `hideGovernanceIndicator()` has no callers in script.js right now (it's
// dead-by-inspection at the time of P12) but it's exported for parity with
// `show*Indicator` and so a future caller doesn't have to re-grow the API.
//
// DOM lookup happens at module load. JS modules are deferred under the HTML
// spec, so the document is fully parsed by the time this file evaluates.
// Same pattern as `modules/column-labels.js` (P12 / 4.4a).

import { state } from '../core/state.js';
import { apiUrl, fetchJson } from '../core/api.js';
import { showToast } from '../core/dom.js';
import { openModal, closeModal, attachOverlayDismiss } from '../core/modals.js';

const governanceIndicator = document.getElementById('governance-indicator');
const governanceBadge = document.getElementById('governance-badge');
const governancePopover = document.getElementById('governance-popover');
const governancePopoverTitle = document.getElementById('governance-popover-title');
const governanceBadgePrefix = document.getElementById('governance-badge-prefix');
const governanceBadgeBundleName = document.getElementById('governance-badge-bundle-name');
const governanceBadgeCount = document.getElementById('governance-badge-count');
const governanceBadgeCaret = document.getElementById('governance-badge-caret');
const governanceBundleName = document.getElementById('governance-bundle-name');
const governanceBundleState = document.getElementById('governance-bundle-state');
const governanceBundleSelect = document.getElementById('governance-bundle-select');
const governanceBundleSelectorContainer = document.getElementById('governance-bundle-selector-container');
const governanceViewBundleBtn = document.getElementById('governance-view-bundle');
const createFindingBtn = document.getElementById('create-finding-btn');
const findingModal = document.getElementById('finding-modal-overlay');
const findingStageSelect = document.getElementById('finding-stage');
const findingApprovalSelect = document.getElementById('finding-approval');

// Check for governance bundles when a dataset is loaded.
// Governance attachments are snapshot-specific. Both DatasetSnapshotFile
// and NetAppVolumeSnapshotFile attachments are keyed by a globally-unique
// identifier.snapshotId (Mongo OID for datasets, UUID for NetApp), so we
// use the same query shape for both — filename + snapshotId.
//
// ctx fields (from the load response / load body):
//   sourceType:       'dataset' | 'netapp' | 'local' (or missing)
//   filename:         basename, no source prefix (how governance stores it)
//   snapshotId:       globally-unique snapshot id (the only reliable key)
//   datasetId/volumeId: defensive extra narrowing, when available
export async function checkGovernanceBundles(ctx) {
    try {
        ctx = ctx || {};
        const filename = ctx.filename;

        // Governance attachments are always tied to a snapshot. Without
        // snapshot identity we can't pose a safe query, so show ungoverned.
        // Cases this covers: local files, netapp r/w-head loads (can't be
        // attached per the platform), and missing context.
        const isDataset = ctx.sourceType === 'dataset';
        const isNetapp = ctx.sourceType === 'netapp';
        if (!(isDataset || isNetapp) || !filename || !ctx.snapshotId) {
            console.log('No snapshot identity available for governance check — treating as ungoverned');
            showUngovernedIndicator();
            return;
        }

        const params = {
            'identifier.filename': filename,
            'identifier.snapshotId': ctx.snapshotId,
        };
        if (isDataset) {
            params['type'] = 'DatasetSnapshotFile';
            if (ctx.datasetId) params['identifier.datasetId'] = ctx.datasetId;
        } else {
            params['type'] = 'NetAppVolumeSnapshotFile';
            if (ctx.volumeId) params['identifier.volumeId'] = ctx.volumeId;
        }

        const data = await queryAttachmentOverviews(params);

        if (data.error || !data.data || data.data.length === 0) {
            console.log('No governance bundles found for this snapshot file');
            showUngovernedIndicator();
            return;
        }

        // Filter to only active (non-archived) bundles and deduplicate by bundle ID
        const bundleMap = new Map();
        data.data.forEach(item => {
            if (item.bundle && item.bundle.state !== 'Archived') {
                if (!bundleMap.has(item.bundle.id)) {
                    bundleMap.set(item.bundle.id, {
                        id: item.bundle.id,
                        name: item.bundle.name,
                        state: item.bundle.state,
                        attachmentId: item.id,
                        attachmentType: item.type,
                        // Needed to build the project-scoped "View in Governance" link.
                        // The bundle URL lives under /u/{owner}/{project}/governance/bundle/{id}.
                        projectOwner: item.bundle.projectOwner || '',
                        projectName: item.bundle.projectName || '',
                        projectId: item.bundle.projectId || ''
                    });
                }
            }
        });

        const activeBundles = Array.from(bundleMap.values());

        if (activeBundles.length === 0) {
            console.log('No active governance bundles found');
            showUngovernedIndicator();
            return;
        }

        // Store bundles and select the first one
        state.governanceState.bundles = activeBundles;
        state.governanceState.available = true;
        state.governanceState.selectedBundle = activeBundles[0];

        console.log(`Found ${activeBundles.length} governance bundle(s) for snapshot file`);

        // Show the governance indicator
        showGovernanceIndicator();

        // Load stages for the selected bundle
        await loadBundleStages(state.governanceState.selectedBundle.id);

        // Also try to get current user info
        loadCurrentUser();

    } catch (error) {
        console.error('Error checking governance bundles:', error);
        showUngovernedIndicator();
    }
}

// Helper to query attachment overviews with given params
async function queryAttachmentOverviews(params) {
    const searchParams = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
        searchParams.set(key, value);
    }
    searchParams.set('limit', '50');

    return await fetchJson(apiUrl(`governance/attachment-overviews?${searchParams.toString()}`));
}

function showGovernanceIndicator() {
    governanceIndicator.style.display = 'block';
    governanceIndicator.classList.remove('no-governance');

    // Badge prefix + surrounding pieces are toggled by updateGovernanceDisplay()
    // based on how many bundles this file lives in. The trailing colon is
    // rendered by CSS (::after) so it can be suppressed in the ungoverned
    // and mobile cases without touching JS.
    governanceBadgePrefix.textContent = 'Governed';
    governanceBadge.setAttribute('aria-disabled', 'false');

    updateGovernanceDisplay();

    createFindingBtn.style.display = 'inline-flex';
}

function showUngovernedIndicator() {
    governanceIndicator.style.display = 'block';
    governanceIndicator.classList.add('no-governance');
    state.governanceState.available = false;
    state.governanceState.bundles = [];
    state.governanceState.selectedBundle = null;
    state.governanceState.bundleStages = [];

    // Collapse the badge to a single-word state label.
    governanceBadgePrefix.textContent = 'Not Governed';
    governanceBadgeBundleName.style.display = 'none';
    governanceBadgeBundleName.textContent = '';
    governanceBadgeCount.style.display = 'none';
    governanceBadgeCount.textContent = '';
    governanceBadgeCaret.style.display = 'none';
    governanceBadge.title = 'This dataset is not in any governance bundle';
    governanceBadge.setAttribute('aria-disabled', 'true');
    governanceBadge.setAttribute('aria-expanded', 'false');
    governanceBadge.classList.remove('active');
    governancePopover.classList.remove('visible');
    governanceBundleSelectorContainer.style.display = 'none';

    createFindingBtn.style.display = 'none';
}

export function hideGovernanceIndicator() {
    governanceIndicator.style.display = 'none';
    state.governanceState.available = false;
    state.governanceState.bundles = [];
    state.governanceState.selectedBundle = null;
    state.governanceState.bundleStages = [];

    governancePopover.classList.remove('visible');
    governanceBadge.classList.remove('active');
    governanceBadge.setAttribute('aria-expanded', 'false');

    createFindingBtn.style.display = 'none';
}

function updateGovernanceDisplay() {
    if (!state.governanceState.selectedBundle) return;

    const bundle = state.governanceState.selectedBundle;
    const bundleCount = state.governanceState.bundles.length;
    const isMulti = bundleCount > 1;

    // --- Badge: "Governed: <bundle name> [N] ▾" (caret/count only when multi) ---
    governanceBadgeBundleName.style.display = '';
    governanceBadgeBundleName.textContent = bundle.name || '';

    if (isMulti) {
        governanceBadgeCount.style.display = '';
        governanceBadgeCount.textContent = String(bundleCount);
        governanceBadgeCaret.style.display = '';
        governanceBadge.title = `Filing findings in "${bundle.name}" (1 of ${bundleCount} bundles) — click to switch`;
    } else {
        governanceBadgeCount.style.display = 'none';
        governanceBadgeCount.textContent = '';
        governanceBadgeCaret.style.display = 'none';
        governanceBadge.title = `This dataset is governed by bundle "${bundle.name}"`;
    }

    // --- Popover details ---
    governanceBundleName.textContent = bundle.name;
    governancePopoverTitle.textContent = isMulti
        ? `Governance bundles (${bundleCount})`
        : 'Governance bundle';

    governanceBundleState.textContent = bundle.state || 'Active';
    governanceBundleState.className = 'bundle-state';
    if (bundle.state === 'Active' || !bundle.state) {
        governanceBundleState.classList.add('state-active');
    } else if (bundle.state === 'Archived') {
        governanceBundleState.classList.add('state-archived');
    }

    // Update view link - construct Domino governance URL.
    // Bundles live under the owning project's namespace: the correct URL is
    // /u/{projectOwner}/{projectName}/governance/bundle/{id} (singular "bundle").
    // The global /governance/bundles/{id} path opens the policy, not the bundle.
    const dominoBaseUrl = getDominoBaseUrl();
    if (dominoBaseUrl && bundle.projectOwner && bundle.projectName) {
        governanceViewBundleBtn.href = `${dominoBaseUrl}/u/${encodeURIComponent(bundle.projectOwner)}/${encodeURIComponent(bundle.projectName)}/governance/bundle/${bundle.id}`;
    } else if (dominoBaseUrl) {
        governanceViewBundleBtn.href = `${dominoBaseUrl}/governance/bundles/${bundle.id}`;
    } else {
        governanceViewBundleBtn.href = `#bundle-${bundle.id}`;
    }

    // Default-show the selector whenever the file is in more than one bundle.
    // We drop the old "Change Bundle" two-click affordance entirely.
    if (isMulti) {
        governanceBundleSelectorContainer.style.display = 'block';
        governanceBundleSelect.innerHTML = '';
        state.governanceState.bundles.forEach(b => {
            const option = document.createElement('option');
            option.value = b.id;
            option.textContent = `${b.name} (${b.state || 'Active'})`;
            if (b.id === bundle.id) option.selected = true;
            governanceBundleSelect.appendChild(option);
        });
    } else {
        governanceBundleSelectorContainer.style.display = 'none';
    }
}

function getDominoBaseUrl() {
    // Try to extract Domino base URL from current location
    // In Domino, the URL structure is typically: https://domain/u/user/project/app/...
    const match = window.location.href.match(/(https?:\/\/[^\/]+)/);
    return match ? match[1] : null;
}

async function loadBundleStages(bundleId) {
    try {
        const data = await fetchJson(apiUrl(`governance/bundles/${bundleId}/stages`));

        if (data.error) {
            console.error('Error loading bundle stages:', data.error);
            return;
        }

        state.governanceState.bundleStages = data.stages || [];
        state.governanceState.bundleApprovals = data.approvals || [];
        state.governanceState.designatedApprovers = data.designatedApprovers || [];
        state.governanceState.policyVersionId = data.policyVersionId || '';
        state.governanceState.currentStage = data.currentStage || '';

        // The attachment-overviews bundle is trimmed; the stages endpoint
        // fetches the full bundle, which carries projectOwner/projectName.
        // Patch the selected bundle so "View in Governance" links correctly.
        if (state.governanceState.selectedBundle && (data.projectOwner || data.projectName)) {
            state.governanceState.selectedBundle.projectOwner = data.projectOwner || state.governanceState.selectedBundle.projectOwner || '';
            state.governanceState.selectedBundle.projectName = data.projectName || state.governanceState.selectedBundle.projectName || '';
            state.governanceState.selectedBundle.projectId = data.projectId || state.governanceState.selectedBundle.projectId || '';
            updateGovernanceDisplay();
        }

        console.log(`Loaded ${state.governanceState.bundleStages.length} stages, ${state.governanceState.bundleApprovals.length} approvals, ${state.governanceState.designatedApprovers.length} designated approvers, policyVersionId: ${state.governanceState.policyVersionId}`);

        // Also load project collaborators for assignee selection
        await loadProjectCollaborators();

        // Update the finding modal selectors
        populateFindingSelectors();

    } catch (error) {
        console.error('Error loading bundle stages:', error);
    }
}

async function loadProjectCollaborators() {
    try {
        const data = await fetchJson(apiUrl('governance/project-collaborators'));

        if (data.error) {
            console.warn('Could not load project collaborators:', data.error);
            return;
        }

        state.governanceState.projectCollaborators = data.collaborators || [];
        console.log(`Loaded ${state.governanceState.projectCollaborators.length} project collaborators`);

    } catch (error) {
        console.error('Error loading project collaborators:', error);
    }
}

async function loadCurrentUser() {
    try {
        const data = await fetchJson(apiUrl('governance/current-user'));

        if (!data.error) {
            state.governanceState.currentUser = data;
        }
    } catch (error) {
        console.error('Error loading current user:', error);
    }
}

function populateFindingSelectors() {
    const findingApproverSelect = document.getElementById('finding-approver');
    const findingAssigneeSelect = document.getElementById('finding-assignee');

    // Populate approver selector - use designated approvers first, then fall back to collaborators
    findingApproverSelect.innerHTML = '<option value="">Select approver...</option>';

    // Add designated approvers with a label
    if (state.governanceState.designatedApprovers.length > 0) {
        const optgroup = document.createElement('optgroup');
        optgroup.label = 'Designated Approvers';
        state.governanceState.designatedApprovers.forEach(approver => {
            const option = document.createElement('option');
            option.value = JSON.stringify({ id: approver.id, name: approver.name });
            option.textContent = approver.name;
            optgroup.appendChild(option);
        });
        findingApproverSelect.appendChild(optgroup);
    }

    // Add project collaborators
    if (state.governanceState.projectCollaborators.length > 0) {
        const optgroup = document.createElement('optgroup');
        optgroup.label = 'Project Collaborators';
        state.governanceState.projectCollaborators.forEach(collab => {
            const option = document.createElement('option');
            option.value = JSON.stringify({ id: collab.id, name: collab.name });
            option.textContent = collab.name + (collab.role ? ` (${collab.role})` : '');
            optgroup.appendChild(option);
        });
        findingApproverSelect.appendChild(optgroup);
    }

    // Populate assignee selector from project collaborators
    findingAssigneeSelect.innerHTML = '<option value="">Select assignee...</option>';

    // Add current user first if available
    if (state.governanceState.currentUser && state.governanceState.currentUser.name) {
        const option = document.createElement('option');
        option.value = JSON.stringify({ id: state.governanceState.currentUser.id, name: state.governanceState.currentUser.name });
        option.textContent = state.governanceState.currentUser.name + ' (me)';
        option.selected = true;
        findingAssigneeSelect.appendChild(option);
    }

    // Add project collaborators
    state.governanceState.projectCollaborators.forEach(collab => {
        // Skip if it's the current user (already added)
        if (state.governanceState.currentUser && collab.name === state.governanceState.currentUser.name) {
            return;
        }
        const option = document.createElement('option');
        option.value = JSON.stringify({ id: collab.id, name: collab.name });
        option.textContent = collab.name + (collab.role ? ` (${collab.role})` : '');
        findingAssigneeSelect.appendChild(option);
    });

    // Populate approval selector from bundle-level approvals
    findingApprovalSelect.innerHTML = '<option value="">Select approval...</option>';
    state.governanceState.bundleApprovals.forEach(approval => {
        const option = document.createElement('option');
        option.value = approval.id;
        // Show approval name with evidence name if different
        let displayName = approval.name || 'Approval';
        if (approval.evidenceName && approval.evidenceName !== approval.name) {
            displayName += ` - ${approval.evidenceName}`;
        }
        option.textContent = displayName;
        option.dataset.evidenceId = approval.evidenceId || '';
        option.title = approval.evidenceDescription || '';
        findingApprovalSelect.appendChild(option);
    });

    // Populate stage selector (optional)
    findingStageSelect.innerHTML = '<option value="">Select stage (optional)...</option>';
    state.governanceState.bundleStages.forEach(stage => {
        const option = document.createElement('option');
        option.value = stage.stageId;
        option.textContent = stage.stageName;
        // Mark current stage
        if (stage.stageName === state.governanceState.currentStage) {
            option.textContent += ' (current)';
        }
        findingStageSelect.appendChild(option);
    });
}

// Kept for compatibility - calls the new combined function
function populateFindingStageSelector() {
    populateFindingSelectors();
}

function populateFindingApprovalSelector(stageId) {
    // Stage selection no longer affects approval list
    // Approvals are at bundle level, not per-stage
    // This function is kept for compatibility but doesn't need to do anything
}

// Create a finding via the governance proxy. `permalink` is computed by the
// caller (currently script.js's finding-submit-btn handler) using
// `generatePermalink()` — see the module docstring for why that bridge lives
// in script.js rather than here.
export async function createFinding(findingData, permalink) {
    const submitBtn = document.getElementById('finding-submit-btn');
    submitBtn.classList.add('loading');
    submitBtn.disabled = true;

    try {
        // Append permalink to description
        const descriptionWithLink = findingData.description
            ? `${findingData.description}\n\n---\nData View: ${permalink}`
            : `Data View: ${permalink}`;

        // Prepare the finding request
        const findingRequest = {
            bundleId: state.governanceState.selectedBundle.id,
            policyVersionId: state.governanceState.policyVersionId,
            approvalId: findingData.approvalId,
            name: findingData.name,
            description: descriptionWithLink,
            severity: findingData.severity,
            approver: findingData.approver,
            assignee: findingData.assignee
        };

        // Add optional fields
        if (findingData.dueDate) {
            findingRequest.dueDate = findingData.dueDate;
        }

        if (findingData.evidenceId) {
            findingRequest.evidenceId = findingData.evidenceId;
        }

        const data = await fetchJson(apiUrl('governance/findings'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(findingRequest)
        });

        if (data.error) {
            throw new Error(data.error);
        }

        // Success!
        closeFindingModal();
        showToast('Finding created successfully!');

        return data;

    } catch (error) {
        console.error('Error creating finding:', error);
        alert(`Failed to create finding: ${error.message}`);
        throw error;
    } finally {
        submitBtn.classList.remove('loading');
        submitBtn.disabled = false;
    }
}

// Governance Event Listeners
function toggleGovernancePopover() {
    // The badge is a context/action trigger only when there's an actual
    // bundle to describe or choose. In the ungoverned state the popover
    // has nothing useful to show, so the badge is purely informational.
    if (governanceIndicator.classList.contains('no-governance')) return;
    const nowVisible = !governancePopover.classList.contains('visible');
    governancePopover.classList.toggle('visible', nowVisible);
    governanceBadge.classList.toggle('active', nowVisible);
    governanceBadge.setAttribute('aria-expanded', String(nowVisible));
}

governanceBadge.addEventListener('click', (e) => {
    e.stopPropagation();
    toggleGovernancePopover();
});

governanceBadge.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        e.stopPropagation();
        toggleGovernancePopover();
    }
});

document.getElementById('governance-popover-close').addEventListener('click', () => {
    governancePopover.classList.remove('visible');
    governanceBadge.classList.remove('active');
    governanceBadge.setAttribute('aria-expanded', 'false');
});

governanceBundleSelect.addEventListener('change', async (e) => {
    const selectedId = e.target.value;
    const bundle = state.governanceState.bundles.find(b => b.id === selectedId);
    if (bundle) {
        state.governanceState.selectedBundle = bundle;
        updateGovernanceDisplay();
        await loadBundleStages(bundle.id);
    }
});

// Close popover when clicking outside
document.addEventListener('click', (e) => {
    if (!governanceIndicator.contains(e.target)) {
        governancePopover.classList.remove('visible');
        governanceBadge.classList.remove('active');
        governanceBadge.setAttribute('aria-expanded', 'false');
    }
});

// Finding Modal Event Listeners (the finding-submit-btn handler intentionally
// stays in script.js — see module docstring)
createFindingBtn.addEventListener('click', openFindingModal);
document.getElementById('finding-modal-close').addEventListener('click', closeFindingModal);
document.getElementById('finding-cancel-btn').addEventListener('click', closeFindingModal);

attachOverlayDismiss(findingModal, closeFindingModal);

findingStageSelect.addEventListener('change', (e) => {
    populateFindingApprovalSelector(e.target.value);
});

function openFindingModal() {
    // Reset form
    document.getElementById('finding-name').value = '';
    document.getElementById('finding-severity').value = '';
    document.getElementById('finding-description').value = '';
    document.getElementById('finding-due-date').value = '';

    // Populate stage and approval selectors
    populateFindingSelectors();

    openModal(findingModal);
}

function closeFindingModal() {
    closeModal(findingModal);
}
