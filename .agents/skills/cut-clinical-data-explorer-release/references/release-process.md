# Clinical Data Explorer release process

This is the repository's implemented release workflow. Audit it against the live sources before every release; the source snapshots below are drift detectors, not substitutes for live access.

## Sources last reconciled

- [Clinical Data Explorer Runbook](https://dominodatalab.atlassian.net/wiki/spaces/ENG/pages/4218191891/Clinical+Data+Explorer+Runbook), page `4218191891`, version 28, updated 2026-10-08.
- [Extensions Installer Runbook](https://dominodatalab.atlassian.net/wiki/spaces/ENG/pages/4263706625/Extensions+Installer+Runbook), page `4263706625`, version 32, updated 2026-07-16.
- [Clinical Data Explorer repository](https://github.com/dominodatalab/clinical-data-explorer), its local checkout, Git history, tags, releases, PRs, and `extension_versions` branch.

## Runbook coverage and reconciliations

| Source requirement | Implemented here |
| --- | --- |
| Local tests and Cucu e2e tests pass | Steps 4 and 5 require local suites and explicit human Cucu evidence for an exact `main` SHA. |
| Update `main`, create `release-X.Y.Z` from it | Steps 7 and 8 require a new worktree from freshly fetched `origin/main`. |
| Validate `extension_manifest.json`, tags, `extension_versions.json`, and a provisional install | Steps 9 through 15 separate the tagged manifest from the registry branch and require an installer gate. |
| Create a GitHub release | Step 16 pushes the stable tag first, then creates a release named after it with the changelog. |
| Merge the release branch back to `main` | Step 12 merges before any tag, as the user's explicit workflow requires. This intentionally reverses the CDE runbook's merge-last order. |
| Official repo is public, in `dominodatalab`, and has topic `domino-official-extension` | Step 3 treats all three as hard prerequisites. |
| Registry versions map to GitHub releases and tagged manifests | Steps 14 through 17 enforce exact version/tag equality and publication ordering. |
| Use provisional releases without exposing them to customers | Steps 13 through 15 use `isProvisional: true`, installer confirmation, and an increment-only tag suffix. |

The following reconciliations are intentional and must be shown during the process-agreement gate:

1. **Merge before every tag.** The user explicitly requires the release PR to merge before a tag is created. This overrides the CDE runbook's merge-last order for both provisional and stable tags.
2. **Tagged-only provisional commit.** After the final-form manifest is merged, create a commit based on the verified merge commit that changes only true release-version values from stable to provisional form. Tag that commit without putting it on `main`. This lets `EXTENSION_VERSION` point to the provisional tag while keeping every tag after the PR merge.
3. **Mechanical commit difference.** The installer runbook says the stable release should use the same commit as the validated provisional release. That is impossible when the manifest must change from `vX.Y.Z-provisionalN` to `vX.Y.Z`. The stable merge commit and provisional tagged commit may differ only by audited mechanical version substitutions. Any functional or other metadata difference invalidates the provisional result.
4. **Canonical provisional spelling.** Use `vX.Y.Z-provisionalN`, with display version `X.Y.Z-provisionalN`. One installer-runbook JSON example includes an extra hyphen before the number, but its prose and this repository's tags do not.
5. **Metadata locations.** `extension_manifest.json` belongs in the released code and every tag. `extension_versions.json` belongs only on the dedicated `extension_versions` branch.

## Numbered release process

### 1. Audit the live process

Read both live runbooks and the current skill. Produce a requirement-to-implementation comparison. If anything differs, propose an exact skill edit and do no release work. Make accepted skill edits in a dedicated new worktree and branch from freshly fetched `origin/main`, not in a release worktree. After each edit, print this full numbered process and iterate until the human explicitly agrees it is correct. Even when the audit finds no drift, print the complete process and require explicit agreement; approval of a summary is insufficient. Keep release execution paused until the agreed process update is merged or the human explicitly chooses to continue from the reviewed local skill change.

### 2. Discover or resume release state

Inspect local worktrees and status, remote release branches, open and merged PRs, remote tags, GitHub releases, and the remote `extension_versions` JSON. Resume only a candidate version the human named or one unambiguous candidate whose stable publication is incomplete. A stable GitHub release with a missing or invalid stable registry entry is still incomplete. If multiple candidates could be active, ask which one to resume. Provisional artifacts whose base version has both a published stable release and a valid stable registry entry are historical unless the human explicitly directs otherwise.

Partial-state examples:

- For the verified active candidate only, if a correct tag exists but its GitHub release does not, create only the missing release.
- If the stable release exists but the registry update failed, resume only the registry update.
- If an existing artifact points at the wrong commit or contains unexpected data, stop; never move or reuse the tag.

### 3. Verify release prerequisites

Confirm:

- the repo is the public `dominodatalab/clinical-data-explorer` repository;
- its default branch is `main` and the `domino-official-extension` topic is present;
- authenticated GitHub access can read releases/PRs and perform the later authorized writes;
- `extension_manifest.json` exists at repository root and parses as JSON;
- `origin/extension_versions` exists and its root `extension_versions.json` parses as JSON;
- every stable registry entry has `version == releaseTag` without the leading `v`, and its tag/release exists;
- the intended schema version remains supported by the live installer specification.

### 4. Refresh `main`, build the changelog, and run local tests

Fetch `origin/main`, `origin/extension_versions`, tags, and release metadata. Record the exact `origin/main` SHA.

Find the latest published GitHub release that is neither draft nor prerelease; do not pick the highest Git tag because provisional tags exist. When a squash-merged release tag is not an ancestor of `main`, locate the first-parent `main` commit whose tree equals the stable tag's tree and use it as the history baseline. Use the stable tag-to-`origin/main` diff for net content.

Read the relevant merged PRs, implementation, tests, and prior notes. Produce the changelog and identify major, minor, and patch evidence.

On a clean checkout of that exact SHA, run:

```text
make test-all
make test-frontend
```

Also run `make test-external` when the changelog touches chat or an external integration. If required credentials are unavailable, report that as an unmet release gate rather than silently skipping it. The PR Docker workflow is only a smoke build and is not a replacement for these suites.

### 5. Obtain Cucu e2e evidence

Ask: “Did the Clinical Data Explorer Cucu e2e suite pass against `main` at `<sha>`? Please answer yes or no.” Only an unambiguous yes counts. Blank, missing, ambiguous, or negative input means no and stops the release before mutations.

For a yes, ask which exact Domino version or versions passed and record the human's answer for `testedWithDominoVersions`. Do not invent TestRail IDs or infer tested Domino versions. If the human needs a command, the runbook's shape is `CDE_VERSION=main cucu run --tags=@clinical-data-explorer` from the internal e2e service checkout.

### 6. Recommend and confirm the version

Present the changelog, current stable version, candidate major/minor/patch evidence, and one exact SemVer recommendation. Ask the human to confirm the exact `X.Y.Z`.

Verify the chosen version is newer than the latest stable version. Treat an existing remote branch, tag, GitHub release, or registry entry as resumable only when it belongs to the verified active candidate from step 2; otherwise treat it as a conflict and stop. Do not create anything until the exact version is confirmed.

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

Update every true release-version reference to the confirmed stable version. Compare `extension_manifest.json` with the changelog and current implementation so mount points, file types, build arguments, base image, and other installer behavior remain accurate.

Be conservative with project, app, and extension names and descriptions. Change them only when the changelog makes the current text materially inaccurate, and call out the proposed wording.

Validate:

- JSON syntax and the live `ExtensionManifestV1` schema;
- all required manifest fields;
- every stable version reference is consistent;
- the Dockerfile can clone the future stable tag through `EXTENSION_VERSION`;
- the metadata diff contains no unrelated edits.

### 10. Test the release branch

Run `make test-all` and `make test-frontend` again. Run `make test-external` when required by the changelog. Fix failures before continuing and keep the changelog current if the fix changes behavior.

### 11. Push and open the release PR

Commit and push `release-X.Y.Z`, then open a PR to `main`. Use a conventional title such as `Cut vX.Y.Z release`. Use the changelog in the description.

Do not mention Codex and do not add a `Validation` section. Report test results outside the PR description unless the human asks for a different established template.

### 12. Stop for review and verify the merge

Ask the human to review the PR and tell you after it is merged. Stop and create no tag.

When the human returns, independently verify the PR is merged, fetch `origin/main`, and record the PR's merge commit. Confirm the merged tree contains the final stable metadata and matches the reviewed release tree. Because this repository squash-merges, compare trees rather than requiring identical commit IDs.

If unrelated or functional changes entered the release tree, return to the changelog, test, e2e, and version gates as appropriate. Do not tag a moving `main` tip; all following tags use the verified merge commit explicitly.

If the release PR intentionally changed functional installer metadata rather than only release-version values, obtain Cucu evidence for the merged `main` SHA before continuing. The earlier readiness confirmation does not prove the merged metadata works.

### 13. Create the provisional tagged-only commit

Choose the next unused integer `N` after checking all remote tags and GitHub releases. Never retarget or reuse an old provisional tag, even if a release was deleted.

In a clean, detached temporary worktree at the verified merge commit, make only mechanical substitutions from:

```text
vX.Y.Z -> vX.Y.Z-provisionalN
X.Y.Z  -> X.Y.Z-provisionalN
```

Apply them only to the true release metadata identified in step 8. Do not change independent schema or package versions. Commit the provisional metadata locally. Compare the provisional commit with the stable merge commit and require an allowlisted diff containing only those version values. Validate the manifest and Dockerfile/tag consistency.

Create the lightweight tag `vX.Y.Z-provisionalN` at that commit and push the tag. Then create a GitHub prerelease whose name is exactly the tag and whose notes contain the changelog plus a clear provisional notice.

### 14. Publish the provisional registry entry

Use a separate clean, detached worktree based on freshly fetched `origin/extension_versions`; this special long-lived registry branch is not based on `main`. Add one entry that follows the existing file's ordering and style:

- `manifestSchemaVersion`: the manifest schema version;
- `version`: `X.Y.Z-provisionalN`;
- `releaseTag`: `vX.Y.Z-provisionalN`;
- `isProvisional`: `true`;
- `minDominoVersion`: a human-confirmed value, or the prior value only after confirming the changelog did not raise it;
- `testedWithDominoVersions`: only the exact versions confirmed in step 5.

Preserve all stable entries. Validate the JSON and exact tag/display-version relationship, commit, and push the detached commit to `extension_versions`. If direct push is disallowed, stop and ask the human how this special branch should be updated; do not fabricate a branch from `main`.

### 15. Validate with the Extension Installer

Ask the human to enable `com.cerebro.domino.extensions.installer.allowProvisionalVersions`, select the exact provisional version, and answer whether the relevant installer path succeeded. At minimum verify the version is discoverable, the install or upgrade completes, and the installed app starts. Test an upgrade from the latest stable release; also test a fresh install when the changelog or manifest changes could affect first-time provisioning.

If it failed, do not move or delete the tag. Diagnose the result. Any code, Dockerfile, or functional manifest fix must go through a new or updated `release-X.Y.Z` PR and merge before creating `provisionalN+1`. Remove or replace the stale provisional registry entry only through a new registry commit. Re-run affected tests and human gates; then repeat with the next integer.

### 16. Publish the stable tag and GitHub release

After provisional success, show the human:

- verified stable merge commit;
- exact `vX.Y.Z` tag;
- changelog/release notes;
- proposed stable registry entry;
- evidence that the provisional-versus-stable diff contains only allowed version substitutions.

Ask for explicit stable-publication approval. Then create a lightweight `vX.Y.Z` tag at the verified merge commit and push it. Only after the push succeeds, create the GitHub release:

- release name: exactly `vX.Y.Z`;
- target: the already-pushed tag;
- notes: the changelog, with useful PR links and comparison link;
- not draft and not prerelease.

If tag push succeeds but release creation fails, keep the immutable tag and resume by creating only the missing release.

### 17. Publish and verify the stable registry entry

Only after the stable GitHub release exists, update `origin/extension_versions` in a clean detached worktree. Replace/remove the provisional entry and add the stable entry:

- `version`: `X.Y.Z`;
- `releaseTag`: `vX.Y.Z`;
- `isProvisional`: omit it or set `false` according to existing file style;
- preserve the confirmed schema and compatibility values;
- include only human-confirmed `testedWithDominoVersions`.

Preserve older stable releases. Commit and push to `extension_versions`, then independently verify:

- stable tag target equals the verified merge commit;
- GitHub release exists, is published, and is named after the tag;
- tagged `extension_manifest.json` has the stable version everywhere required;
- remote `extension_versions.json` parses and points to that release;
- no obsolete provisional entry remains customer-selectable;
- the stable version appears in the installer when compatible.

Keep provisional tags and GitHub prereleases for audit history unless the human explicitly requests cleanup. Remove temporary worktrees only after confirming they contain no uncommitted work.

## Repository-specific historical hazards

Check these rather than repeating them:

- `v1.0.1` points to release-branch commit `2d6fefe`, while PR #72 squash-merged as `f8fa418`; the trees match but the tag is not in `main` history.
- The `v1.0.0` tag was moved after its GitHub release. Treat all new release tags as immutable.
- The `v1.0.0-provisional3` manifest points `EXTENSION_VERSION` at `v1.0.0-provisional2`. Require exact tag/build-arg equality.
- `Dockerfile` still labels the image as `1.0.0` on the source snapshot reviewed for this skill. Treat that label as release metadata.
- The PR Docker smoke workflow can pass without running the documented test suites and has historically mishandled captured logs. Do not use a green check as local or Cucu e2e evidence.
