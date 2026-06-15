"""Dataset file discovery, loading, and Arrow-type fixup helpers.

Extracted from `mcp_server/app.py` as step 2.4a of REFACTOR_PLAN.md §2.

What lives here:

- `find_data_files()` — search for supported files in the repo `datasets/`
  folder used for bundled local data.
- `load_dataset(file_snapshot_path)` — resolves a dataset path reference,
  reads it
  (CSV / parquet / SAS7BDAT / XPT), and normalizes types.
- `_convert_arrow_types(df)` — defensive type coercion for parquet files
  that come back with PyArrow-backed dtypes or string-typed numeric
  columns (very common for upstream-exported clinical data).

Design notes:

- These helpers raise `HTTPException` directly because they're called from
  inside route handlers. Per ground rule #2 of REFACTOR_PLAN.md, behavior
  is preserved exactly — the "pure functions, no Flask/FastAPI imports"
  guidance from the plan target layout is aspirational here and would
  require a behavior change (replacing `HTTPException` with a sentinel
  exception that the route layer translates) which is out of scope.
- `_convert_arrow_types` is also re-exported from `mcp_server/app.py` and
  from the top-level `data_analysis_mcp.py` shim so the existing import
  surface (`from data_analysis_mcp import _convert_arrow_types`, named
  defensively in `tests/contract/test_mcp_parquet.py`) stays intact.
- Logging stays on the local module logger; the per-load info messages
  (column types, detected numeric/categorical columns) are preserved
  byte-equivalent for parity with the previous behavior.
"""
import json
import zlib
from pathlib import Path
from typing import Dict, List

import logging

import numpy as np
import pandas as pd
from fastapi import HTTPException

from mcp_server.services.columns import (
    _get_categorical_columns,
    _get_numeric_columns,
)

logger = logging.getLogger(__name__)

# Try to import pyreadstat for SAS file support
try:
    import pyreadstat
    PYREADSTAT_AVAILABLE = True
except ImportError:
    PYREADSTAT_AVAILABLE = False
    logger.warning("pyreadstat not available - SAS file formats (.sas7bdat, .xpt) will not be supported")


# Data source locations
datasets_folder = Path("datasets")

# CDISC Dataset-JSON v1.1 encodings: compact JSON, newline-delimited JSON,
# and zLib-compressed NDJSON (DSJC). All three carry the same payload.
DATASET_JSON_EXTENSIONS = {'.json', '.ndjson', '.dsjc'}

# Supported file extensions
SUPPORTED_EXTENSIONS = {'.csv', '.parquet', '.pq', '.sas7bdat', '.xpt'} | DATASET_JSON_EXTENSIONS

UNSUPPORTED_DATASET_JSON_DETAIL = (
    "This JSON file is not a supported CDISC Dataset-JSON file. "
    "Clinical Data Explorer supports CDISC Dataset-JSON v1.1, "
    "Dataset-NDJSON v1.1, and DSJC files for JSON data."
)


def _read_dsjc_bytes(path: Path) -> bytes:
    """Decompress a .dsjc file. The CDISC spec says raw zLib, but vendor
    outputs (e.g. VDE Dataset Converter) are often gzip-wrapped; wbits=47
    auto-detects both gzip and zlib wrappers in one call."""
    try:
        return zlib.decompress(path.read_bytes(), wbits=47)
    except zlib.error as e:
        raise HTTPException(
            status_code=400,
            detail=f"{path.name} could not be decompressed as DSJC "
                   f"(expected zLib or gzip stream): {e}",
        )


def _unsupported_dataset_json(path: Path, detail: str = "") -> HTTPException:
    suffix = f" {detail}" if detail else ""
    return HTTPException(
        status_code=400,
        detail=f"{path.name}: {UNSUPPORTED_DATASET_JSON_DETAIL}{suffix}",
    )


def _parse_dataset_json_line(line: str, path: Path, line_number: int):
    try:
        return json.loads(line)
    except json.JSONDecodeError as exc:
        raise _unsupported_dataset_json(
            path,
            f"Line {line_number} is not valid JSON: {exc.msg}.",
        ) from exc


def _validate_dataset_json_shape(obj: dict, path: Path) -> None:
    """Fail fast with a clear error if a .json/.ndjson file isn't actually
    Dataset-JSON (since .json is a generic extension — could be a
    package.json, a pandas to_json dump, anything). Requiring both
    `datasetJSONVersion` and `columns` is a tight signal for the spec;
    either alone could false-positive on a coincidentally-named field."""
    if (not isinstance(obj, dict)
            or 'columns' not in obj
            or 'datasetJSONVersion' not in obj):
        raise _unsupported_dataset_json(path, "Missing 'datasetJSONVersion' or 'columns' metadata.")

    columns = obj.get('columns')
    if not isinstance(columns, list) or not all(isinstance(c, dict) and c.get('name') for c in columns):
        raise _unsupported_dataset_json(path, "The 'columns' metadata must be a list of named columns.")


def _validate_dataset_json_rows(rows, path: Path, expected_width: int, row_context: str) -> None:
    if not isinstance(rows, list):
        raise _unsupported_dataset_json(path, f"{row_context} must be a row array.")
    if len(rows) != expected_width:
        raise _unsupported_dataset_json(
            path,
            f"{row_context} has {len(rows)} values, but the metadata declares {expected_width} columns.",
        )


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
        try:
            with open(path, 'r', encoding='utf-8') as f:
                obj = json.load(f)
        except json.JSONDecodeError as exc:
            raise _unsupported_dataset_json(path, f"The file is not valid JSON: {exc.msg}.") from exc
        _validate_dataset_json_shape(obj, path)
        col_names = [c['name'] for c in obj['columns']]
        rows = obj.get('rows', [])
        if not isinstance(rows, list):
            raise _unsupported_dataset_json(path, "The 'rows' value must be a list of row arrays.")
        for index, row in enumerate(rows, start=1):
            _validate_dataset_json_rows(row, path, len(col_names), f"Row {index}")
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

        meta = _parse_dataset_json_line(lines[0], path, 1)
        _validate_dataset_json_shape(meta, path)
        col_names = [c['name'] for c in meta['columns']]
        rows = []
        for line_number, line in enumerate(lines[1:], start=2):
            if not line.strip():
                continue
            row = _parse_dataset_json_line(line, path, line_number)
            _validate_dataset_json_rows(row, path, len(col_names), f"Line {line_number}")
            rows.append(row)
        df = pd.DataFrame(rows, columns=col_names)

    return df


def _convert_arrow_types(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert PyArrow-backed types to standard pandas/numpy types.
    This ensures compatibility with pandas type detection functions like select_dtypes.

    Uses aggressive numeric conversion to properly detect numeric columns that may
    be stored as strings or Arrow types in parquet files.

    Handles common missing value representations in clinical/scientific data:
    - Empty strings ''
    - Whitespace-only strings
    - Common missing value indicators like '.', 'NA', 'N/A', 'NaN', 'null'

    Performance: Uses vectorized pandas operations instead of row-by-row apply()
    for efficient processing of large datasets (1M+ rows).
    """
    # Common missing value indicators in clinical data (SAS exports often use '.')
    MISSING_VALUES = {'', '.', 'NA', 'N/A', 'NaN', 'nan', 'null', 'NULL', 'None', 'NONE'}

    for col in df.columns:
        dtype = df[col].dtype
        dtype_str = str(dtype).lower()

        # First, handle Arrow-backed types by converting to Python objects
        if 'pyarrow' in dtype_str or 'arrow' in dtype_str:
            try:
                # Convert Arrow types to Python objects first
                df[col] = df[col].astype(object)
            except Exception:
                pass

        # Now try to infer the best type for each column
        # Try numeric conversion first (this catches numeric columns stored as strings/objects)
        if not pd.api.types.is_numeric_dtype(df[col]):
            try:
                # For string/object columns, first normalize missing values
                # This handles empty strings, '.', 'NA', etc. common in clinical data
                col_series = df[col]
                col_dtype_str = str(col_series.dtype).lower()

                # 'str' covers pandas 3.0's arrow-backed default string dtype
                # (str(dtype) == 'str', which does NOT contain 'string'); without
                # it, missing-value normalization is silently skipped on every
                # string column under infer_string and '.'/''/'NA' never become NaN.
                if col_series.dtype == 'object' or 'str' in col_dtype_str:
                    # VECTORIZED missing value detection (much faster than apply() with lambda)
                    # Start with pandas NA check
                    is_missing = col_series.isna()

                    # For string values, use vectorized string operations
                    # fillna('') ensures .str accessor works on all values
                    str_values = col_series.fillna('').astype(str).str.strip()
                    is_missing_str = str_values.isin(MISSING_VALUES)

                    # Combine the masks
                    is_missing = is_missing | is_missing_str

                    # Replace missing indicators with NaN for proper counting
                    # Use numpy where for efficiency (avoids copy in .where())
                    col_values = np.where(is_missing, np.nan, col_series)
                    col_values = pd.Series(col_values, index=df.index)
                else:
                    col_values = col_series

                # Try to convert to numeric
                numeric_col = pd.to_numeric(col_values, errors='coerce')

                # Count "real" non-null values (excluding missing value indicators)
                # For numeric check, we care about values that were actual data, not missing indicators
                non_null_before = col_values.notna().sum()
                non_null_after = numeric_col.notna().sum()

                if non_null_after > 0:
                    # Check if values look numeric:
                    # - If there were real values and most converted successfully, it's numeric
                    # - The threshold is 90% of actual (non-missing) values
                    if non_null_before == 0 or non_null_after >= non_null_before * 0.9:
                        df[col] = numeric_col
                        logger.debug(f"Converted a column to numeric: {non_null_after}/{non_null_before} values converted")
                    else:
                        # Column is not numeric, but still apply missing value normalization
                        # so that empty strings, '.', 'NA', etc. become NaN and are
                        # properly detected by isna() in stats and filters
                        if is_missing.any():
                            df[col] = col_values
                            logger.debug(f"Normalized {is_missing.sum()} missing indicators in a string column")
                else:
                    # No numeric values at all — still apply missing value normalization
                    # for string columns (e.g. all-empty or all-missing-indicator columns)
                    if is_missing.any():
                        df[col] = col_values
                        logger.debug(f"Normalized {is_missing.sum()} missing indicators in a string column")
            except Exception as e:
                logger.debug(f"Could not convert a column to numeric: {e}")
                pass

        # Handle nullable integer types (Int64, Int32, etc.) - convert to standard types
        dtype_str = str(df[col].dtype)
        if dtype_str in ('Int8', 'Int16', 'Int32', 'Int64', 'UInt8', 'UInt16', 'UInt32', 'UInt64'):
            try:
                if df[col].isna().any():
                    df[col] = df[col].astype('float64')
                else:
                    df[col] = df[col].astype('int64')
            except Exception:
                pass

        # Handle nullable float types (Float32, Float64)
        elif dtype_str in ('Float32', 'Float64'):
            try:
                df[col] = df[col].astype('float64')
            except Exception:
                pass

        # Handle string types - keep as object for compatibility.
        # 'str' is pandas 3.0's arrow-backed default string dtype (when
        # infer_string is on); downstream type detection (e.g. the route
        # layer's date-sniffing) only recognizes object/'string' columns.
        elif dtype_str in ('string', 'string[python]', 'string[pyarrow]', 'str'):
            try:
                df[col] = df[col].astype('object')
            except Exception:
                pass

    return df


# ===== Verbatim file metadata extraction (for the "Metadata" side panel) =====
#
# This is purely informational: we surface metadata that already lives in the
# file, with no inference or extra computation. CSV/Parquet carry none, so they
# get an empty-state. Dataset-JSON embeds a rich header; SAS files (.xpt /
# .sas7bdat) expose labels/types/widths via pyreadstat's metadata-only read.
#
# define.xml (the CDISC submission-level metadata document referenced by
# `metaDataRef`) is intentionally out of scope — it's one-per-submission,
# deeply nested, and not co-located with the data file. We only extract what
# is verbatim inside the data file itself.

_NO_METADATA_MESSAGE = (
    "No embedded metadata for this file type. CDISC Dataset-JSON "
    "(.json, .ndjson, .dsjc) and SAS files (.xpt, .sas7bdat) carry dataset- "
    "and variable-level metadata; CSV and Parquet do not."
)

# Friendly label + display order for the Dataset-JSON file-level header keys.
# Anything not listed here is still shown afterwards, verbatim by its raw key.
_DATASET_JSON_FILE_LABELS = [
    ('name', 'Dataset'),
    ('label', 'Dataset Label'),
    ('records', 'Records'),
    ('datasetJSONVersion', 'Dataset-JSON Version'),
    ('studyOID', 'Study OID'),
    ('metaDataVersionOID', 'Metadata Version OID'),
    ('metaDataRef', 'Metadata Reference'),
    ('itemGroupOID', 'Item Group OID'),
    ('originator', 'Originator'),
    ('sourceSystem', 'Source System'),
    ('datasetJSONCreationDateTime', 'Created'),
    ('dbLastModifiedDateTime', 'DB Last Modified'),
    ('fileOID', 'File OID'),
]


def _stringify_meta_value(value) -> str:
    """Render a metadata value as a flat display string. Handles the nested
    `sourceSystem` object ({name, version}) and list-valued fields."""
    if isinstance(value, dict):
        name = value.get('name')
        version = value.get('version')
        if name and version:
            return f"{name} ({version})"
        return ', '.join(f"{k}: {v}" for k, v in value.items())
    if isinstance(value, list):
        return ', '.join(str(v) for v in value)
    return str(value)


def _dataset_json_header(path: Path) -> dict:
    """Read just the metadata header of a Dataset-JSON file (no rows for
    NDJSON/DSJC; the compact .json form is one object so it's parsed whole)."""
    ext = path.suffix.lower()
    if ext == '.json':
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    if ext == '.dsjc':
        first_line = _read_dsjc_bytes(path).decode('utf-8').splitlines()[0]
        return json.loads(first_line)
    # .ndjson — line 1 is the metadata object.
    with open(path, 'r', encoding='utf-8') as f:
        return json.loads(f.readline())


def _build_dataset_json_metadata(path: Path) -> dict:
    obj = _dataset_json_header(path)
    if not isinstance(obj, dict) or 'columns' not in obj:
        return {'available': False, 'message': _NO_METADATA_MESSAGE}

    file_items = []
    seen = set()
    for key, label in _DATASET_JSON_FILE_LABELS:
        if key in obj and obj[key] not in (None, ''):
            file_items.append({'key': label, 'value': _stringify_meta_value(obj[key])})
            seen.add(key)
    # Surface any remaining top-level keys verbatim (forward-compatible with
    # spec additions), skipping the bulky data arrays.
    for key, value in obj.items():
        if key in seen or key in ('columns', 'rows') or value in (None, ''):
            continue
        file_items.append({'key': key, 'value': _stringify_meta_value(value)})

    columns = obj.get('columns', [])
    has_key = any(c.get('keySequence') is not None for c in columns)
    has_format = any(c.get('displayFormat') for c in columns)
    headers = ['Name', 'Label', 'Type', 'Length']
    if has_key:
        headers.append('Key')
    if has_format:
        headers.append('Format')

    rows = []
    for c in columns:
        length = c.get('length')
        row = [
            c.get('name', ''),
            c.get('label', ''),
            c.get('dataType', ''),
            '' if length is None else str(length),
        ]
        if has_key:
            ks = c.get('keySequence')
            row.append('' if ks is None else str(ks))
        if has_format:
            row.append(c.get('displayFormat', '') or '')
        rows.append(row)

    version = obj.get('datasetJSONVersion')
    fmt = 'CDISC Dataset-JSON' + (f' v{version}' if version else '')
    return {
        'available': True,
        'format': fmt,
        'file': file_items,
        'variables': {'headers': headers, 'rows': rows},
    }


def _build_sas_metadata(path: Path) -> dict:
    if not PYREADSTAT_AVAILABLE:
        return {'available': False, 'message': _NO_METADATA_MESSAGE}

    ext = path.suffix.lower()
    reader = pyreadstat.read_xport if ext == '.xpt' else pyreadstat.read_sas7bdat
    # metadataonly avoids materializing the rows just to read the header.
    _, meta = reader(str(path), metadataonly=True)

    file_items = []

    def add(label, value):
        if value not in (None, ''):
            file_items.append({'key': label, 'value': str(value)})

    add('Dataset', meta.table_name)
    add('Dataset Label', meta.file_label)
    add('Records', meta.number_rows)
    add('Variables', meta.number_columns)
    add('Encoding', meta.file_encoding)
    add('Created', meta.creation_time)
    add('Modified', meta.modification_time)

    labels = meta.column_names_to_labels or {}
    types = meta.readstat_variable_types or {}
    widths = meta.variable_storage_width or {}
    rows = []
    for name in (meta.column_names or []):
        width = widths.get(name)
        rows.append([
            name,
            labels.get(name, '') or '',
            str(types.get(name, '') or ''),
            '' if width is None else str(width),
        ])

    fmt = 'SAS Transport (XPT)' if ext == '.xpt' else 'SAS dataset (sas7bdat)'
    return {
        'available': True,
        'format': fmt,
        'file': file_items,
        'variables': {'headers': ['Name', 'Label', 'Type', 'Length'], 'rows': rows},
    }


def extract_dataset_metadata(path: Path) -> dict:
    """Extract verbatim file/variable metadata for the Metadata panel.

    Never raises — informational only. Returns a dict with `available: True`
    and `file` / `variables` sections when the format carries metadata, or
    `available: False` with a `message` otherwise (CSV/Parquet, unreadable
    files, or non-Dataset-JSON `.json`).
    """
    try:
        ext = path.suffix.lower()
        if ext in DATASET_JSON_EXTENSIONS:
            return _build_dataset_json_metadata(path)
        if ext in {'.xpt', '.sas7bdat'}:
            return _build_sas_metadata(path)
        return {'available': False, 'message': _NO_METADATA_MESSAGE}
    except Exception as e:
        logger.debug(f"Could not extract metadata for {path}: {e}")
        return {'available': False, 'message': _NO_METADATA_MESSAGE}


def find_data_files() -> List[Dict[str, str]]:
    """
    Find all supported data files from the repo datasets/ folder.

    Returns a list of dicts with 'name' (display name) and 'path' (full path)
    """
    data_files = []

    if datasets_folder.exists():
        for ext in SUPPORTED_EXTENSIONS:
            for f in datasets_folder.glob(f"*{ext}"):
                data_files.append({
                    'name': f.name,
                    'path': str(f.resolve())
                })

    return data_files


def _resolve_dataset_path(file_snapshot_path: str) -> Path:
    dataset_path = Path(file_snapshot_path)
    if dataset_path.exists():
        return dataset_path

    if not dataset_path.is_absolute():
        datasets_path = datasets_folder / dataset_path
        if datasets_path.exists():
            return datasets_path

    return dataset_path


def load_dataset(file_snapshot_path: str) -> pd.DataFrame:
    """Load a dataset file from disk and return the DataFrame."""
    dataset_path = _resolve_dataset_path(file_snapshot_path)

    if not dataset_path.exists():
        raise HTTPException(status_code=404, detail=f"Dataset '{file_snapshot_path}' not found")

    try:
        # Load based on file extension
        file_ext = dataset_path.suffix.lower()
        if file_ext == '.csv':
            df = pd.read_csv(dataset_path)
        elif file_ext in {'.parquet', '.pq'}:
            # Read parquet and convert Arrow types to standard pandas types
            # This ensures compatibility with pandas type detection functions
            df = pd.read_parquet(dataset_path)
            df = _convert_arrow_types(df)
        elif file_ext == '.sas7bdat':
            # Read SAS dataset format
            if not PYREADSTAT_AVAILABLE:
                raise HTTPException(
                    status_code=500,
                    detail="SAS file support requires pyreadstat. Run `uv sync --locked` to install project dependencies."
                )
            df, meta = pyreadstat.read_sas7bdat(str(dataset_path))
            logger.info(f"Loaded SAS dataset with {len(df)} rows and {len(df.columns)} columns")
            # pyreadstat returns a clean DataFrame, but we should still convert types for consistency
            df = _convert_arrow_types(df)
        elif file_ext == '.xpt':
            # Read SAS Transport format (XPT)
            if not PYREADSTAT_AVAILABLE:
                raise HTTPException(
                    status_code=500,
                    detail="SAS Transport file support requires pyreadstat. Run `uv sync --locked` to install project dependencies."
                )
            df, meta = pyreadstat.read_xport(str(dataset_path))
            logger.info(f"Loaded SAS Transport file with {len(df)} rows and {len(df.columns)} columns")
            # pyreadstat returns a clean DataFrame, but we should still convert types for consistency
            df = _convert_arrow_types(df)
        elif file_ext in DATASET_JSON_EXTENSIONS:
            # CDISC Dataset-JSON (.json), Dataset-NDJSON (.ndjson), or DSJC (.dsjc).
            # Build the DataFrame from row arrays, then normalize types exactly
            # like the parquet path — we let pandas infer rather than honoring
            # the declared per-column dataType.
            df = _load_dataset_json(dataset_path)
            df = _convert_arrow_types(df)
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported file format: {file_ext}")

        numeric_cols = _get_numeric_columns(df)
        categorical_cols = _get_categorical_columns(df, numeric_cols)
        logger.info(f"Loaded dataset: {file_snapshot_path} (format: {file_ext})")
        logger.info(f"Loaded dataset with {len(df)} rows and {len(df.columns)} columns")
        logger.info(f"Detected {len(numeric_cols)} numeric columns and {len(categorical_cols)} categorical columns")

        return df
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error loading dataset: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error loading dataset: {str(e)}")
