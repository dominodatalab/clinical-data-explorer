"""Expression-filter routes for the MCP server.

Routes (moved verbatim from `mcp_server/app.py` as step 2.5a):
- POST /table/expression_filter
- GET  /table/expression_samples

Translators / validators / applier (translate_*, validate_expression_columns,
apply_expression_filter) live in `mcp_server/services/expressions.py`.
The pandas filter helper `apply_filters` lives in
`mcp_server/services/filters.py`. Numeric/categorical column detection
lives in `mcp_server/services/columns.py`.

`get_current_df` is imported from `mcp_server.session` per the S2 watch-out
("`_sessions` is module-level state accessed by every endpoint... every
route must import `get_current_df` from there").
"""
import json
import logging

import pandas as pd
from fastapi import APIRouter, HTTPException

from mcp_server.session import get_current_df
from mcp_server.services.columns import (
    _get_categorical_columns,
    _get_numeric_columns,
)
from mcp_server.services.expressions import (
    apply_expression_filter,
    validate_expression_columns,
)
from mcp_server.services.filters import apply_filters
from mcp_server.types import ExpressionFilterRequest

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/table/expression_filter")
async def expression_filter(request: ExpressionFilterRequest):
    """
    Filter table data using SAS WHERE, R dplyr, or Python pandas expression syntax.
    
    Supports:
    - SAS WHERE: AGE > 65 AND TRTA = 'Placebo'
    - R dplyr: AGE > 65 & TRTA == "Placebo"  
    - Python pandas: AGE > 65 & TRTA == "Placebo"
    """
    df = get_current_df()
    
    # Validate expression columns exist (pass syntax to know which keywords to exclude)
    unknown_cols = validate_expression_columns(request.expression, df, request.syntax)
    if unknown_cols:
        # Try to suggest similar column names
        suggestions = {}
        for unknown in unknown_cols:
            similar = [c for c in df.columns if unknown.lower() in c.lower() or c.lower() in unknown.lower()]
            if similar:
                suggestions[unknown] = similar[:3]
        
        error_msg = f"Unknown column(s): {', '.join(unknown_cols)}"
        if suggestions:
            suggestion_strs = [f"'{k}' - did you mean: {', '.join(v)}?" for k, v in suggestions.items()]
            error_msg += ". " + " ".join(suggestion_strs)
        
        raise HTTPException(status_code=400, detail=error_msg)
    
    # Apply UI filters first (if any)
    filtered_df = apply_filters(df, request.filters)
    
    # Apply expression filter
    try:
        filtered_df = apply_expression_filter(filtered_df, request.expression, request.syntax)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Expression filter error: {e}")
        raise HTTPException(status_code=400, detail=f"Error applying expression: {str(e)}")
    
    # Apply sorting
    if request.sort_column and request.sort_column in filtered_df.columns:
        ascending = request.sort_direction == "asc"
        filtered_df = filtered_df.sort_values(by=request.sort_column, ascending=ascending, na_position='last')
    
    # Calculate pagination
    total_rows = len(filtered_df)
    total_pages = max(1, (total_rows + request.page_size - 1) // request.page_size)
    page = max(1, min(request.page, total_pages))
    
    start_idx = (page - 1) * request.page_size
    end_idx = start_idx + request.page_size
    
    # Get page of data
    page_df = filtered_df.iloc[start_idx:end_idx]
    
    # Convert to JSON-serializable format
    try:
        data = json.loads(page_df.to_json(orient='records', date_format='iso', default_handler=str))
    except Exception as e:
        logger.error(f"Error serializing table data: {e}")
        data = []
        for _, row in page_df.iterrows():
            record = {}
            for col in page_df.columns:
                val = row[col]
                if pd.isna(val):
                    record[col] = None
                elif hasattr(val, 'item'):
                    record[col] = val.item()
                elif hasattr(val, 'isoformat'):
                    record[col] = val.isoformat()
                else:
                    record[col] = val
            data.append(record)
    
    return {
        "data": data,
        "page": page,
        "page_size": request.page_size,
        "total_rows": total_rows,
        "total_pages": total_pages,
        "filtered_rows": total_rows,
        "unfiltered_rows": len(df),
        "expression_applied": request.expression,
        "syntax": request.syntax
    }


@router.get("/table/expression_samples")
async def get_expression_samples():
    """
    Get sample column names and values for generating dynamic expression examples.
    This helps users write expressions with actual column names from their data.
    """
    df = get_current_df()
    
    numeric_cols = _get_numeric_columns(df)
    categorical_cols = _get_categorical_columns(df, numeric_cols)
    
    # Get sample numeric columns with their ranges
    numeric_samples = []
    for col in numeric_cols[:3]:  # Limit to 3 numeric columns
        col_data = df[col].dropna()
        if len(col_data) > 0:
            min_val = float(col_data.min())
            max_val = float(col_data.max())
            # Use median as a "typical" sample value
            sample_val = float(col_data.median())
            numeric_samples.append({
                "column": col,
                "min": round(min_val, 2),
                "max": round(max_val, 2),
                "sample": round(sample_val, 2)
            })
    
    # Get sample categorical columns with their top values
    categorical_samples = []
    for col in categorical_cols[:3]:  # Limit to 3 categorical columns
        value_counts = df[col].value_counts().head(3)
        if len(value_counts) > 0:
            values = [str(v) for v in value_counts.index.tolist()]
            categorical_samples.append({
                "column": col,
                "values": values
            })
    
    # Identify flag columns (ending in FL with Y/N values)
    flag_columns = []
    for col in df.columns:
        if col.upper().endswith('FL'):
            unique_vals = df[col].dropna().unique()
            if len(unique_vals) <= 3:  # Likely a flag
                flag_columns.append(col)
    
    # Identify date columns
    date_columns = []
    for col in df.columns:
        dtype_str = str(df[col].dtype).lower()
        if 'datetime' in dtype_str or 'timestamp' in dtype_str:
            date_columns.append(col)
        elif col.upper().endswith('DT') or col.upper().endswith('DTM'):
            # Common clinical date column naming conventions
            date_columns.append(col)
    
    return {
        "numeric_samples": numeric_samples,
        "categorical_samples": categorical_samples,
        "flag_columns": flag_columns[:5],  # Limit to 5
        "date_columns": date_columns[:5],  # Limit to 5
        "all_columns": df.columns.tolist()
    }
