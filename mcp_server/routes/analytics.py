"""Analytics / feature-statistics routes for the MCP server.

Routes (moved verbatim from `mcp_server/app.py` as step 2.5b):
- GET /feature/stats
- GET /correlation/matrix
- GET /correlation/target
- GET /feature/histogram/{feature}
- GET /feature/boxplot/{feature}
- GET /feature/comparison
- GET /feature/group_analysis
- GET /feature/value_counts/{feature}

`get_current_df` is imported from `mcp_server.session` per the S2
watch-out ("`_sessions` is module-level state accessed by every
endpoint... every route must import `get_current_df` from there").
"""
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from mcp_server.session import get_current_df
from mcp_server.types import FeatureStats

router = APIRouter()


@router.get("/feature/stats", response_model=List[FeatureStats])
async def get_feature_stats(features: Optional[List[str]] = Query(None)):
    """Get detailed statistics for specified features or all features"""
    df = get_current_df()
    
    if features is None:
        features = df.columns.tolist()
    else:
        for feature in features:
            if feature not in df.columns:
                raise HTTPException(status_code=404, detail=f"Feature '{feature}' not found")
    
    stats = []
    for feature in features:
        stat_dict = {
            "feature": feature,
            "missing_values": int(df[feature].isna().sum())
        }
        
        if pd.api.types.is_numeric_dtype(df[feature]):
            stat_dict.update({
                "mean": float(df[feature].mean()),
                "median": float(df[feature].median()),
                "std": float(df[feature].std()),
                "min": float(df[feature].min()),
                "max": float(df[feature].max())
            })
        
        # Add unique values count for all columns
        stat_dict["unique_values"] = int(df[feature].nunique())
        
        stats.append(stat_dict)
    
    return stats

@router.get("/correlation/matrix")
async def get_correlation_matrix():
    """Get the correlation matrix for all numeric features"""
    df = get_current_df()
    numeric_df = df.select_dtypes(include=[np.number])
    
    if numeric_df.empty:
        raise HTTPException(status_code=400, detail="No numeric columns found in the dataset")
    
    return numeric_df.corr().to_dict()

@router.get("/correlation/target", response_model=Dict[str, float])
async def get_target_correlation(target: str = Query(..., description="Target variable to calculate correlations against")):
    """Get correlation coefficients between each feature and the specified target variable"""
    df = get_current_df()
    
    if target not in df.columns:
        raise HTTPException(status_code=404, detail=f"Target variable '{target}' not found")
    
    if not pd.api.types.is_numeric_dtype(df[target]):
        raise HTTPException(status_code=400, detail=f"Target variable '{target}' must be numeric")
    
    numeric_df = df.select_dtypes(include=[np.number])
    if target not in numeric_df.columns:
        raise HTTPException(status_code=400, detail=f"Target variable '{target}' is not numeric")
    
    correlations = numeric_df.corr()[target].drop(target).to_dict()
    return {k: float(v) for k, v in correlations.items()}

@router.get("/feature/histogram/{feature}")
async def get_feature_histogram(feature: str, bins: int = Query(10, description="Number of bins")):
    """Get histogram data for a specific feature"""
    df = get_current_df()
    
    if feature not in df.columns:
        raise HTTPException(status_code=404, detail=f"Feature '{feature}' not found")
    
    if not pd.api.types.is_numeric_dtype(df[feature]):
        raise HTTPException(status_code=400, detail=f"Feature '{feature}' is not numeric")
    
    hist, bin_edges = np.histogram(df[feature].dropna(), bins=bins)
    return {
        "counts": hist.tolist(),
        "bins": bin_edges.tolist(),
        "feature": feature
    }

@router.get("/feature/boxplot/{feature}")
async def get_feature_boxplot(feature: str):
    """Get boxplot data for a specific feature"""
    df = get_current_df()
    
    if feature not in df.columns:
        raise HTTPException(status_code=404, detail=f"Feature '{feature}' not found")
    
    if not pd.api.types.is_numeric_dtype(df[feature]):
        raise HTTPException(status_code=400, detail=f"Feature '{feature}' is not numeric")
    
    q1 = float(df[feature].quantile(0.25))
    q3 = float(df[feature].quantile(0.75))
    iqr = q3 - q1
    lower_bound = float(max(df[feature].min(), q1 - 1.5 * iqr))
    upper_bound = float(min(df[feature].max(), q3 + 1.5 * iqr))
    
    outliers = df[feature][(df[feature] < lower_bound) | (df[feature] > upper_bound)].tolist()
    
    return {
        "feature": feature,
        "min": float(df[feature].min()),
        "q1": q1,
        "median": float(df[feature].median()),
        "q3": q3,
        "max": float(df[feature].max()),
        "outliers": outliers[:100] if len(outliers) > 100 else outliers  # Limit number of outliers
    }

@router.get("/feature/comparison")
async def get_feature_comparison(
    feature1: str, 
    feature2: str, 
    filter_column: Optional[str] = None, 
    filter_value: Optional[str] = None
):
    """
    Get data for comparing two features, optionally filtered by any column and value
    
    Parameters:
    - feature1: First feature to compare
    - feature2: Second feature to compare
    - filter_column: Optional column to filter on
    - filter_value: Optional value to filter for in filter_column
    """
    df = get_current_df()
    
    if feature1 not in df.columns or feature2 not in df.columns:
        missing = []
        if feature1 not in df.columns:
            missing.append(feature1)
        if feature2 not in df.columns:
            missing.append(feature2)
        raise HTTPException(status_code=404, detail=f"Features not found: {', '.join(missing)}")
    
    # Apply filtering if filter parameters are provided
    if filter_column and filter_value:
        if filter_column not in df.columns:
            raise HTTPException(status_code=404, detail=f"Filter column '{filter_column}' not found")
        
        try:
            # Try to convert filter_value to appropriate type based on column
            if pd.api.types.is_numeric_dtype(df[filter_column]):
                filter_value_converted = float(filter_value)
            elif pd.api.types.is_bool_dtype(df[filter_column]):
                filter_value_converted = filter_value.lower() in ['true', '1', 't', 'y', 'yes']
            else:
                filter_value_converted = filter_value
                
            filtered_df = df[df[filter_column] == filter_value_converted]
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Cannot convert filter value '{filter_value}' to the appropriate type for column '{filter_column}'")
    else:
        filtered_df = df
    
    data = filtered_df[[feature1, feature2]].dropna().to_dict(orient="records")
    
    return {
        "feature1": feature1,
        "feature2": feature2,
        "filter_applied": {
            "column": filter_column,
            "value": filter_value
        } if filter_column and filter_value else None,
        "data": data[:1000]  # Limit data points returned to prevent large responses
    }

@router.get("/feature/group_analysis")
async def get_group_analysis(feature: str, group_by: str = Query(..., description="Column to group by")):
    """Group the data by a column and calculate statistics for another feature in each group"""
    df = get_current_df()
    
    if feature not in df.columns or group_by not in df.columns:
        missing = []
        if feature not in df.columns:
            missing.append(feature)
        if group_by not in df.columns:
            missing.append(group_by)
        raise HTTPException(status_code=404, detail=f"Features not found: {', '.join(missing)}")
    
    if not pd.api.types.is_numeric_dtype(df[feature]):
        raise HTTPException(status_code=400, detail=f"Feature '{feature}' must be numeric")
    
    result = {}
    for group, group_df in df.groupby(group_by):
        result[str(group)] = {
            "count": len(group_df),
            "mean": float(group_df[feature].mean()),
            "median": float(group_df[feature].median()),
            "std": float(group_df[feature].std()),
            "min": float(group_df[feature].min()),
            "max": float(group_df[feature].max())
        }
    
    return {
        "feature": feature,
        "grouped_by": group_by,
        "analysis": result
    }

@router.get("/feature/value_counts/{feature}")
async def get_value_counts(feature: str, limit: int = Query(50, description="Maximum number of unique values to return")):
    """Get value counts for a feature (useful for categorical data)"""
    df = get_current_df()
    
    if feature not in df.columns:
        raise HTTPException(status_code=404, detail=f"Feature '{feature}' not found")
    
    value_counts = df[feature].value_counts().head(limit).to_dict()
    
    return {
        "feature": feature,
        "value_counts": {str(k): int(v) for k, v in value_counts.items()},
        "total_unique": int(df[feature].nunique()),
        "showing_top": min(limit, len(value_counts))
    }
