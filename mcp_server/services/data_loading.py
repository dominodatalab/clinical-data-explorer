"""Dataset file discovery, loading, and Arrow-type fixup helpers.

Extracted from `mcp_server/app.py` as step 2.4a of REFACTOR_PLAN.md §2.

What lives here:

- `find_data_files()` — recursive search for supported files across the
  five known data roots (`datasets/`, `/mnt/data`, `/mnt/netapp-volumes`,
  `/domino/datasets`, `/domino/netapp-volumes`).
- `load_dataset(file_snapshot_path)` — resolves a dataset path reference,
  reads it
  (CSV / parquet / SAS7BDAT / XPT), normalizes types, and stores the
  resulting DataFrame for the current session.
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
from mcp_server.session import _set_current_df

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
mnt_data_folder = Path("/mnt/data")
mnt_netapp_folder = Path("/mnt/netapp-volumes")
domino_datasets_folder = Path("/domino/datasets")
domino_netapp_folder = Path("/domino/netapp-volumes")

# Supported file extensions
SUPPORTED_EXTENSIONS = {'.csv', '.parquet', '.pq', '.sas7bdat', '.xpt'}


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

                if col_series.dtype == 'object' or 'string' in col_dtype_str:
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
                        logger.debug(f"Converted column '{col}' to numeric: {non_null_after}/{non_null_before} values converted")
                    else:
                        # Column is not numeric, but still apply missing value normalization
                        # so that empty strings, '.', 'NA', etc. become NaN and are
                        # properly detected by isna() in stats and filters
                        if is_missing.any():
                            df[col] = col_values
                            logger.debug(f"Normalized {is_missing.sum()} missing indicators in string column '{col}'")
                else:
                    # No numeric values at all — still apply missing value normalization
                    # for string columns (e.g. all-empty or all-missing-indicator columns)
                    if is_missing.any():
                        df[col] = col_values
                        logger.debug(f"Normalized {is_missing.sum()} missing indicators in string column '{col}'")
            except Exception as e:
                logger.debug(f"Could not convert column '{col}' to numeric: {e}")
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

        # Handle string types - keep as object for compatibility
        elif dtype_str in ('string', 'string[python]', 'string[pyarrow]'):
            try:
                df[col] = df[col].astype('object')
            except Exception:
                pass

    return df


def find_data_files() -> List[Dict[str, str]]:
    """
    Find all supported data files from:
    1. datasets/ folder (flat)
    2. /mnt/data/ folder (recursive)
    3. /domino/datasets/ folder (recursive)

    Returns a list of dicts with 'name' (display name) and 'path' (full path)
    """
    data_files = []

    # Search in datasets/ folder (flat search)
    if datasets_folder.exists():
        for ext in SUPPORTED_EXTENSIONS:
            for f in datasets_folder.glob(f"*{ext}"):
                data_files.append({
                    'name': f.name,
                    'path': str(f.resolve())
                })

    # Search in /mnt/data/ folder recursively
    if mnt_data_folder.exists():
        for ext in SUPPORTED_EXTENSIONS:
            # Use rglob for recursive search
            for f in mnt_data_folder.rglob(f"*{ext}"):
                # Use relative path from /mnt/data as the display name
                relative_path = f.relative_to(mnt_data_folder)
                data_files.append({
                    'name': f"/mnt/data/{relative_path}",
                    'path': str(f.resolve())
                })

    # Search in /mnt/netapp-volumes/ folder recursively
    if mnt_netapp_folder.exists():
        for ext in SUPPORTED_EXTENSIONS:
            # Use rglob for recursive search
            for f in mnt_netapp_folder.rglob(f"*{ext}"):
                # Use relative path from /mnt/netapp-volumes as the display name
                relative_path = f.relative_to(mnt_netapp_folder)
                data_files.append({
                    'name': f"/mnt/netapp-volumes/{relative_path}",
                    'path': str(f.resolve())
                })

    # Search in /domino/datasets/ folder recursively
    if domino_datasets_folder.exists():
        for ext in SUPPORTED_EXTENSIONS:
            # Use rglob for recursive search
            for f in domino_datasets_folder.rglob(f"*{ext}"):
                # Use relative path from /domino/datasets as the display name
                relative_path = f.relative_to(domino_datasets_folder)
                data_files.append({
                    'name': f"/domino/datasets/{relative_path}",
                    'path': str(f.resolve())
                })

    # Search in /domino/netapp-volumes/ folder recursively
    if domino_netapp_folder.exists():
        for ext in SUPPORTED_EXTENSIONS:
            # Use rglob for recursive search
            for f in domino_netapp_folder.rglob(f"*{ext}"):
                # Use relative path from /domino/netapp-volumes as the display name
                relative_path = f.relative_to(domino_netapp_folder)
                data_files.append({
                    'name': f"/domino/netapp-volumes/{relative_path}",
                    'path': str(f.resolve())
                })

    return data_files


def load_dataset(file_snapshot_path: str) -> pd.DataFrame:
    """Load a dataset"""
    dataset_path = Path(file_snapshot_path)

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
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported file format: {file_ext}")

        # TODO when is this read?
        _set_current_df(df, file_snapshot_path)

        # Log column types for debugging
        logger.info(f"Loaded dataset: {file_snapshot_path} (format: {file_ext})")
        logger.info(f"Column types after conversion:")
        for col in df.columns:
            logger.info(f"  {col}: {df[col].dtype}")

        numeric_cols = _get_numeric_columns(df)
        categorical_cols = _get_categorical_columns(df, numeric_cols)
        logger.info(f"Detected numeric columns: {numeric_cols}")
        logger.info(f"Detected categorical columns: {categorical_cols}")

        return df
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error loading dataset: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error loading dataset: {str(e)}")
