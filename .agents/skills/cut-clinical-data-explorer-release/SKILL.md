---
name: cut-clinical-data-explorer-release
description: Prepare, cut, resume, or publish a Clinical Data Explorer release. Use for release readiness, changelogs, SemVer selection, release branches or PRs, provisional installer validation, tags, GitHub releases, and extension_versions publication in dominodatalab/clinical-data-explorer.
---

# Cut a Clinical Data Explorer release

Run this workflow only in `dominodatalab/clinical-data-explorer`. It is deliberately human-gated because tags and published extension registry entries affect customer-visible releases.

Before doing anything else, read [references/release-process.md](references/release-process.md) completely. Treat it and this file as the skill implementation.

## Start with a live process audit

Before changing Git, GitHub, release metadata, or the skill:

1. Fetch the current release sections from both linked Confluence runbooks using an authenticated connector or authenticated browser:
   - Clinical Data Explorer Runbook
   - Extensions Installer Runbook, including official-repository requirements, release steps, and provisional releases
2. Inspect the current local repository, GitHub repository metadata, releases, tags, default branch, `extension_manifest.json`, and the remote `extension_versions` branch.
3. Map every live runbook requirement to the implementation in this file and the reference. Include intentional reconciliations and repository-specific safeguards.
4. If a requirement is missing, stale, contradictory, or implemented incorrectly, make no release mutation. Show the human a compact comparison of the source requirement, current behavior, and recommended edit.
5. After the human accepts an edit, update this skill in a dedicated new worktree and branch from freshly fetched `origin/main`, never in a release worktree. Print the complete renumbered release process and ask whether it is correct. Continue revising and reprinting it until the human explicitly agrees. Keep release execution paused until the agreed process update is merged or the human explicitly chooses to continue from the reviewed local skill change.
6. Whether or not the audit found drift, print the complete numbered release process and ask whether it is correct. Do not pass the process gate on approval of a summary alone.

If either runbook cannot be read, stop and say which access path failed. Ask the human to restore access or provide the current content; do not substitute the stored snapshot silently. Do not edit Confluence unless separately asked.

## Discover state before acting

This workflow is resumable. On every invocation, inspect existing release branches and PRs, remote tags, GitHub releases, and both metadata files. Verify any existing artifact against its expected commit and content, then resume at the first incomplete gate. Resume only a human-named candidate or one unambiguous candidate whose stable publication is incomplete. Provisional artifacts for a version that has both a published stable release and a valid stable registry entry are historical unless the human explicitly says otherwise. Never recreate a completed action blindly.

Use authenticated GitHub tooling before a browser and never use unauthenticated raw HTTP when an authenticated client is available. Preserve unrelated local changes. Never move, reuse, or delete a release tag to repair a failed attempt.

## Required human gates

Stop and wait at each gate below. Do not infer approval from earlier authorization.

- **Process:** the human explicitly agrees that the complete audited process is correct.
- **Cucu e2e:** ask whether the Clinical Data Explorer Cucu e2e suite passed against the exact updated `main` SHA. Only an unambiguous “yes” counts; blank, missing, ambiguous, or negative input means “no” and blocks all release mutations. If yes, obtain the exact Domino version or versions tested.
- **Version:** present the changelog, an exact SemVer recommendation, and the evidence for its bump class. Obtain confirmation of the exact `X.Y.Z` version before creating the release worktree or branch.
- **Release PR:** after opening the release PR, ask the human to review it and tell you when it is merged. Stop. Create no tag before independently verifying the merge.
- **Provisional install:** after publishing provisional metadata, ask the human to confirm that the Extension Installer successfully installed it. A failure starts a new immutable provisional iteration.
- **Stable publication:** immediately before the stable tag, GitHub release, and customer-visible registry update, show the exact commit, tag, changelog, and registry entry and obtain explicit approval.

## Changelog and SemVer

Derive the changelog from the latest published, non-draft, non-prerelease GitHub release through updated `origin/main`. Use the content diff, merged PRs, commit history, prior release notes, and relevant source/tests. Exclude release bookkeeping, duplicated squash commits, and reverted work.

If the stable tag is not an ancestor of `main`, find the first-parent `main` commit with the same tree as the tag and use that as the history/PR baseline. Still use the stable tag-to-main content diff. If no trustworthy baseline exists, stop and reconcile it with the human.

Summarize user-visible features, fixes, compatibility or operational changes, and relevant documentation. Call this summary the **changelog** and reuse it in the release PR and GitHub release notes.

Recommend the highest applicable SemVer bump:

- major for incompatible behavior, removed support, required migration, or a broken public/installer contract;
- minor for backward-compatible user-visible capabilities or supported surfaces;
- patch for backward-compatible fixes, maintenance, dependency, build, test, or documentation changes.

## Execute the approved process

Follow the numbered process and invariants in the reference exactly. In particular:

- branch as `release-X.Y.Z` from a freshly fetched `origin/main` in a new worktree;
- update every true release-version reference, while asking before changing apparently independent package versions;
- keep names and descriptions stable unless the changelog makes them materially inaccurate;
- run the documented local suites; never treat the Docker smoke workflow as e2e evidence;
- put `extension_manifest.json` on the release branch/tag and `extension_versions.json` only on the dedicated registry branch;
- merge the release PR before creating any provisional or stable tag;
- create a provisional tagged-only commit after merge whose diff from the stable merge commit is limited to mechanical provisional-version substitutions;
- tag the verified PR merge commit for the stable release, push the tag, then create a GitHub release named exactly after the tag with the changelog as its notes;
- publish the stable registry entry only after the GitHub release exists.

PR titles and descriptions must not mention Codex. Do not add a `Validation` section to a PR description.

## Finish with evidence

Report the stable version and commit; release PR; local test results; human-confirmed Cucu/installer evidence; provisional and stable release URLs; final registry entry; and any unresolved caveat. Distinguish observed evidence from human confirmation.
