"""Table view routes for the MCP server.

Routes (moved verbatim from `mcp_server/app.py` as step 2.5d):
- POST /table/data
- GET  /table/column_values/{column}
- POST /table/summary
- GET  /table/column_stats/{column}

`get_current_df` is imported from `mcp_server.session` per the S2 watch-out
("`_sessions` is module-level state accessed by every endpoint... every
route must import `get_current_df` from there"). The pandas filter helper
`apply_filters` lives in `mcp_server/services/filters.py`; the expression
filter applier lives in `mcp_server/services/expressions.py`.
Request models / FilterCondition live in `mcp_server/types.py`.
"""
import json
import logging
from typing import Optional

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from mcp_server.session import get_current_df
from mcp_server.services.expressions import apply_expression_filter
from mcp_server.services.filters import apply_filters
from mcp_server.types import FilterCondition, TableDataRequest

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/table/data")
async def get_table_data(request: TableDataRequest):
    """Get paginated table data with filtering and sorting"""
    df = get_current_df()
    
    # Apply filters
    filtered_df = apply_filters(df, request.filters)
    
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
        "unfiltered_rows": len(df)
    }


@router.get("/table/column_values/{column}")
async def get_column_values(
    column: str, 
    search: Optional[str] = Query("", description="Search term to filter values"),
    limit: int = Query(20, description="Maximum number of values to return"),
    filters: Optional[str] = Query(None, description="JSON string of filter conditions"),
    expression: Optional[str] = Query(None, description="Expression filter (SAS/R/Python)"),
    syntax: Optional[str] = Query(None, description="Expression syntax: 'sas', 'r', or 'python'")
):
    """Get distinct values for a column with optional search (for autocomplete)"""
    df = get_current_df()
    
    if column not in df.columns:
        raise HTTPException(status_code=404, detail=f"Column '{column}' not found")
    
    # Parse and apply regular filters if provided
    if filters:
        try:
            filter_data = json.loads(filters)
            filter_list = [FilterCondition(**f) for f in filter_data]
            df = apply_filters(df, filter_list)
        except Exception as e:
            logger.warning(f"Could not parse filters: {e}")
    
    # Apply expression filter if provided
    if expression and syntax:
        df = apply_expression_filter(df, expression, syntax)
    
    # Get unique values
    unique_vals = df[column].dropna().unique()
    
    # Convert to strings for consistent handling
    unique_strs = [str(v) for v in unique_vals]
    
    # Filter by search term if provided
    if search:
        search_lower = search.lower()
        unique_strs = [v for v in unique_strs if search_lower in v.lower()]
    
    # Sort and limit
    unique_strs.sort()
    
    return {
        "column": column,
        "values": unique_strs[:limit],
        "total_unique": len(unique_vals),
        "is_numeric": pd.api.types.is_numeric_dtype(df[column])
    }


@router.post("/table/summary")
async def get_table_summary(request: TableDataRequest):
    """Get summary statistics for the filtered data"""
    df = get_current_df()
    
    # Apply regular filters
    filtered_df = apply_filters(df, request.filters)
    
    # Apply expression filter if provided
    if request.expression and request.syntax:
        filtered_df = apply_expression_filter(filtered_df, request.expression, request.syntax)
    
    total_rows = len(filtered_df)
    unfiltered_rows = len(df)
    
    # Calculate missing values per column
    missing_by_column = {}
    total_missing = 0
    for col in filtered_df.columns:
        missing_count = int(filtered_df[col].isna().sum())
        missing_by_column[col] = missing_count
        total_missing += missing_count
    
    total_cells = total_rows * len(filtered_df.columns)
    missing_percentage = (total_missing / total_cells * 100) if total_cells > 0 else 0
    
    # Columns with most missing
    cols_by_missing = sorted(missing_by_column.items(), key=lambda x: x[1], reverse=True)
    
    return {
        "total_rows": total_rows,
        "unfiltered_rows": unfiltered_rows,
        "columns": filtered_df.columns.tolist(),
        "missing_values": {
            "total_missing_cells": total_missing,
            "total_cells": total_cells,
            "missing_percentage": round(missing_percentage, 2),
            "by_column": missing_by_column,
            "columns_with_most_missing": cols_by_missing[:10]
        }
    }


@router.get("/table/column_stats/{column}")
async def get_column_stats(
    column: str, 
    filters: Optional[str] = Query(None, description="JSON string of filter conditions"),
    expression: Optional[str] = Query(None, description="Expression filter (SAS/R/Python)"),
    syntax: Optional[str] = Query(None, description="Expression syntax: 'sas', 'r', or 'python'")
):
    """Get statistics for a specific column, optionally with filters applied"""
    df = get_current_df()
    
    if column not in df.columns:
        raise HTTPException(status_code=404, detail=f"Column '{column}' not found")
    
    # Parse filters from JSON string if provided
    filter_list = []
    if filters:
        try:
            filter_data = json.loads(filters)
            filter_list = [FilterCondition(**f) for f in filter_data]
        except Exception as e:
            logger.warning(f"Could not parse filters: {e}")
    
    # Apply regular filters
    filtered_df = apply_filters(df, filter_list)
    
    # Apply expression filter if provided
    if expression and syntax:
        filtered_df = apply_expression_filter(filtered_df, expression, syntax)
    
    col_data = filtered_df[column]
    is_numeric = pd.api.types.is_numeric_dtype(col_data)
    
    stats = {
        "column": column,
        "is_numeric": is_numeric,
        "total_count": len(col_data),
        "non_null_count": int(col_data.notna().sum()),
        "null_count": int(col_data.isna().sum()),
        "unique_count": int(col_data.nunique())
    }
    
    if is_numeric:
        numeric_data = col_data.dropna()
        if len(numeric_data) > 0:
            stats.update({
                "mean": float(numeric_data.mean()),
                "median": float(numeric_data.median()),
                "std": float(numeric_data.std()) if len(numeric_data) > 1 else 0,
                "min": float(numeric_data.min()),
                "max": float(numeric_data.max()),
                "sum": float(numeric_data.sum())
            })
    else:
        # For non-numeric, get top values
        value_counts = col_data.value_counts().head(5).to_dict()
        stats["top_values"] = {str(k): int(v) for k, v in value_counts.items()}
    
    return stats
