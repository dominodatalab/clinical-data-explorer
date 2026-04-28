"""Server-side chart aggregation routes for the MCP server.

Routes (moved verbatim from `mcp_server/app.py` as step 2.5c):
- POST /chart/bar_aggregation
- POST /chart/xy_data
- POST /chart/time_series
- POST /chart/histogram

These endpoints perform aggregations on the server and return only
summary data, avoiding the need to transfer millions of rows to the
client.

`get_current_df` is imported from `mcp_server.session` per the S2
watch-out ("`_sessions` is module-level state accessed by every
endpoint... every route must import `get_current_df` from there").
Request models live in `mcp_server/types.py`.
"""
import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException

from mcp_server.session import get_current_df
from mcp_server.types import (
    BarChartRequest,
    HistogramRequest,
    TimeSeriesRequest,
    XYChartRequest,
)

router = APIRouter()


@router.post("/chart/bar_aggregation")
async def get_bar_chart_data(request: BarChartRequest):
    """Get aggregated data for bar charts - performs groupby on server"""
    df = get_current_df()
    
    if request.category_column not in df.columns:
        raise HTTPException(status_code=404, detail=f"Column '{request.category_column}' not found")
    
    # Apply filter if provided
    if request.filter and request.filter.column in df.columns:
        filter_col = df[request.filter.column]
        if pd.api.types.is_numeric_dtype(filter_col):
            try:
                filter_val = float(request.filter.value)
                df = df[filter_col == filter_val]
            except ValueError:
                df = df[filter_col.astype(str) == request.filter.value]
        else:
            df = df[filter_col.astype(str) == request.filter.value]
    
    # Parse aggregation
    if request.aggregation == "count":
        # Simple value counts
        counts = df[request.category_column].value_counts().head(request.limit)
        chart_data = [{"label": str(k), "value": int(v)} for k, v in counts.items()]
    else:
        # Parse aggregation like "mean:column_name"
        parts = request.aggregation.split(":", 1)
        if len(parts) != 2:
            raise HTTPException(status_code=400, detail=f"Invalid aggregation format: {request.aggregation}")
        
        agg_type, agg_column = parts
        if agg_column not in df.columns:
            raise HTTPException(status_code=404, detail=f"Aggregation column '{agg_column}' not found")
        
        if not pd.api.types.is_numeric_dtype(df[agg_column]):
            raise HTTPException(status_code=400, detail=f"Column '{agg_column}' is not numeric")
        
        # Group by category and aggregate
        grouped = df.groupby(request.category_column, dropna=False)[agg_column]
        
        if agg_type == "mean":
            result = grouped.mean()
        elif agg_type == "sum":
            result = grouped.sum()
        elif agg_type == "min":
            result = grouped.min()
        elif agg_type == "max":
            result = grouped.max()
        else:
            raise HTTPException(status_code=400, detail=f"Unknown aggregation type: {agg_type}")
        
        # Sort by value descending and limit
        result = result.sort_values(ascending=False).head(request.limit)
        chart_data = [{"label": str(k), "value": float(v) if pd.notna(v) else None} for k, v in result.items()]
    
    return {
        "chart_data": chart_data,
        "category_column": request.category_column,
        "aggregation": request.aggregation,
        "total_categories": int(df[request.category_column].nunique())
    }


@router.post("/chart/xy_data")
async def get_xy_chart_data(request: XYChartRequest):
    """Get data for scatter/area charts with optional aggregation and sampling"""
    df = get_current_df()
    
    if request.x_column not in df.columns:
        raise HTTPException(status_code=404, detail=f"X column '{request.x_column}' not found")
    if request.y_column not in df.columns:
        raise HTTPException(status_code=404, detail=f"Y column '{request.y_column}' not found")
    
    # Apply filter if provided
    if request.filter and request.filter.column in df.columns:
        filter_col = df[request.filter.column]
        if pd.api.types.is_numeric_dtype(filter_col):
            try:
                filter_val = float(request.filter.value)
                df = df[filter_col == filter_val]
            except ValueError:
                df = df[filter_col.astype(str) == request.filter.value]
        else:
            df = df[filter_col.astype(str) == request.filter.value]
    
    x_col = df[request.x_column]
    y_col = df[request.y_column]
    is_numeric_x = pd.api.types.is_numeric_dtype(x_col)
    is_numeric_y = pd.api.types.is_numeric_dtype(y_col)
    
    if not is_numeric_y:
        raise HTTPException(status_code=400, detail=f"Y column '{request.y_column}' must be numeric")
    
    # Drop rows with NaN in either column
    valid_mask = x_col.notna() & y_col.notna()
    x_values = x_col[valid_mask]
    y_values = y_col[valid_mask]
    
    if len(x_values) == 0:
        return {"chart_data": [], "chart_type": "scatter", "x_column": request.x_column, "y_column": request.y_column}
    
    if request.aggregation == "none":
        # Scatter plot - sample if too many points
        if len(x_values) > request.max_points:
            indices = np.random.choice(len(x_values), request.max_points, replace=False)
            x_sample = x_values.iloc[indices]
            y_sample = y_values.iloc[indices]
        else:
            x_sample = x_values
            y_sample = y_values
        
        chart_data = []
        for x, y in zip(x_sample, y_sample):
            x_val = float(x) if is_numeric_x and pd.notna(x) else str(x) if pd.notna(x) else None
            y_val = float(y) if pd.notna(y) else None
            if x_val is not None and y_val is not None:
                chart_data.append({"x": x_val, "y": y_val})
        
        return {
            "chart_data": chart_data,
            "chart_type": "scatter",
            "x_column": request.x_column,
            "y_column": request.y_column,
            "sampled": len(x_values) > request.max_points,
            "total_points": len(x_values)
        }
    else:
        # Aggregated chart (area/line)
        if is_numeric_x:
            # Bucket numeric x values
            x_min, x_max = x_values.min(), x_values.max()
            if x_min == x_max:
                buckets = [x_min]
                bucket_labels = [str(x_min)]
            else:
                buckets = np.linspace(x_min, x_max, request.num_buckets + 1)
                bucket_labels = [(buckets[i] + buckets[i+1]) / 2 for i in range(len(buckets)-1)]
            
            # Assign each value to a bucket
            bucket_indices = np.digitize(x_values, buckets[1:-1])
            
            # Group and aggregate
            temp_df = pd.DataFrame({'bucket': bucket_indices, 'y': y_values})
            
            if request.aggregation == "mean":
                agg_result = temp_df.groupby('bucket')['y'].mean()
            elif request.aggregation == "sum":
                agg_result = temp_df.groupby('bucket')['y'].sum()
            elif request.aggregation == "min":
                agg_result = temp_df.groupby('bucket')['y'].min()
            elif request.aggregation == "max":
                agg_result = temp_df.groupby('bucket')['y'].max()
            else:
                agg_result = temp_df.groupby('bucket')['y'].mean()
            
            chart_data = []
            for bucket_idx in range(len(bucket_labels)):
                if bucket_idx in agg_result.index:
                    chart_data.append({
                        "x": float(bucket_labels[bucket_idx]),
                        "y": float(agg_result[bucket_idx]) if pd.notna(agg_result[bucket_idx]) else None
                    })
        else:
            # Categorical x - group by category
            temp_df = pd.DataFrame({'x': x_values, 'y': y_values})
            
            if request.aggregation == "mean":
                agg_result = temp_df.groupby('x')['y'].mean()
            elif request.aggregation == "sum":
                agg_result = temp_df.groupby('x')['y'].sum()
            elif request.aggregation == "min":
                agg_result = temp_df.groupby('x')['y'].min()
            elif request.aggregation == "max":
                agg_result = temp_df.groupby('x')['y'].max()
            else:
                agg_result = temp_df.groupby('x')['y'].mean()
            
            # Limit categories
            agg_result = agg_result.head(request.num_buckets)
            
            chart_data = [
                {"x": str(k), "y": float(v) if pd.notna(v) else None}
                for k, v in agg_result.items()
            ]
        
        return {
            "chart_data": chart_data,
            "chart_type": "area",
            "x_column": request.x_column,
            "y_column": request.y_column,
            "aggregation": request.aggregation
        }


@router.post("/chart/time_series")
async def get_time_series_data(request: TimeSeriesRequest):
    """Get aggregated time series data for line/area charts"""
    df = get_current_df()
    
    if request.date_column not in df.columns:
        raise HTTPException(status_code=404, detail=f"Date column '{request.date_column}' not found")
    if request.value_column not in df.columns:
        raise HTTPException(status_code=404, detail=f"Value column '{request.value_column}' not found")
    
    # Apply filter if provided
    if request.filter and request.filter.column in df.columns:
        filter_col = df[request.filter.column]
        if pd.api.types.is_numeric_dtype(filter_col):
            try:
                filter_val = float(request.filter.value)
                df = df[filter_col == filter_val]
            except ValueError:
                df = df[filter_col.astype(str) == request.filter.value]
        else:
            df = df[filter_col.astype(str) == request.filter.value]
    
    # Convert date column to datetime
    try:
        dates = pd.to_datetime(df[request.date_column], errors='coerce')
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse dates: {e}")
    
    values = df[request.value_column]
    
    if not pd.api.types.is_numeric_dtype(values):
        raise HTTPException(status_code=400, detail=f"Value column '{request.value_column}' must be numeric")
    
    # Drop invalid rows
    valid_mask = dates.notna() & values.notna()
    dates = dates[valid_mask]
    values = values[valid_mask]
    
    if len(dates) == 0:
        return {"chart_data": [], "chart_type": "time_series"}
    
    # Create time buckets
    date_min, date_max = dates.min(), dates.max()
    
    if date_min == date_max:
        # All same date
        if request.aggregation == "count":
            agg_val = len(values)
        elif request.aggregation == "sum":
            agg_val = float(values.sum())
        elif request.aggregation == "mean":
            agg_val = float(values.mean())
        elif request.aggregation == "min":
            agg_val = float(values.min())
        elif request.aggregation == "max":
            agg_val = float(values.max())
        else:
            agg_val = float(values.mean())
        
        return {
            "chart_data": [{"x": date_min.isoformat(), "y": agg_val}],
            "chart_type": "time_series",
            "date_column": request.date_column,
            "value_column": request.value_column
        }
    
    # Create time buckets using pd.cut
    time_buckets = pd.cut(dates, bins=request.num_buckets, labels=False)
    bucket_dates = pd.date_range(date_min, date_max, periods=request.num_buckets + 1)
    bucket_centers = [(bucket_dates[i] + (bucket_dates[i+1] - bucket_dates[i])/2) for i in range(len(bucket_dates)-1)]
    
    temp_df = pd.DataFrame({'bucket': time_buckets, 'value': values})
    
    if request.aggregation == "count":
        agg_result = temp_df.groupby('bucket')['value'].count()
    elif request.aggregation == "sum":
        agg_result = temp_df.groupby('bucket')['value'].sum()
    elif request.aggregation == "mean":
        agg_result = temp_df.groupby('bucket')['value'].mean()
    elif request.aggregation == "min":
        agg_result = temp_df.groupby('bucket')['value'].min()
    elif request.aggregation == "max":
        agg_result = temp_df.groupby('bucket')['value'].max()
    else:
        agg_result = temp_df.groupby('bucket')['value'].mean()
    
    chart_data = []
    for bucket_idx in range(len(bucket_centers)):
        if bucket_idx in agg_result.index and pd.notna(agg_result[bucket_idx]):
            chart_data.append({
                "x": bucket_centers[bucket_idx].isoformat(),
                "y": float(agg_result[bucket_idx])
            })
    
    # Sort by date
    chart_data.sort(key=lambda d: d["x"])
    
    return {
        "chart_data": chart_data,
        "chart_type": "time_series",
        "date_column": request.date_column,
        "value_column": request.value_column,
        "aggregation": request.aggregation
    }


@router.post("/chart/histogram")
async def get_histogram_data(request: HistogramRequest):
    """Get histogram data for a numeric column with optional filter support.
    
    Returns bin edges and counts suitable for rendering a standard histogram.
    For categorical columns, returns value counts instead.
    """
    df = get_current_df()
    
    if request.column not in df.columns:
        raise HTTPException(status_code=404, detail=f"Column '{request.column}' not found")
    
    # Apply filter if provided
    if request.filter and request.filter.column in df.columns:
        filter_col = df[request.filter.column]
        if pd.api.types.is_numeric_dtype(filter_col):
            try:
                filter_val = float(request.filter.value)
                df = df[filter_col == filter_val]
            except ValueError:
                df = df[filter_col.astype(str) == request.filter.value]
        else:
            df = df[filter_col.astype(str) == request.filter.value]
    
    col_data = df[request.column].dropna()
    
    if len(col_data) == 0:
        return {"chart_data": [], "column": request.column, "chart_type": "histogram", "is_numeric": True, "total_count": 0}
    
    is_numeric = pd.api.types.is_numeric_dtype(col_data)
    
    if is_numeric:
        # Standard numeric histogram using numpy
        counts, bin_edges = np.histogram(col_data, bins=min(request.bins, len(col_data.unique())))
        
        chart_data = []
        for i in range(len(counts)):
            chart_data.append({
                "bin_start": float(bin_edges[i]),
                "bin_end": float(bin_edges[i + 1]),
                "count": int(counts[i])
            })
        
        return {
            "chart_data": chart_data,
            "column": request.column,
            "chart_type": "histogram",
            "is_numeric": True,
            "total_count": int(len(col_data)),
            "stats": {
                "mean": float(col_data.mean()),
                "median": float(col_data.median()),
                "std": float(col_data.std()) if len(col_data) > 1 else 0.0,
                "min": float(col_data.min()),
                "max": float(col_data.max())
            }
        }
    else:
        # Categorical: return value counts as a bar-style histogram
        value_counts = col_data.astype(str).value_counts().head(50)
        chart_data = [{"label": str(k), "count": int(v)} for k, v in value_counts.items()]
        
        return {
            "chart_data": chart_data,
            "column": request.column,
            "chart_type": "categorical_histogram",
            "is_numeric": False,
            "total_count": int(len(col_data)),
            "unique_count": int(col_data.nunique())
        }
