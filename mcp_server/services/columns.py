"""Robust numeric/categorical column detection helpers.

Extracted from `mcp_server/app.py` as step 2.4b of REFACTOR_PLAN.md §2.
The pre-existing implementations are kept verbatim — the helpers are
deliberately defensive (multi-method numeric detection) because parquet
files written by upstream tools sometimes use Arrow-backed or
nullable-pandas dtypes that `df.select_dtypes(include=[np.number])` alone
misses. Do not "simplify" without verifying the parquet contract tests
still pass.

These helpers are pure (no Flask/FastAPI imports, no module state) so
they're safe to import from both `services/data_loading.py` and the
still-inline route handlers in `mcp_server/app.py`.
"""
from typing import List

import numpy as np
import pandas as pd


def _get_numeric_columns(df: pd.DataFrame) -> List[str]:
    """
    Get list of numeric columns using multiple detection methods.
    More robust than just using select_dtypes.
    """
    numeric_cols = []
    
    for col in df.columns:
        # Method 1: Check using pandas is_numeric_dtype
        if pd.api.types.is_numeric_dtype(df[col]):
            numeric_cols.append(col)
            continue
        
        # Method 2: Check using numpy number type
        try:
            if np.issubdtype(df[col].dtype, np.number):
                numeric_cols.append(col)
                continue
        except Exception:
            pass
        
        # Method 3: Check dtype string for numeric indicators
        dtype_str = str(df[col].dtype).lower()
        if any(t in dtype_str for t in ['int', 'float', 'double', 'decimal', 'numeric']):
            numeric_cols.append(col)
            continue
    
    return numeric_cols


def _get_categorical_columns(df: pd.DataFrame, numeric_cols: List[str]) -> List[str]:
    """
    Get list of categorical (non-numeric) columns.
    """
    return [col for col in df.columns if col not in numeric_cols]
