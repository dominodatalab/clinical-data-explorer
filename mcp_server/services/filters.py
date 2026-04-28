"""Pandas DataFrame filtering helper.

Extracted from `mcp_server/app.py` ahead of the route splits in step 2.5
so that the soon-to-be-extracted `routes/filters.py` and the still-inline
`/table/*` routes can both depend on a single source of truth without a
circular import (route module → app module → route module).

Per ground rule #2 (preserve behavior exactly), `apply_filters` is moved
verbatim — including the per-operator try/except + silent-fallback
behavior. Per the §2 watch-out ("Pandas DataFrame mutations: apply_filters
and friends sometimes mutate in place. Preserve that exactly; do not
'fix' it in this pass."), the `df.copy()` at the top stays.
"""
from typing import List

import pandas as pd

from mcp_server.types import FilterCondition


def apply_filters(df: pd.DataFrame, filters: List[FilterCondition]) -> pd.DataFrame:
    """Apply a list of filter conditions to a dataframe"""
    filtered_df = df.copy()
    
    for f in filters:
        if f.column not in filtered_df.columns:
            continue
            
        col = filtered_df[f.column]
        is_numeric = pd.api.types.is_numeric_dtype(col)
        
        if f.operator == 'is':
            if is_numeric and f.value:
                try:
                    val = float(f.value)
                    filtered_df = filtered_df[col == val]
                except ValueError:
                    filtered_df = filtered_df[col.astype(str) == f.value]
            else:
                filtered_df = filtered_df[col.astype(str) == f.value]
                
        elif f.operator == 'is_not':
            if is_numeric and f.value:
                try:
                    val = float(f.value)
                    filtered_df = filtered_df[col != val]
                except ValueError:
                    filtered_df = filtered_df[col.astype(str) != f.value]
            else:
                filtered_df = filtered_df[col.astype(str) != f.value]
                
        elif f.operator == 'contains':
            filtered_df = filtered_df[col.astype(str).str.contains(str(f.value), case=False, na=False)]
            
        elif f.operator == 'not_contains':
            filtered_df = filtered_df[~col.astype(str).str.contains(str(f.value), case=False, na=False)]
            
        elif f.operator == 'between' and f.value and f.value2:
            if is_numeric:
                try:
                    val1 = float(f.value)
                    val2 = float(f.value2)
                    filtered_df = filtered_df[(col >= val1) & (col <= val2)]
                except ValueError:
                    pass
                    
        elif f.operator == 'gt' and f.value:
            if is_numeric:
                try:
                    val = float(f.value)
                    filtered_df = filtered_df[col > val]
                except ValueError:
                    pass
                    
        elif f.operator == 'lt' and f.value:
            if is_numeric:
                try:
                    val = float(f.value)
                    filtered_df = filtered_df[col < val]
                except ValueError:
                    pass
                    
        elif f.operator == 'gte' and f.value:
            if is_numeric:
                try:
                    val = float(f.value)
                    filtered_df = filtered_df[col >= val]
                except ValueError:
                    pass
                    
        elif f.operator == 'lte' and f.value:
            if is_numeric:
                try:
                    val = float(f.value)
                    filtered_df = filtered_df[col <= val]
                except ValueError:
                    pass
                    
        elif f.operator == 'is_missing':
            filtered_df = filtered_df[col.isna()]
            
        elif f.operator == 'is_not_missing':
            filtered_df = filtered_df[col.notna()]
    
    return filtered_df
