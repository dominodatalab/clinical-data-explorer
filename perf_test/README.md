# CDE Performance Test Helpers

This directory contains small helpers for exercising Clinical Data Explorer data-load paths against a Domino deployment.

## Files

- `load_cde_other_dataset.py`: logs in through the Domino UI as practitioner users, opens each user's quick-start project, opens the CDE extension, selects the largest file from the `other` dataset source, and optionally clicks **Load File**.
- `test_data_load_req.sh`: sends one direct `/dataset/load` request using a bearer token.
- `test_all_practs_data_load_reqs.sh`: serially calls `test_data_load_req.sh` for `pract1`, `pract2`, etc. using tokens exported in the shell.
- `test_user_creds_example.txt`: template for local practitioner PAT exports.
- `test_user_creds.txt`: local helper containing `export practN=<token>` lines. This file is ignored by git because it contains sensitive PATs.

## UI Load Test

The UI script is the preferred helper when you need to exercise browser login, project navigation, CDE extension launch, authorization, file browsing, and sticky-session behavior.

Prerequisites:

- The CDE extension exists in the target Domino deployment and is named `CDE`.
- Users `pract1` through `pract10` exist and share the same password.
- Each user's quick-start project has an `other` dataset with at least one supported data file.
- Playwright dependencies are installed in the environment used by `uv`.

Run from the repo root:

```bash
PRACT_PASSWORD='<password>' uv run perf_test/load_cde_other_dataset.py --allow-load-failures
```

Useful options:

```bash
uv run perf_test/load_cde_other_dataset.py \
  --base-url 'https://nio2tst124524.engineering-dev.domino.tech/' \
  --users pract1 pract2 pract3 \
  --source-label other \
  --output perf_test/results.json \
  --screenshot-dir perf_test/screenshots \
  --allow-load-failures
```

Use `--headed` to watch the browser and `--no-click-load` to stop after selecting the largest file.

The script captures screenshots in `perf_test/screenshots` when automation fails or `/dataset/load` returns an error.

## Direct Data-Load Request

The shell scripts bypass the UI and call the deployment's `/dataset/load` endpoint directly. They are useful for quick endpoint checks, but they do not exercise UI login, CDE extension authorization, file browsing, or sticky-session behavior.

Configure the app and dataset target:

```bash
export CDE_APP_URL='https://<domino-host>/apps/<app-id>'
export CDE_DATASET_NAME='other/<file-name>.csv'
export CDE_DATASET_ID='<dataset-id>'
```

Create a local token file from the example, then fill it with each user's PAT:

```bash
cp perf_test/test_user_creds_example.txt perf_test/test_user_creds.txt
```

Remove the `_example` suffix by using the copied filename above, then edit `perf_test/test_user_creds.txt` so each `practN` value contains that user's PAT:

```bash
export pract1="<pract1-pat>"
export pract2="<pract2-pat>"
```

Load the PATs into the current shell:

```bash
source perf_test/test_user_creds.txt
```

Send one request:

```bash
./perf_test/test_data_load_req.sh "$pract1"
```

You can also pass the target explicitly:

```bash
./perf_test/test_data_load_req.sh \
  "$pract1" \
  'https://<domino-host>/apps/<app-id>' \
  'other/<file-name>.csv' \
  '<dataset-id>'
```

Send one request per practitioner user, serially:

```bash
./perf_test/test_all_practs_data_load_reqs.sh 10
```

`test_all_practs_data_load_reqs.sh` uses the same `CDE_APP_URL`, `CDE_DATASET_NAME`, and `CDE_DATASET_ID` environment variables.

## Monitoring

While running these tests, watch the app deployment and MCP server pods in Kubernetes, plus the run-container logs for the relevant app replicas. For load-related failures, compare the browser/script output with server logs around the `/dataset/load` request time.
