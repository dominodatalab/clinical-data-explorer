# Clinical Data Explorer release process

This is the repository's implemented release workflow. Audit it against the live sources before every release; the source snapshots below are drift detectors, not substitutes for live access.

## Sources last reconciled

- [Clinical Data Explorer Runbook](https://dominodatalab.atlassian.net/wiki/spaces/ENG/pages/4218191891/Clinical+Data+Explorer+Runbook), page `4218191891`, version 29, updated 2026-10-20.
- [Extensions Installer Runbook](https://dominodatalab.atlassian.net/wiki/spaces/ENG/pages/4263706625/Extensions+Installer+Runbook), page `4263706625`, version 32, updated 2026-07-16.
- [Clinical Data Explorer repository](https://github.com/dominodatalab/clinical-data-explorer), its local checkout, Git history, tags, releases, PRs, and `extension_versions` branch.

## Runbook coverage and reconciliations

| Source requirement | Implemented here |
| --- | --- |
| Local tests and Cucu e2e tests pass | Steps 4, 5, and 10 require local suites and explicit human Cucu evidence for an exact `main` SHA. |
| Update `main`, create `release-X.Y.Z` from it | Step 7 requires a new worktree from freshly fetched `origin/main`. |
| Validate `extension_manifest.json`, tags, and `extension_versions.json` | Steps 8, 9, and 13 through 15 validate the tagged manifest and the separately reviewed registry change. |
| Create a GitHub release | Step 13 pushes the stable tag first, then creates a release named after it with the changelog. |
| Merge the release branch back to `main` | Step 12 merges before the tag, as the user's explicit workflow requires. This intentionally reverses the CDE runbook's merge-last order. |
| Official repo is public, in `dominodatalab`, and has topic `domino-official-extension` | Step 3 treats all three as hard prerequisites. |
| Registry versions map to GitHub releases and tagged manifests | Steps 14 and 15 enforce exact version/tag equality and publication ordering. |
| Provisional releases can be used for installer testing | The user explicitly chose not to create provisional artifacts or perform Extension Installer validation. Local tests, Cucu evidence, immutable stable publication, and two human-reviewed PRs are the replacement gates. |
| Registry compatibility describes supported and tested Domino versions | Steps 4, 5, and 14 require evidence-based compatibility research and restrict `testedWithDominoVersions` to confirmed passing versions. |

The following reconciliations are intentional and must be included when they are new differences at the process-agreement gate:

1. **Merge before tagging.** The release PR must merge before the stable tag is created. This overrides the CDE runbook's merge-last order.
2. **No provisional workflow.** Do not create provisional tags, GitHub prereleases, or provisional registry entries, and do not validate through the Extension Installer. This intentionally omits the installer runbook's provisional path.
3. **Human-reviewed registry PR and branch-base exception.** Never push release metadata directly to `extension_versions`. To produce a clean PR against the special long-lived `extension_versions` branch, create `release-X.Y.Z-extension_versions` from freshly fetched `origin/extension_versions`, not from `main`. This is the sole exception to the default-branch rule and must be presented for explicit human confirmation when first introduced. Wait for the human to review and merge the PR.
4. **Metadata locations.** `extension_manifest.json` belongs in released code and the stable tag. `extension_versions.json` belongs only on the dedicated registry branch.
5. **Compact plan confirmation.** Show humans only differences from the last agreed process. If there are no differences, say so. Print the full process only when requested.
6. **PR notification.** Every PR body opened by this workflow must mention `@niole`.

## Numbered release process

### 1. Audit the live process

Read both live runbooks and the current skill. Produce a requirement-to-implementation comparison internally. If any unaccounted requirement differs, propose an exact skill edit and do no release work. Make accepted skill edits in a dedicated worktree and branch from freshly fetched `origin/main`, not in a release worktree. Keep release execution paused until the agreed skill update is merged or the human explicitly chooses to continue from the reviewed local change.

At the process gate, show only differences from the last agreed plan and ask whether they are correct. If nothing changed, say there are no differences and ask for confirmation. Do not print this complete process unless the human asks for it.

### 2. Discover or resume release state

Inspect local worktrees and status, remote release and registry branches, open and merged PRs, remote tags, GitHub releases, and the remote `extension_versions.json`. Resume only a candidate version the human named or one unambiguous candidate whose stable publication is incomplete. A correct stable tag without a GitHub release, or a stable release without a merged and valid registry entry, is still incomplete. If multiple candidates could be active, ask which one to resume.

Historical provisional artifacts are never resumable candidates and this workflow creates no new ones. If an existing release artifact points at the wrong commit or contains unexpected data, stop; never move, reuse, or delete a tag.

### 3. Verify release prerequisites

Confirm:

- the repo is the public `dominodatalab/clinical-data-explorer` repository;
- its default branch is `main` and the `domino-official-extension` topic is present;
- authenticated GitHub access can read releases and PRs and perform later authorized writes;
- `extension_manifest.json` exists at repository root and parses as JSON;
- `origin/extension_versions` exists and its root `extension_versions.json` parses as JSON;
- every stable registry entry has `version == releaseTag` without the leading `v`, and its tag and published release exist;
- the intended manifest and registry schema versions remain supported by the live installer specification.

### 4. Refresh `main`, build the changelog, research compatibility, and run local tests

Fetch `origin/main`, `origin/extension_versions`, tags, and release metadata. Record the exact `origin/main` SHA.

Find the latest published GitHub release that is neither draft nor prerelease; do not choose the highest tag because historical provisional tags exist. When a squash-merged release tag is not an ancestor of `main`, locate the first-parent `main` commit whose tree equals the stable tag's tree and use it as the history baseline. Use the stable-tag-to-`origin/main` diff for net content.

Read the relevant merged PRs, implementation, tests, and prior notes. Produce the changelog and identify major, minor, and patch evidence.

Build a preliminary Domino compatibility assessment from the strongest available evidence:

- the previous stable registry entry and earlier compatibility history;
- explicit requirements in `README.md`, `INSTALL.md`, `QUICKSTART.md`, `ARCHITECTURE.md`, the manifest, and live runbooks;
- changed dependencies, base images, manifest surfaces, Domino API routes or generated clients, injected environment variables, mount points, feature flags, authentication behavior, and other platform integrations;
- linked PRs, Jira issues, Domino source, fix versions, official release notes, and design or rollout notes that name a minimum, maximum, or tested Domino version;
- relevant Cucu coverage and completed results.

Set `minDominoVersion` to the highest proven minimum among mandatory dependencies needed by the extension's advertised workflows. Do not assume that the newest known Domino version is the minimum. Do not lower the previous minimum without affirmative evidence that the broader range is supported. Otherwise preserve that floor unless a changed requirement has a higher documented minimum, and record the reasoning. Treat older-version support that depends on manual configuration or a backport as conditional: show the condition to the human and either document and test it or raise the minimum.

Treat `minDominoVersion` and `maxDominoVersion` as inclusive installer gates. Add `maxDominoVersion` only for a documented incompatibility and omit it when no defensible upper bound exists; the newest tested version is not a maximum. Treat test coverage separately from inferred support.

On a clean checkout of the exact `main` SHA, run:

```text
make test-all
make test-frontend
```

Also run `make test-external` when the changelog touches chat or an external integration. If required credentials are unavailable, report that as an unmet release gate rather than silently skipping it. The PR Docker workflow is only a smoke build and is not a replacement for these suites.

### 5. Obtain Cucu e2e evidence

Ask: “Did the Clinical Data Explorer Cucu e2e suite pass against `main` at `<sha>`? Please answer yes or no.” Only an unambiguous yes counts. Blank, missing, ambiguous, or negative input means no and stops the release before mutations.

For a yes, ask which exact Domino version or versions passed and record the human's answer for `testedWithDominoVersions`. Never infer tested versions or invent TestRail IDs. De-duplicate and sort the confirmed versions, and bind them to completed results for the recorded candidate SHA.

For a release from `main`, run both of these triggers on an internal-e2e service PR when execution is needed:

```text
/test-e2e nexus-global-config --tags=clinical-data-explorer
/test-e2e cucu --tags=clinical-data-explorer
```

For a non-main candidate, manually run `CDE_VERSION=<ref> cucu run --tags=@clinical-data-explorer` from the internal e2e service checkout. Posting a trigger or starting a command is not passing evidence; require completed results tied to the exact `main` SHA and human-confirmed Domino versions.

### 6. Recommend and confirm the version

Present the changelog, current stable version, candidate major/minor/patch evidence, and one exact SemVer recommendation. Include the preliminary compatibility conclusion and any unresolved evidence gap. Ask the human to confirm the exact `X.Y.Z`.

Verify the chosen version is newer than the latest stable version. Treat an existing remote release branch, tag, GitHub release, registry branch, registry PR, or registry entry as resumable only when it belongs to the verified active candidate from step 2; otherwise treat it as a conflict and stop. Do not create anything until the exact version is confirmed.

### 7. Create the release worktree and branch

Fetch `origin/main` again. If it no longer equals the SHA used for the changelog and e2e evidence, return to step 4.

Create a new worktree and branch from `origin/main` named exactly:

```text
release-X.Y.Z
```

Do not include `v` in the branch name. Never repurpose an existing dirty worktree.

### 8. Audit every release-version reference

Search the entire checkout for the previous stable version, version-like release metadata, and tag strings. Classify each match before editing.

Known true release metadata includes:

- `extension_manifest.json` → `app.computeEnvironment.customEnvironment.buildArgs.EXTENSION_VERSION`, which must be `vX.Y.Z`;
- `extension_manifest.json` → `app.appVersionDescription`, which must use `X.Y.Z`;
- `Dockerfile` → `LABEL version`, which must use `X.Y.Z`.

`pyproject.toml` and the root package entry in `uv.lock` have historically used an independent package version. Flag them and ask before changing them; do not assume they track the extension release.

### 9. Update and validate release metadata

Update every true release-version reference to the confirmed stable version. Compare `extension_manifest.json` with the changelog and current implementation so mount points, file types, build arguments, base image, and other release behavior remain accurate.

Be conservative with project, app, and extension names and descriptions. Change them only when the changelog makes the current text materially inaccurate, and call out the proposed wording.

Validate:

- JSON syntax and the live `ExtensionManifestV1` schema;
- all required manifest fields;
- every stable version reference is consistent;
- the Dockerfile can clone the future stable tag through `EXTENSION_VERSION`;
- the metadata diff contains no unrelated edits.

### 10. Test the release branch

Run `make test-all` and `make test-frontend` again. Run `make test-external` when required by the changelog. Fix failures before continuing and keep the changelog current if a fix changes behavior.

### 11. Push and open the release PR

Commit and push `release-X.Y.Z`, then open a PR to `main`. Use a conventional title such as `Cut vX.Y.Z release`. Use the changelog in the description and mention `@niole`.

Do not mention Codex and do not add a `Validation` section. Report test results outside the PR description unless the human asks for a different established template.

### 12. Stop for release-PR review and verify the merge

Ask the human to review the release PR and tell you after it is merged. Stop and create no tag.

When the human returns, independently verify the PR is merged, fetch `origin/main`, and record the PR's merge commit. Confirm the merged tree contains the final stable metadata and matches the reviewed release tree. Because this repository squash-merges, compare trees rather than requiring identical commit IDs.

If unrelated or functional changes entered the release tree, return to the changelog, test, e2e, compatibility, and version gates as appropriate. Do not tag a moving `main` tip; use the verified merge commit explicitly. If the release PR changed functional release metadata rather than only version values, obtain Cucu evidence for the merged `main` SHA before continuing.

### 13. Publish the stable tag and GitHub release

Show the human:

- the verified stable merge commit;
- the exact `vX.Y.Z` tag;
- the changelog and release notes;
- the proposed registry compatibility fields and the evidence supporting them.

Ask for explicit stable-publication approval. Then create a lightweight `vX.Y.Z` tag at the verified merge commit and push it. Only after the push succeeds, create the GitHub release:

- release name: exactly `vX.Y.Z`;
- target: the already-pushed tag;
- notes: the changelog, with useful PR links and a comparison link;
- not draft and not prerelease.

If tag push succeeds but release creation fails, keep the immutable tag and resume by creating only the missing release.

### 14. Open the `extension_versions` PR

Only after the stable GitHub release exists, fetch `origin/extension_versions` again. In a new clean worktree based on that ref, create the branch:

```text
release-X.Y.Z-extension_versions
```

This branch intentionally starts from `origin/extension_versions`, not `main`, because its PR base is `extension_versions`. This is the required exception to the repository's default-branch rule; every other new branch still starts from updated `main`. Do not rely on the stored wording as approval when the exception is newly introduced—present it at the process gate and obtain explicit confirmation.

Revalidate the approved compatibility conclusion using the evidence collected in steps 4 and 5. If evidence has changed, conflicts, or cannot establish a safe minimum, stop and ask the human rather than guessing. Add one stable registry entry that follows the existing ordering and style:

- `manifestSchemaVersion`: the manifest schema version;
- `version`: `X.Y.Z`;
- `releaseTag`: `vX.Y.Z`;
- `isProvisional`: omit it or set `false` according to existing style;
- `minDominoVersion`: the best-supported minimum, with its evidence documented in the PR;
- `maxDominoVersion`: only when a concrete incompatibility establishes an upper bound;
- `testedWithDominoVersions`: only exact versions confirmed by completed e2e results.

Preserve every older stable entry. Validate the JSON, schema versions, exact display-version/tag relationship, existence of the published release, and the tagged manifest's release values.

Commit and push the branch, then open a PR whose base is `extension_versions`. The PR description must:

- mention `@niole`;
- show the exact proposed registry entry;
- state the proposed compatibility fields;
- summarize the evidence for the minimum and any maximum;
- distinguish inferred compatibility from human-confirmed tested versions, including the candidate SHA and result links when available;
- state any remaining uncertainty;
- omit deployment hostnames, credentials, secrets, and other internal run details because the repository is public; use access-controlled result links when appropriate;
- avoid mentioning Codex or adding a `Validation` section.

Ask the human to review the registry PR and tell you after it is merged. Stop. Never bypass this gate by pushing directly to `extension_versions`.

### 15. Verify registry publication

When the human returns, independently verify that the registry PR was merged. Fetch `origin/extension_versions` and confirm:

- `extension_versions.json` parses and contains the reviewed stable entry;
- the entry's `version` and `releaseTag` match exactly;
- its compatibility fields match the reviewed evidence;
- `testedWithDominoVersions` contains only human-confirmed passing versions;
- the stable tag targets the verified release commit;
- the published GitHub release exists and is named after the tag;
- the tagged `extension_manifest.json` contains the stable version everywhere required.

Do not perform Extension Installer validation. Remove temporary worktrees only after confirming they contain no uncommitted work.

## Repository-specific historical hazards

Check these rather than repeating them:

- `v1.0.1` points to release-branch commit `2d6fefe`, while PR #72 squash-merged as `f8fa418`; the trees match but the tag is not in `main` history.
- The `v1.0.0` tag was moved after its GitHub release. Treat all new release tags as immutable.
- Historical provisional tags and prereleases exist. Ignore them when finding the latest stable release and never resume them or create new ones.
- `Dockerfile` still labels the image as `1.0.0` on the source snapshot reviewed for this skill. Treat that label as release metadata.
- The PR Docker smoke workflow can pass without running the documented test suites and has historically mishandled captured logs. Do not use a green check as local or Cucu e2e evidence.
