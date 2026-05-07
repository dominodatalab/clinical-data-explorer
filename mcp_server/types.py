"""Pydantic request/response models for the MCP server.

Extracted from `mcp_server/app.py` as step 2.2 of REFACTOR_PLAN.md §2. The
models are grouped by the endpoint area that owns them in the eventual
`routes/*` split (datasets, analytics, table-view, expression-filter,
chart-aggregation), but they all live in this single file because:

  - Several models cross area boundaries (`FilterCondition` is used by both
    `TableDataRequest` and `ExpressionFilterRequest`; `ChartFilterCondition`
    is used by every chart-aggregation request model).
  - Splitting per-area would create import cycles between routes and types
    once step 2.5 lands.

Definitions are byte-equivalent to the originals — no fields renamed, no
defaults changed, no validators added.
"""
from typing import Dict, List, Optional

from pydantic import BaseModel

CHART_BUCKETS_LIMIT = 50
XY_CHART_MAX_POINTS_LIMIT = 1000

# ===== Dataset / analytics models =====

class DatasetInfo(BaseModel):
    columns: List[str]
    num_rows: int
    num_features: int
    column_types: Dict[str, str]
    numeric_columns: List[str]
    categorical_columns: List[str]


class FeatureStats(BaseModel):
    feature: str
    mean: Optional[float] = None
    median: Optional[float] = None
    std: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None
    missing_values: int
    unique_values: Optional[int] = None


class CorrelationData(BaseModel):
    feature_pairs: List[Dict[str, float]]


class DatasetList(BaseModel):
    datasets: List[str]
    current_dataset: Optional[str]


# ===== Table view models =====

class FilterCondition(BaseModel):
    column: str
    operator: str  # 'is', 'is_not', 'contains', 'not_contains', 'between', 'gt', 'lt', 'gte', 'lte', 'is_missing', 'is_not_missing'
    value: Optional[str] = None
    value2: Optional[str] = None  # For 'between' operator


class TableDataRequest(BaseModel):
    page: int = 1
    page_size: int = 100
    filters: List[FilterCondition] = []
    sort_column: Optional[str] = None
    sort_direction: str = "asc"  # 'asc' or 'desc'
    # Optional expression filter fields
    expression: Optional[str] = None
    syntax: Optional[str] = None  # 'sas', 'r', 'python'


# ===== Expression filter models =====

class ExpressionFilterRequest(BaseModel):
    expression: str
    syntax: str  # 'sas', 'r', 'python'
    page: int = 1
    page_size: int = 100
    sort_column: Optional[str] = None
    sort_direction: str = "asc"
    # Additional UI filters can still be applied
    filters: List[FilterCondition] = []


# ===== Chart aggregation models =====

class ChartFilterCondition(BaseModel):
    column: str
    value: str


class BarChartRequest(BaseModel):
    category_column: str
    aggregation: str = "count"  # 'count', 'mean:col', 'sum:col', 'min:col', 'max:col'
    filter: Optional[ChartFilterCondition] = None
    limit: int = 20

    def get_limit(self):
        return min(self.limit, CHART_BUCKETS_LIMIT)


class XYChartRequest(BaseModel):
    x_column: str
    y_column: str
    aggregation: str = "none"  # 'none', 'mean', 'sum', 'min', 'max'
    filter: Optional[ChartFilterCondition] = None
    max_points: int = XY_CHART_MAX_POINTS_LIMIT  # Limit points for scatter plots
    num_buckets: int = CHART_BUCKETS_LIMIT   # For bucketed aggregation

    def get_max_points(self):
        return min(self.max_points, XY_CHART_MAX_POINTS_LIMIT)

    def get_num_buckets(self):
        return min(self.num_buckets, CHART_BUCKETS_LIMIT)


class TimeSeriesRequest(BaseModel):
    date_column: str
    value_column: str
    aggregation: str = "mean"  # 'mean', 'sum', 'min', 'max', 'count'
    filter: Optional[ChartFilterCondition] = None
    num_buckets: int = CHART_BUCKETS_LIMIT

    def get_num_buckets(self):
        return min(self.num_buckets, CHART_BUCKETS_LIMIT)

class HistogramRequest(BaseModel):
    column: str
    bins: int = 30
    filter: Optional[ChartFilterCondition] = None

    def get_bins(self):
        return min(self.bins, CHART_BUCKETS_LIMIT)
