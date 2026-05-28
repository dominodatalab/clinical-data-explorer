# Adding CDISC Dataset-JSON support to the Data Explorer

This plan adds three new file extensions to the dataset loader: `.json`,
`.ndjson`, and `.dsjc`. All three are encodings of the CDISC
**Dataset-JSON v1.1** standard, the modern pharma-friendly replacement for
SAS XPT (which is what `.xpt` files in `datasets/testing_json/` are — same
data, different container).

## Background: what are these three extensions?

CDISC defines **one** logical dataset (metadata header + columns + rows)
and **three** ways to serialize it:

| Ext       | Encoding                                                         | Spec link                                                               | Notes                              |
|-----------|------------------------------------------------------------------|--------------------------------------------------------------------------|------------------------------------|
| `.json`   | Compact: a single JSON object with `columns: [...]` and `rows: [[...], [...]]` | [Dataset-JSON v1.1](https://cdisc-org.github.io/DataExchange-DatasetJson/doc/dataset-json1-1.html) | Whole file is one JSON object. |
| `.ndjson` | Newline-delimited: line 1 is the metadata object (with `columns` but no `rows`); lines 2..N are individual row arrays, one per line | [Dataset-NDJSON v1.1](https://cdisc-org.github.io/DataExchange-DatasetJson/doc/dataset-json-ndjson1-1.html) | Streamable; each row is independently valid JSON. |
| `.dsjc`   | zLib-compressed Dataset-NDJSON                                   | [Compressed Dataset-JSON v1.1](https://cdisc-org.github.io/DataExchange-DatasetJson/doc/compressed-dataset-json1-1.html) | "DSJC" = **D**ata**S**et **J**SON **C**ompressed. |

**Important payload shape detail:** in all three forms, each row is a
**positional array** of values (not a `{column: value}` object) — the
column names live once in the metadata header, and rows reference them by
index. That's how the format stays compact even in `.json`.

### Verified with the test fixtures

The 6 files in `datasets/testing_json/` (`ae.{json,ndjson,dsjc}` +
`adae.{json,ndjson,dsjc}`) round-trip to **identical** DataFrames:

- `ae`:    74 rows × 37 columns (matches the existing `ae.xpt` fixture)
- `adae`:  1191 rows × 55 columns

All three variants of each dataset carry the same `columns` metadata and
the same row values — the only difference is the wire encoding.

### One real-world wrinkle to know about

The CDISC v1.1 spec says `.dsjc` is a **raw zLib stream** ("no headers,
signatures, or metadata wrapping"). The test fixtures in this repo, which
were produced by `VDE Dataset Converter v0.6.0`, are actually **gzip-wrapped**
(magic bytes `1f 8b`). The implementation should handle both — Python's
`zlib.decompress(data, wbits=47)` auto-detects gzip and zlib wrappers in
one call, which is the cleanest way to be robust to either.

## What this feature buys us

1. **Same data the `.xpt` loader already handles, in a modern format.**
   Pharma vendors are actively shifting from XPT to Dataset-JSON because
   it's text-based, smaller, and round-trips cleanly through standard
   tools. Test fixtures in `datasets/testing_json/` prove parity with the
   existing `ae.xpt`.
2. **No new Python dependencies.** `json`, `gzip`, and `zlib` are stdlib;
   `pandas` is already a core dep. (Contrast `.xpt`/`.sas7bdat` which
   needed `pyreadstat`.)
3. **Free per-column labels.** Every Dataset-JSON file embeds a `label`
   per column (`"STUDYID" -> "Study Identifier"`, etc.). The app already
   has a column-labels machinery — see "Optional follow-up" below.

## Design decisions to make up front

### 1. Honor embedded column labels?

The app currently reads friendly column names from a project-root
`column_labels_simple.csv` (see `backend/services/column_labels.py`). When
a user loads a Dataset-JSON file, that file already contains its own per-column
labels in `columns[].label` — a strict superset of what's in the CSV for
this specific dataset.

**Recommendation: ship the first cut without touching the labels system.**
Just load the data; ignore `columns[].label`. Reason: the labels pipeline
is currently global (one CSV for the whole project) and making it
per-dataset is a real plumbing change — pulling that in here would
balloon the PR. File a follow-up to wire dataset-embedded labels into
`state.columnLabels` once the loader lands.

### 2. Honor the declared `dataType` per column?

Dataset-JSON declares one of ten types per column (`string`, `integer`,
`decimal`, `float`, `double`, `boolean`, `date`, `datetime`, `time`,
`URI`). The existing loader relies on pandas inference + the shared
`_convert_arrow_types()` normalizer in
`mcp_server/services/data_loading.py` to figure types out from the data.

**Recommendation: ignore the declared types in the first cut.** Build
the DataFrame from row arrays, then pipe it through `_convert_arrow_types()`
exactly like the parquet branch does. The same logic that successfully
classifies numeric/categorical/date columns for CSV and parquet will work
here — and the route layer's date-sniffing in
`mcp_server/routes/datasets.py:65-78` already handles ISO date strings.

The case where this is wrong is a column that's declared `integer` but
happens to contain only one value in the sample (could get classified as
categorical). That's a known edge case for CSV today too — not a
regression.

### 3. Memory model

NDJSON's whole point is that it's streamable. We could read it line by
line and not hold the parsed structure in memory twice.

**Recommendation: don't.** The existing CSV and parquet paths load
everything via `pd.read_csv` / `pd.read_parquet`. The MCP server keeps
the whole DataFrame in memory anyway (per `ARCHITECTURE.md` §"Dataset
State"). Streaming the parser would save only a transient peak during
load, at the cost of more code. Match the existing pattern.

## Implementation

### Files to change

All edits stay inside the existing module layout — no new packages.

#### 1. `mcp_server/services/data_loading.py` — the actual loader

Add a small `_load_dataset_json(path)` helper next to the existing
`load_dataset()`, then route to it from the format switch:

```python
import gzip
import json
import zlib
from pathlib import Path
import pandas as pd

DATASET_JSON_EXTENSIONS = {'.json', '.ndjson', '.dsjc'}
SUPPORTED_EXTENSIONS = {'.csv', '.parquet', '.pq', '.sas7bdat', '.xpt'} | DATASET_JSON_EXTENSIONS


def _read_dsjc_bytes(path: Path) -> bytes:
    """Decompress a .dsjc file. Spec says raw zLib but vendor outputs
    often use gzip; wbits=47 auto-detects both."""
    return zlib.decompress(path.read_bytes(), wbits=47)


def _load_dataset_json(path: Path) -> pd.DataFrame:
    """Load Dataset-JSON / Dataset-NDJSON / DSJC into a DataFrame.

    All three encodings carry the same payload: metadata (with `columns`)
    plus row arrays whose positions align to `columns`. We don't honor the
    declared per-column dataType — we let pandas infer types and rely on
    `_convert_arrow_types()` for normalization, matching the parquet path.
    """
    ext = path.suffix.lower()

    if ext == '.json':
        # Compact: one object with `columns` and `rows`.
        with open(path, 'r', encoding='utf-8') as f:
            obj = json.load(f)
        _validate_dataset_json_shape(obj, path)
        col_names = [c['name'] for c in obj['columns']]
        rows = obj.get('rows', [])
        df = pd.DataFrame(rows, columns=col_names)

    else:
        # NDJSON or DSJC: line 1 = metadata, lines 2..N = row arrays.
        if ext == '.dsjc':
            text = _read_dsjc_bytes(path).decode('utf-8')
            lines = text.splitlines()
        else:  # .ndjson
            with open(path, 'r', encoding='utf-8') as f:
                lines = f.read().splitlines()

        if not lines:
            raise HTTPException(status_code=400, detail=f"Empty Dataset-JSON file: {path.name}")

        meta = json.loads(lines[0])
        _validate_dataset_json_shape(meta, path)
        col_names = [c['name'] for c in meta['columns']]
        rows = [json.loads(line) for line in lines[1:] if line.strip()]
        df = pd.DataFrame(rows, columns=col_names)

    return df


def _validate_dataset_json_shape(obj: dict, path: Path) -> None:
    """Fail fast with a clear error if a .json/.ndjson file isn't actually
    Dataset-JSON (since .json is a generic extension)."""
    if not isinstance(obj, dict) or 'columns' not in obj:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{path.name} is not a CDISC Dataset-JSON file "
                f"(missing 'columns' metadata). Supported JSON encodings: "
                f"Dataset-JSON v1.1, Dataset-NDJSON v1.1, DSJC."
            ),
        )
```

Then in `load_dataset()`, add one new branch alongside the existing
`.csv` / `.parquet` / `.sas7bdat` / `.xpt` branches:

```python
elif file_ext in DATASET_JSON_EXTENSIONS:
    df = _load_dataset_json(dataset_path)
    df = _convert_arrow_types(df)
```

That's the whole load path. `_convert_arrow_types()` already does:
- numeric coercion (catches columns that arrived as strings)
- missing-value normalization (`""`, `null`, `"."`, `"NA"`, etc.)
- Int64/Float64 → numpy dtype demotion

…which is exactly what Dataset-JSON output needs.

#### 2. `backend/services/datasets.py` — the discovery / file-listing copy

Update the duplicated `SUPPORTED_EXTENSIONS` constant
(`backend/services/datasets.py:47`) to match:

```python
SUPPORTED_EXTENSIONS = {'.csv', '.parquet', '.pq', '.sas7bdat', '.xpt', '.json', '.ndjson', '.dsjc'}
```

This is the constant that controls which files show up in the dataset
dropdown (Domino datasets, NetApp volumes, snapshot browser) and what
the local `find_data_files_fallback()` finds. **Cleanup nit:** these
two `SUPPORTED_EXTENSIONS` constants are already duplicated between
`mcp_server/services/data_loading.py` and `backend/services/datasets.py`
— this is pre-existing tech debt, don't try to deduplicate in this PR.

#### 3. `chat_ui/script.js` — UI copy

Two strings at lines 295–298:
```js
'No supported data files found in this dataset. Supported formats: CSV, Parquet, SAS.'
```
…need updating. Replace `SAS.` with `SAS, Dataset-JSON.` (or similar
short phrasing). One-line change, no logic.

#### 4. `README.md` — supported-format list

Line 40–43 currently lists `.csv` / `.parquet` / `.sas7bdat` / `.xpt`.
Add a fifth bullet:

```markdown
- CDISC Dataset-JSON (`.json`, `.ndjson`, `.dsjc`)
```

Also line 318's troubleshooting hint should be updated to include the
new extensions.

### New file: `tests/contract/test_mcp_dataset_json.py`

Mirror `tests/contract/test_mcp_parquet.py`. Use the existing
`datasets/testing_json/ae.json` (or build tiny synthetic ones in
`tmp_path` like `test_mcp_parquet.py` does — the existing fixtures are
nicer because they're real-world).

Recommended test cases (one fixture file per encoding, parametrized):

```python
@pytest.mark.parametrize("fixture_name", ["ae.json", "ae.ndjson", "ae.dsjc"])
def test_dataset_json_loads_and_classifies_columns(_mcp_app, fixture_name):
    # Load the fixture, assert num_rows == 74, num_columns == 37,
    # assert AESEQ ∈ numeric_columns, AETERM ∈ categorical_columns,
    # AESTDTC ∈ date_columns.
```

A second test should confirm a filter round-trips (same pattern as
`test_parquet_numeric_filter_works_after_type_conversion`) — pick a
filter on `AESEQ > 5` and assert the returned rows satisfy it.

That's enough coverage. The three encodings share `_load_dataset_json`'s
downstream code path after the encoding-specific read, so we don't need
exhaustive cross-product tests.

### What is NOT changing

- `pyproject.toml`: no new deps. `json`, `gzip`, `zlib` are stdlib.
- `backend/services/file_size_limits.py`: the byte-size enforcement is
  generic. The `DATA_TO_DATAFRAME_SIZE_MULTIPLIER = 5` heuristic is
  designed for the worst case (CSV → DataFrame); Dataset-JSON inflates
  somewhat less than CSV (the row arrays are denser than `key=value`
  CSV cells), so the existing multiplier is already conservative.
- `backend/services/column_labels.py`: see "Design decisions" §1 — out
  of scope.
- `backend/routes/datasets.py`: only imports the constant, no edit needed.

## Implementation order for the coding agent

1. **Add the loader + the new extension to the MCP server's
   `SUPPORTED_EXTENSIONS`** (`mcp_server/services/data_loading.py`).
2. **Update the backend's duplicated `SUPPORTED_EXTENSIONS`** so files
   show up in the UI's discovery responses
   (`backend/services/datasets.py:47`).
3. **Update the two UI strings** (`chat_ui/script.js:295-298`).
4. **Write the contract tests** against the existing fixtures in
   `datasets/testing_json/`.
5. **Update `README.md`** (lines 40–43 and 318).
6. **Manually verify** by running the app and loading each of the six
   fixture files through the dataset dropdown — assert column counts,
   numeric/categorical classification, and that a basic filter works.

## Optional follow-up (separate PR)

Wire embedded Dataset-JSON labels into the existing column-labels UI:

- In `mcp_server/routes/datasets.py:load_dataset_endpoint`, return the
  embedded `columns[].label` map alongside the existing response fields.
- In `chat_ui/modules/column-labels.js`, merge the per-dataset label
  map into `state.columnLabels` on load (taking precedence over the
  CSV-derived map for keys present in both).
- Reveal the "Show friendly names" toggle when *either* source has labels.

This is a small, well-scoped UX win that's intentionally deferred to keep
the format-support PR easy to review.

## References

- [CDISC Dataset-JSON v1.1 (compact)](https://cdisc-org.github.io/DataExchange-DatasetJson/doc/dataset-json1-1.html)
- [CDISC Dataset-NDJSON v1.1](https://cdisc-org.github.io/DataExchange-DatasetJson/doc/dataset-json-ndjson1-1.html)
- [CDISC Compressed Dataset-JSON v1.1 (DSJC)](https://cdisc-org.github.io/DataExchange-DatasetJson/doc/compressed-dataset-json1-1.html)
- [CDISC Dataset-JSON repository (cdisc-org/DataExchange-DatasetJson)](https://github.com/cdisc-org/DataExchange-DatasetJson)
