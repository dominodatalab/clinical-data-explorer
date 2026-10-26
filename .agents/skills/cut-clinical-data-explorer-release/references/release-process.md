# Clinical Data Explorer release process

This is the repository's implemented release workflow. Audit it against the live sources before every release; the source snapshots below are drift detectors, not substitutes for live access.

## Sources last reconciled

- [Clinical Data Explorer Runbook](https://dominodatalab.atlassian.net/wiki/spaces/ENG/pages/4218191891/Clinical+Data+Explorer+Runbook), page `4218191891`, version 30, updated 2026-10-21.
- [Extensions Installer Runbook](https://dominodatalab.atlassian.net/wiki/spaces/ENG/pages/4263706625/Extensions+Installer+Runbook), page `4263706625`, version 32, updated 2026-07-16.
- [Clinical Data Explorer repository](https://github.com/dominodatalab/clinical-data-explorer), its local checkout, Git history, tags, releases, PRs, and `extension_versions` branch.
- [`cerebrotech/internal-e2e-tests-service`](https://github.com/cerebrotech/internal-e2e-tests-service), its live branches, trigger documentation and implementation, and `main` at `f04b6ac2a3a46b4ee8027d026303b58355d0ac8e`, reviewed 2026-10-23.

## Runbook coverage and reconciliations

| Source requirement | Implemented here |
| --- | --- |
| Local tests and Cucu e2e tests pass | Steps 4, 5, and 10 require local suites, a separately observed and human-reported external-test result, and candidate-bound Cucu runs across the selected Domino compatibility matrix with explicit human confirmation. |
| Run the documented internal-e2e GitHub triggers on a PR | Step 5 creates one branch-correct temporary PR per selected Domino release branch plus `main`, then posts the `nexus-global-config` and `cucu` commands as separate comments. |
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
3. **Human-reviewed registry PR and branch-base exception.** Never push release metadata directly to `extension_versions`. To produce a clean PR against the special long-lived `extension_versions` branch, create `release-X.Y.Z-extension_versions` from freshly fetched `origin/extension_versions`, not from `main`. This is the sole publication-branch exception inside the Clinical Data Explorer repository and must be presented for explicit human confirmation when first introduced. Wait for the human to review and merge the PR.
4. **Metadata locations.** `extension_manifest.json` belongs in released code and the stable tag. `extension_versions.json` belongs only on the dedicated registry branch.
5. **Compact plan confirmation.** Show humans only differences from the last agreed process. If there are no differences, say so. Print the full process only when requested.
6. **PR notification.** Every PR body opened by this workflow must mention `@niole`.
7. **Observable external-test handoff.** Run `make test-external` separately, report whether tests actually executed rather than trusting its exit code, then ask the human to run it against the exact functional candidate SHA and report the result. Selected skips or no selected external tests are not passing evidence; deselected non-external tests are expected.
8. **Approved external-test defaults.** The external-test target may default its Gateway URL and model to the repository's documented Cloud Dogfood test values, but it never supplies credentials. Warn when `API_KEY_OVERRIDE` is absent; if that override is used with the Cloud Dogfood default, it must be a PAT issued by cloud-dogfood. Never print or request the credential.
9. **Cross-version Cucu trigger PRs.** If Cucu has not already run, determine the relevant Domino compatibility test matrix first. In `cerebrotech/internal-e2e-tests-service`, create an empty-commit test PR against each selected release branch and `main`, title each one exactly `DO NOT MERGE`, and post the two documented `/test-e2e` triggers as separate comments. A test branch targeting a release branch starts from that freshly fetched target branch; this is an explicit test-only exception to the default-branch rule. The current e2e flow resolves Clinical Data Explorer from `main` without recording the resolved CDE commit, so `main` must remain at the recorded candidate SHA through result collection and this must be reported as a binding proxy rather than direct SHA proof. Never merge these PRs.

## Numbered release process

### 1. Audit the live process

Read both live runbooks and the current skill. Produce a requirement-to-implementation comparison internally. If any unaccounted requirement differs, propose an exact skill edit and do no release work. Make accepted skill edits in a dedicated worktree and branch from freshly fetched `origin/main`, not in a release worktree. Keep release execution paused until the agreed skill update is merged or the human explicitly chooses to continue from the reviewed local change.

At the process gate, show only differences from the last agreed plan and ask whether they are correct. If nothing changed, say there are no differences and ask for confirmation. If no trustworthy approval baseline exists, say so and present the current reconciliation list once. Do not print this complete process unless the human asks for it.

### 2. Discover or resume release state

Inspect local worktrees and status, remote release and registry branches, open and merged PRs, remote tags, GitHub releases, the remote `extension_versions.json`, and candidate-specific branches, PRs, trigger comments, and results in `cerebrotech/internal-e2e-tests-service`. Resume only a candidate version the human named or one unambiguous candidate whose stable publication is incomplete. A correct stable tag without a GitHub release, or a stable release without a merged and valid registry entry, is still incomplete. If multiple candidates could be active, ask which one to resume.

Historical provisional artifacts are never resumable candidates and this workflow creates no new ones. If an existing release artifact points at the wrong commit or contains unexpected data, stop; never move, reuse, or delete a tag.

### 3. Verify release prerequisites

Confirm:

- the repo is the public `dominodatalab/clinical-data-explorer` repository;
- its default branch is `main` and the `domino-official-extension` topic is present;
- authenticated GitHub access can read releases and PRs and perform later authorized writes;
- authenticated GitHub access can read `cerebrotech/internal-e2e-tests-service` and, when test runs are needed, create branches and PRs and post comments there;
- `extension_manifest.json` exists at repository root and parses as JSON;
- `origin/extension_versions` exists and its root `extension_versions.json` parses as JSON;
- every stable registry entry has `version == releaseTag` without the leading `v`, and its tag and published release exist;
- the intended manifest and registry schema versions remain supported by the live installer specification.

Before creating any Cucu trigger PR, verify on every selected internal-e2e base branch that `tests/common/clinical_data_explorer.Dockerfile` resolves `EXTENSION_VERSION` from `main` by default, the app setup also resolves the Clinical Data Explorer Git reference from `main` when no override is supplied, and the `@clinical-data-explorer` feature references `EXTENSION_CLINICAL_DATA_EXPLORER_IMAGE_NAME`, so CI can discover and build the required custom environment. If those files or variable shapes differ, inspect the branch's implementation and ask the human rather than guessing.

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

On a clean checkout of the exact `main` SHA, run each command separately:

```text
make test-all
make test-frontend
make test-external
```

The `make test-external` target supplies the approved test-only defaults `DOMINO_LLM_GATEWAY_URL=https://apps.cloud-dogfood.domino.tech/apps/llm_gateway` and `DOMINO_LLM_GATEWAY_MODEL=sonnet5-5` when those variables are absent, but it never supplies credentials. If `API_KEY_OVERRIDE` is set while the default Gateway URL is used, it must be a Domino PAT issued by cloud-dogfood and authorized for the selected model alias; a Domino runtime can instead use its access-token sidecar. If the target warns that `API_KEY_OVERRIDE` is unset and the run is outside Domino or fails for authentication, ask the human to configure authentication in their own environment without pasting the credential into chat. A passing sidecar-authenticated result needs no remediation. The warning itself neither passes nor fails the gate.

The standalone `make test-external` result is authoritative for the external-test gate even if `make test-all` also collected an external-marked test. Inspect the pytest summary instead of relying on the command exit status:

- at least one selected external test passing with no selected skips, failures, or errors means the external suite executed and passed;
- any selected failure or error means the external suite executed and failed;
- any selected skip, or no selected external tests, means the external suite did not fully execute;
- deselected non-external tests are expected from `pytest -m external` and do not affect this classification.

Report that classification and the exact summary to the human. Then ask the human to run `make test-external` against the recorded candidate SHA in an appropriately configured environment and report the exact pytest result back. Do not ask the human to paste credentials, deployment hostnames, or other secrets into chat; an access-controlled result link is appropriate. When Cucu has not run, continue to step 5 and start its PR matrix while this human external-test result is pending; both gates remain blocking for step 6 and later release mutations. Only an unambiguous report that at least one selected external test passed and none skipped, failed, or errored clears the external gate. A blank, ambiguous, skipped, uncollected, failed, or errored result blocks creation of the release branch.

The PR Docker workflow is only a smoke build and is not a replacement for these suites.

### 5. Obtain or start Cucu e2e evidence

Ask: “Have the Clinical Data Explorer Cucu e2e tests already been run against `main` at `<sha>` for every relevant Domino version? Please answer yes or no.” A “no” means the tests have not run; it does not end the release workflow.

Classify the gate as **passed**, **not run**, **pending or partial**, or **failed**. A pending or partial matrix reuses the correct existing PRs and waits; it does not post duplicate triggers. A failed matrix blocks the release and is not rerun automatically. Report the failure and wait for the human to request or approve remediation and a rerun.

If the answer is yes, inspect the available run evidence and ask whether both required runs passed on every relevant Domino version. Obtain the exact Domino versions tested and the result links when available. De-duplicate and sort the confirmed versions, bind them to the recorded candidate SHA, and never infer an exact tested version from a branch name or invent TestRail IDs.

If the tests have not run, do all of the following:

1. Complete the compatibility assessment from step 4 and select a finite test matrix. Include `main` and one current internal-e2e release branch for each relevant supported Domino release line. Prefer an exact `release-X.Y.Z` ref for an exact `X.Y.Z` test target, but inspect live branch and catalog metadata because naming is not uniform. Record the evidence connecting each branch to the intended release line. Do not automatically include every historical branch at or above `minDominoVersion`; if multiple branches could represent the same line or the mapping is otherwise ambiguous, ask the human which branch to use.
2. Search existing internal-e2e branches and PRs for this exact Clinical Data Explorer candidate SHA and target matrix. Key resumability on the candidate SHA recorded in the PR body, base branch, and current internal-e2e PR head, not on the non-unique title alone. Reuse an open PR only when its base branch, candidate metadata, empty tree diff, current-head trigger status, and comments are correct. A candidate-related PR with a non-empty or uncertain diff is not valid for this flow: do not mutate, retrigger, duplicate, or treat it as release evidence. Show it to the human and wait for direction before creating a clean replacement. If a matching clean PR exists but only its title or body is wrong, update it only when it is clearly the current user's disposable test PR, otherwise ask the human.
3. For each missing target, freshly fetch the target base and create a unique test-only branch and worktree from that exact ref. Use a branch name that includes the Clinical Data Explorer candidate short SHA and sanitized target base so collisions and stale branches are visible. The branch targeting `main` starts from updated `main`; a branch targeting a release branch starts from that updated release branch so its PR diff and CI context are correct. This release-branch behavior is an explicit exception for these temporary test PRs.
4. Create one true empty commit with `git commit --allow-empty`; do not edit a marker, Dockerfile, feature, or other repository file merely to make GitHub accept the PR. Verify the commit's tree equals its parent tree.
5. Push the test branch and open a draft PR to the selected base branch with the title exactly `DO NOT MERGE`. The body must mention `@niole`, identify the Clinical Data Explorer candidate SHA, intended Domino release line, and exact base branch, and say that the PR exists only to trigger release testing. Do not mention Codex or add a `Validation` section.
6. Immediately before posting the first trigger, refetch Clinical Data Explorer `origin/main` and verify it still equals the recorded candidate SHA; if it moved, return to step 4. Then, on every test PR, post these as two separate comments, in this order, with no surrounding prose or code fence:

   ```text
   /test-e2e nexus-global-config --tags=clinical-data-explorer
   ```

   ```text
   /test-e2e cucu --tags=clinical-data-explorer
   ```

7. Verify each PR has the intended base, exact title, empty tree diff, candidate metadata, and both visible trigger comments. After the automation acknowledges them, verify each status comment names the current internal-e2e PR head commit and that the reported catalog matches the intended base branch; a run routed to a different catalog is invalid. Do not add commits after posting the triggers. Report the PR URL and target branch for every matrix row. Tell the human: “The Cucu runs have been started. Let me know when all of them are done.” Then stop. Do not merge or close the test PRs unless the human separately asks.

When the human says the runs are done, inspect the result comments and checks on every test PR, then ask: “Did both Clinical Data Explorer runs pass on every test PR? Please answer yes or no and confirm the exact Domino version tested by each PR.” Refetch Clinical Data Explorer `origin/main` while collecting that answer. Only an unambiguous yes supported by complete results for both triggers on every matrix row, with `origin/main` still at the recorded candidate SHA, clears this gate. If `main` moved, the runs are not usable candidate evidence; return to step 4 and rerun for the new candidate. A missing, pending, cancelled, skipped, failed, errored, wrong-head, or wrong-catalog result blocks the release; report the per-branch status and rerun or remediate before asking again. Record the candidate SHA, exact human-confirmed Domino versions, PR URLs, and result links for `testedWithDominoVersions`, and explicitly state that unchanged `main` is a conservative binding proxy because the jobs do not directly report the resolved CDE commit.

For a non-main Clinical Data Explorer candidate, retain the live runbook's manual path: run `CDE_VERSION=<ref> cucu run --tags=@clinical-data-explorer` against each selected Domino version instead of silently substituting the PR trigger path. Starting a trigger or command is not passing evidence.

### 6. Recommend and confirm the version

Present the changelog, current stable version, candidate major/minor/patch evidence, and one exact SemVer recommendation. Include the compatibility conclusion, completed test matrix, and any unresolved evidence gap. Ask the human to confirm the exact `X.Y.Z`.

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

Run `make test-all`, `make test-frontend`, and `make test-external` again as separate commands, and report the standalone external-test classification from step 4. When the release branch differs from the human-tested `main` candidate only in release-version metadata, retain the external and Cucu results and record why they apply. Ask the human to rerun `make test-external` and return to step 5 for a new candidate-bound Cucu matrix when functional or integration behavior changed, the branch was rebased onto a different candidate, or no valid passing result exists. Any unresolved test gate blocks pushing or opening the release PR and all later publication actions. Fix failures before continuing and keep the changelog current if a fix changes behavior.

### 11. Push and open the release PR

Commit and push `release-X.Y.Z`, then open a PR to `main`. Use a conventional title such as `Cut vX.Y.Z release`. Use the changelog in the description and mention `@niole`.

Do not mention Codex and do not add a `Validation` section. Report test results outside the PR description unless the human asks for a different established template.

### 12. Stop for release-PR review and verify the merge

Ask the human to review the release PR and tell you after it is merged. Stop and create no tag.

When the human returns, independently verify the PR is merged, fetch `origin/main`, and record the PR's merge commit. Confirm the merged tree contains the final stable metadata and matches the reviewed release tree. Because this repository squash-merges, compare trees rather than requiring identical commit IDs.

If unrelated or functional changes entered the release tree, return to the changelog, test, e2e, compatibility, and version gates as appropriate. Do not tag a moving `main` tip; use the verified merge commit explicitly. If the release PR changed functional release metadata rather than only version values, return to step 5 and obtain the full Cucu matrix for the merged `main` SHA before continuing.

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

This branch intentionally starts from `origin/extension_versions`, not `main`, because its PR base is `extension_versions`. This is the required publication-branch exception inside the Clinical Data Explorer repository; every other new branch in this repository starts from updated `main`. The separate internal-e2e test-only branch exception is defined in step 5. Do not rely on the stored wording as approval when the registry exception is newly introduced—present it at the process gate and obtain explicit confirmation.

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
