---
name: cut-clinical-data-explorer-release
description: Prepare, cut, resume, or publish a Clinical Data Explorer release. Use for release readiness, changelogs, SemVer selection, release branches or PRs, tags, GitHub releases, Domino compatibility, cross-version candidate e2e runs, and extension_versions publication in dominodatalab/clinical-data-explorer.
---

# Cut a Clinical Data Explorer release

Run this workflow only in `dominodatalab/clinical-data-explorer`. It is deliberately human-gated because tags and published extension registry entries affect customer-visible releases.

Before doing anything else, read [references/release-process.md](references/release-process.md) completely. Treat it and this file as the skill implementation.

## Start with a live process audit

Before changing Git, GitHub, release metadata, or the skill:

1. Fetch the current release sections from both linked Confluence runbooks using an authenticated connector or authenticated browser:
   - Clinical Data Explorer Runbook
   - Extensions Installer Runbook, including official-repository requirements, release steps, and provisional-release guidance
2. Inspect the current local repository, GitHub repository metadata, releases, tags, default branch, `extension_manifest.json`, the remote `extension_versions` branch, and the live Clinical Data Explorer trigger implementation, branch conventions, and relevant PR state in `cerebrotech/internal-e2e-tests-service`.
3. Map every live runbook requirement to this file and the reference. Include intentional user-approved divergences and repository-specific safeguards.
4. If a requirement is missing, stale, contradictory, or implemented incorrectly, make no release mutation. Show the human a compact comparison of the source requirement, current behavior, and recommended edit.
5. After the human accepts an edit, update this skill in a dedicated worktree and branch from freshly fetched `origin/main`, never in a release worktree. Keep release execution paused until the agreed process update is merged or the human explicitly chooses to continue from the reviewed local skill change.
6. At the process-confirmation gate, present only differences from the last agreed release plan. If there are no differences, say so. If there is no trustworthy record of the last agreed plan, say that and present the current reconciliation list once rather than guessing. Do not print the complete process unless the human asks for it. Ask whether the differences are correct and continue revising until the human explicitly agrees.

If either runbook cannot be read, stop and say which access path failed. Ask the human to restore access or provide the current content; do not substitute the stored snapshot silently. Do not edit Confluence unless separately asked.

## Discover state before acting

This workflow is resumable. On every invocation, inspect existing release and registry branches and PRs, remote tags, GitHub releases, both metadata files, and candidate-specific test PRs and trigger results in `cerebrotech/internal-e2e-tests-service`. Verify any existing artifact against its expected commit and content, then resume at the first incomplete gate. Resume only a human-named candidate or one unambiguous candidate whose stable publication is incomplete. Historical provisional artifacts are never candidates for this workflow. Never recreate a completed action blindly.

Use authenticated GitHub tooling before a browser and never use unauthenticated raw HTTP when an authenticated client is available. Preserve unrelated local changes. Never move, reuse, or delete a release tag to repair a failed attempt.

## Required human gates

Stop and wait at each gate below. Do not infer approval from earlier authorization. After the process gate clears, the external-test handoff and candidate e2e kickoff may be initiated in parallel; both gates must clear before the version gate or any Clinical Data Explorer release mutation.

- **Process:** the human explicitly agrees that the presented differences are correct, or that there are no differences.
- **External tests:** run `make test-external` as a standalone command against the exact functional candidate SHA. Report whether every selected external test actually executed and passed, any failed or errored, or any skipped/were not collected; a successful command exit alone is not passing evidence. Then ask the human to run the same command against that SHA in an appropriately configured environment and report the exact result back. Only an unambiguous human report that at least one selected external test passed and none skipped, failed, or errored clears this gate; deselected non-external tests are expected. Human evidence may be reused across a release branch proven to differ only in release-version metadata, as defined in step 10. Do not ask the human to paste credentials or deployment secrets.
- **Candidate e2e:** ask for the state of the candidate-backed Nexus scenarios and the Clinical Data Explorer Cucu suite for the recorded updated `main` candidate on every Domino row in the compatibility test matrix. Track each evidence-stream and matrix-row cell independently as passed, not run, pending, or failed, and treat observed current-head run status as authoritative before posting a trigger. Preserve passed cells, wait for pending cells, post only the triggers for not-run cells, and block on failed cells until the human requests or approves remediation and a rerun. After direct approval, mark only the affected failed cell rerun-approved and post exactly one fresh trigger for it; do not rerun passed or pending cells. If any cell has not run or is rerun-approved, finish the compatibility assessment and create or reuse the temporary `DO NOT MERGE` test PRs described in step 5. Tell the human to return when all runs are done, and stop after any parallel external-test handoff is also made. While this gate is open, those temporary test PRs are the only permitted mutation; do not create a Clinical Data Explorer release branch, tag, release, or registry change. When the human says the runs are done, require valid passing evidence for every required cell on every matrix row and obtain the exact Domino version tested by each cell. Each cell may map to its own PR and run; do not require both trigger types on the same PR, but require both streams to pass the same exact Domino version before claiming that version as tested. Only an unambiguous “yes” with complete passing evidence clears this gate. The stable-release Extension Installer scenario is not candidate evidence and must be reported separately if it ran.
- **Version:** present the changelog, an exact SemVer recommendation, and the evidence for its bump class. Obtain confirmation of the exact `X.Y.Z` version before creating the release worktree or branch.
- **Release PR:** after opening the release PR, ask the human to review it and tell you when it is merged. Stop. Create no tag before independently verifying the merge.
- **Stable publication:** immediately before the stable tag and GitHub release, show the exact commit, tag, changelog, and proposed registry compatibility fields with their evidence, then obtain explicit approval.
- **Registry PR:** after opening the `release-X.Y.Z-extension_versions` PR against `extension_versions`, ask the human to review it and tell you when it is merged. Stop and do not bypass review with a direct push.

## Changelog and SemVer

Derive the changelog from the latest published, non-draft, non-prerelease GitHub release through updated `origin/main`. Use the content diff, merged PRs, commit history, prior release notes, and relevant source/tests. Exclude release bookkeeping, duplicated squash commits, and reverted work.

If the stable tag is not an ancestor of `main`, find the first-parent `main` commit with the same tree as the tag and use that as the history/PR baseline. Still use the stable tag-to-main content diff. If no trustworthy baseline exists, stop and reconcile it with the human.

Summarize user-visible features, fixes, compatibility or operational changes, and relevant documentation. Call this summary the **changelog** and reuse it in the release PR and GitHub release notes.

Recommend the highest applicable SemVer bump:

- major for incompatible behavior, removed support, required migration, or a broken public/installer contract;
- minor for backward-compatible user-visible capabilities or supported surfaces;
- patch for backward-compatible fixes, maintenance, dependency, build, test, or documentation changes.

## Domino compatibility

Use the strongest available evidence to determine the registry compatibility fields. Start with the prior stable entry, then inspect the release diff, dependency and base-image changes, manifest surfaces, Domino API routes or generated clients, injected environment variables, feature flags, repository documentation, linked PRs and tickets, Domino source and release notes, and completed e2e results. Set `minDominoVersion` to the highest proven minimum among mandatory dependencies for the extension's advertised workflows. Do not lower the previous minimum without affirmative evidence; raise it when a new requirement has a higher documented floor. Treat manual configuration and backport-only support as conditional and ask the human whether to document and test it or raise the minimum.

Treat `minDominoVersion` and `maxDominoVersion` as inclusive installer gates. Add a maximum only for a documented incompatibility; the newest tested version is not a maximum. Put an exact, human-confirmed Domino version in `testedWithDominoVersions` only when both candidate-backed Nexus and Cucu passed that same exact version. The current trigger flow does not directly record the Clinical Data Explorer commit cloned by both test surfaces. Require `main` to remain at the recorded candidate SHA through result collection, describe that unchanged ref as a conservative binding proxy rather than direct SHA proof, and never present inferred, historical, or moving-branch compatibility as test evidence.

Before triggering candidate e2e, turn the compatibility conclusion into a finite test matrix. Map each relevant supported Domino release line to its current base branch in `cerebrotech/internal-e2e-tests-service` and always include that repository's `main`. Treat a branch name as routing information, not proof of the exact Domino version deployed by a run. Do not select every numerically newer historical branch automatically. Inspect the repository's current branch and CI conventions, and ask the human rather than guessing when the mapping is absent or ambiguous.

Include the compatibility conclusion, evidence, and uncertainty in the `extension_versions` PR description. The Cloud Dogfood Gateway URL may remain in repository test documentation as an explicitly approved public test endpoint, but do not repeat it or any other deployment hostname in the registry PR. Never expose credentials, secrets, or internal run details; use an access-controlled result link when appropriate.

## Execute the approved process

Follow the numbered process and invariants in the reference exactly. In particular:

- branch as `release-X.Y.Z` from freshly fetched `origin/main` in a new worktree;
- update every true release-version reference, while asking before changing apparently independent package versions;
- keep names and descriptions stable unless the changelog makes them materially inaccurate;
- run the documented local suites, including `make test-external` as a separate command whose pytest outcome is inspected and reported; never treat the Docker smoke workflow as e2e evidence;
- when any required candidate e2e matrix cell has not run or has direct human approval to rerun after failure, create or reuse one empty-commit draft PR in the internal-e2e repository against each affected selected release branch or `main`, while Clinical Data Explorer `main` remains at the recorded candidate SHA; title every PR exactly `DO NOT MERGE`, then post exactly one candidate-only Nexus or Cucu trigger for each not-run or rerun-approved cell as a separate comment;
- put `extension_manifest.json` on the release branch/tag and `extension_versions.json` only on its dedicated registry branch;
- merge the release PR before creating the stable tag;
- do not create provisional tags, provisional releases, or provisional registry entries, and do not perform Extension Installer validation;
- tag the verified release-PR merge commit, push the tag, then create a GitHub release named exactly after the tag with the changelog as its notes;
- after the GitHub release exists, use the required registry-branch exception: branch `release-X.Y.Z-extension_versions` from freshly fetched `origin/extension_versions`, open a PR against `extension_versions`, and wait for human review and merge. Present this exception for explicit confirmation whenever it is newly introduced.

Every PR body opened by this workflow, including temporary internal-e2e test PRs, must mention `@niole`. PR titles and descriptions must not mention Codex. Do not add a `Validation` section to a PR description. Never merge a test PR titled `DO NOT MERGE`.

## Finish with evidence

Report the stable version and commit; release PR; local test results; whether the standalone external tests actually executed; the human-reported external-test result; the internal-e2e test PRs, target branches, and trigger results; human-confirmed candidate-backed Nexus and Cucu evidence and its candidate-binding limitation; stable release URL; compatibility evidence; registry PR and final registry entry; and any unresolved caveat. Distinguish observed evidence from human confirmation.
