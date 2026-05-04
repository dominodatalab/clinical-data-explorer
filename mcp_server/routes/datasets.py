"""Dataset routes for the MCP server.

Routes (moved verbatim from `mcp_server/app.py` as step 2.5e):
- GET  /datasets/list
- POST /dataset/load
- GET  /dataset/info
- GET  /dataset/head
- GET  /dataset/describe
- GET  /dataset/data

`/dataset/data` lives here (rather than in routes/tables.py) because per the
plan's target layout it's a dataset-level snapshot endpoint — it returns the
whole DataFrame, not a paginated/filtered table view. The plan's
`routes/datasets.py` listing did not enumerate it explicitly, but grouping
by URL prefix matches the natural code/cohesion split.

`get_current_df` is imported from `mcp_server.session` per the S2 watch-out
("`_sessions` is module-level state accessed by every endpoint... every
route must import `get_current_df` from there"). Data-loading helpers
(`find_data_files`, `load_dataset`) live in
`mcp_server/services/data_loading.py`; numeric/categorical column detection
lives in `mcp_server/services/columns.py`.
"""
import json
import logging

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from mcp_server.session import _get_session_dataset_name, get_current_df, load_current_df
from mcp_server.services.columns import (
    _get_categorical_columns,
    _get_numeric_columns,
)
from mcp_server.services.data_loading import find_data_files
from mcp_server.types import DatasetInfo, DatasetList

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/datasets/list", response_model=DatasetList)
async def list_datasets():
    """List all available data files (CSV and Parquet) from datasets/ and /mnt/data/"""
    data_files = find_data_files()
    # Return just the display names for the dropdown
    file_names = [f['name'] for f in data_files]
    return {"datasets": file_names, "current_dataset": _get_session_dataset_name()}


@router.post("/dataset/load", operation_id="load_dataset")
async def load_dataset_endpoint(
    file_snapshot_path: str = Query(..., description="Dataset file path or downloaded snapshot path to load")
):
    """Load a specific dataset file and return column metadata."""
    df = load_current_df(file_snapshot_path)

    # Return column metadata so UI can initialize immediately without fetching all data
    numeric_cols = _get_numeric_columns(df)
    categorical_cols = _get_categorical_columns(df, numeric_cols)

    # Detect date columns
    date_cols = []
    for col in df.columns:
        dtype_str = str(df[col].dtype).lower()
        if 'datetime' in dtype_str or 'timestamp' in dtype_str:
            date_cols.append(col)
        elif df[col].dtype == 'object' or 'string' in dtype_str:
            # Sample first 100 non-null values to check if they look like dates
            sample = df[col].dropna().head(100)
            if len(sample) > 0:
                try:
                    pd.to_datetime(sample, errors='raise')
                    date_cols.append(col)
                except:
                    pass

    column_types = {col: str(df[col].dtype) for col in df.columns}

    return {
        "message": f"Successfully loaded dataset: {file_snapshot_path}",
        "dataset": file_snapshot_path,
        "columns": df.columns.tolist(),
        "numeric_columns": numeric_cols,
        "categorical_columns": categorical_cols,
        "date_columns": date_cols,
        "column_types": column_types,
        "num_rows": len(df)
    }


@router.get("/dataset/info", response_model=DatasetInfo)
def get_dataset_info():
    """Get basic information about the current dataset"""
    df = get_current_df()

    # Identify numeric and categorical columns using robust detection
    numeric_cols = _get_numeric_columns(df)
    categorical_cols = _get_categorical_columns(df, numeric_cols)

    # Get column types
    column_types = {col: str(df[col].dtype) for col in df.columns}

    return {
        "columns": df.columns.tolist(),
        "num_rows": len(df),
        "num_features": len(df.columns),
        "column_types": column_types,
        "numeric_columns": numeric_cols,
        "categorical_columns": categorical_cols
    }


@router.get("/dataset/head")
async def get_dataset_head(rows: int = Query(5, description="Number of rows to return")):
    """Get the first n rows of the dataset"""
    df = get_current_df()

    if rows <= 0:
        raise HTTPException(status_code=400, detail="Rows parameter must be positive")

    return df.head(rows).to_dict(orient="records")


@router.get("/dataset/describe")
async def get_dataset_description():
    """Get basic statistics for all numeric columns"""
    df = get_current_df()
    return json.loads(df.describe().to_json())


@router.get("/dataset/data")
async def get_dataset_data():
    """Get full dataset data with metadata for visualization.

    NOTE: This endpoint transfers ALL data to the client. For large datasets,
    prefer using the server-side chart endpoints (/chart/*) instead.
    """
    df = get_current_df()

    # Identify numeric and categorical columns using robust detection
    numeric_cols = _get_numeric_columns(df)
    categorical_cols = _get_categorical_columns(df, numeric_cols)

    # Detect date columns - check both datetime dtypes and string columns that look like dates
    date_cols = []
    for col in df.columns:
        dtype_str = str(df[col].dtype).lower()
        # Check if already a datetime type
        if 'datetime' in dtype_str or 'timestamp' in dtype_str:
            date_cols.append(col)
        # Check if object/string column that looks like dates
        elif df[col].dtype == 'object' or 'string' in dtype_str:
            try:
                pd.to_datetime(df[col], errors='raise')
                date_cols.append(col)
            except:
                pass

    # Convert dataframe to records with proper JSON serialization
    # Handle NaN, datetime, and numpy types that aren't directly JSON serializable
    try:
        # Create a copy and convert problematic types
        df_serializable = df.copy()

        for col in df_serializable.columns:
            dtype_str = str(df_serializable[col].dtype).lower()

            # Convert datetime columns to ISO string format
            if 'datetime' in dtype_str or 'timestamp' in dtype_str:
                df_serializable[col] = df_serializable[col].astype(str).replace('NaT', None)
            # Convert timedelta to string
            elif 'timedelta' in dtype_str:
                df_serializable[col] = df_serializable[col].astype(str).replace('NaT', None)
            # Ensure numeric types are native Python types
            elif pd.api.types.is_numeric_dtype(df_serializable[col]):
                # Replace inf values with None
                df_serializable[col] = df_serializable[col].replace([np.inf, -np.inf], np.nan)

        # Convert to dict, replacing NaN with None for JSON compatibility
        data = json.loads(df_serializable.to_json(orient='records', date_format='iso', default_handler=str))

    except Exception as e:
        logger.error(f"Error converting dataframe to JSON: {e}")
        # Fallback: convert each record manually with error handling
        data = []
        for _, row in df.iterrows():
            record = {}
            for col in df.columns:
                val = row[col]
                # Handle NaN/None
                if pd.isna(val):
                    record[col] = None
                # Handle numpy types
                elif hasattr(val, 'item'):
                    record[col] = val.item()
                # Handle datetime
                elif hasattr(val, 'isoformat'):
                    record[col] = val.isoformat()
                else:
                    record[col] = val
            data.append(record)

    # Get column types
    column_types = {col: str(df[col].dtype) for col in df.columns}

    return {
        "data": data,
        "columns": df.columns.tolist(),
        "numeric_columns": numeric_cols,
        "categorical_columns": categorical_cols,
        "date_columns": date_cols,
        "column_types": column_types,
        "num_rows": len(df)
    }
