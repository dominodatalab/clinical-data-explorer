"""SAS / R / Python expression-filter translators + applier.

Extracted from `mcp_server/app.py` as step 2.4c of REFACTOR_PLAN.md §2.

Per the §2 watch-out, the three expression translators (SAS WHERE / R
dplyr / Python pandas) share helper logic and column-name normalization
— they stay in one file and are never split per-language.

Public surface (used by the still-inline `/table/expression_filter`,
`/table/data`, `/table/summary`, `/table/column_values/{column}`,
`/table/column_stats/{column}` route handlers in `mcp_server/app.py` —
those move into `mcp_server/routes/{filters,tables}.py` in P8/P9):

- `apply_expression_filter(df, expression, syntax)` — main entry point
- `validate_expression_columns(expression, df, syntax)` — pre-flight
  unknown-column check used by the route layer to build helpful error
  messages with column-name suggestions
- `translate_sas_to_pandas`, `translate_r_to_pandas`,
  `translate_python_to_pandas` — language-specific translators
- `normalize_column_names_in_expression` — case-insensitive column-name
  normalization (e.g. `WEEK` → `week` if the actual column is `week`)

Like `services/data_loading.py`, this module raises `HTTPException`
directly when expression evaluation fails. Per ground rule #2 the
behavior is preserved exactly — translating to a sentinel exception at
the route layer would be a behavior change and is out of scope.
"""
import logging
import re as regex_module
from typing import List

import pandas as pd
from fastapi import HTTPException

logger = logging.getLogger(__name__)


def translate_sas_to_pandas(expression: str, df: pd.DataFrame) -> str:
    """
    Translate SAS WHERE clause syntax to pandas query syntax.
    
    SAS operators supported:
    - AND, OR, NOT -> and, or, not
    - EQ, NE, GT, LT, GE, LE -> ==, !=, >, <, >=, <=
    - = (equality) -> ==
    - IN (...) -> .isin([...])
    - LIKE with % -> str.contains()
    - IS MISSING, IS NOT MISSING -> .isna(), .notna()
    - String literals: 'value' or "value"
    """
    if not expression or not expression.strip():
        return ""
    
    result = expression.strip()
    
    # SAS is case-insensitive for keywords, but we need to preserve column names
    # First, let's handle keywords case-insensitively
    
    # Handle IS NOT MISSING / IS MISSING (must be done before other replacements)
    # Pattern: column IS NOT MISSING or column IS MISSING
    def replace_missing(match):
        col = match.group(1).strip()
        is_not = match.group(2) is not None
        if is_not:
            return f"{col}.notna()"
        else:
            return f"{col}.isna()"
    
    result = regex_module.sub(
        r'(\w+)\s+IS\s+(NOT\s+)?MISSING',
        replace_missing,
        result,
        flags=regex_module.IGNORECASE
    )
    
    # Handle NOT IN operator
    def replace_not_in(match):
        col = match.group(1).strip()
        values = match.group(2).strip()
        values = values.replace("'", '"')
        return f"~{col}.isin([{values}])"

    result = regex_module.sub(
        r'(\w+)\s+NOT\s+IN\s*\(([^)]+)\)',
        replace_not_in,
        result,
        flags=regex_module.IGNORECASE
    )

    # Handle IN operator: column IN ('val1', 'val2') or column IN (1, 2, 3)
    def replace_in(match):
        col = match.group(1).strip()
        values = match.group(2).strip()
        # Convert SAS-style values to Python list
        # Replace single quotes with double quotes for consistency
        values = values.replace("'", '"')
        return f"{col}.isin([{values}])"
    
    result = regex_module.sub(
        r'(\w+)\s+IN\s*\(([^)]+)\)',
        replace_in,
        result,
        flags=regex_module.IGNORECASE
    )
    
    # Replace SAS comparison operators (word form) - case insensitive
    # Must do these before = replacement to avoid conflicts
    replacements = [
        (r'\bEQ\b', '=='),
        (r'\bNE\b', '!='),
        (r'\bGE\b', '>='),
        (r'\bLE\b', '<='),
        (r'\bGT\b', '>'),
        (r'\bLT\b', '<'),
    ]

    for pattern, replacement in replacements:
        result = regex_module.sub(pattern, replacement, result, flags=regex_module.IGNORECASE)

    # Replace single = with == (but not if already == or part of >=, <=, !=)
    # Use negative lookbehind and lookahead
    result = regex_module.sub(r'(?<![=!<>])=(?!=)', '==', result)

    # Handle NOT LIKE
    def replace_not_like(match):
        col = match.group(1).strip()
        pattern = match.group(2).strip().strip("'\"")
        if pattern.startswith('%') and pattern.endswith('%'):
            pattern = pattern[1:-1]
            return f"~{col}.str.contains('{pattern}', case=False, na=False)"
        elif pattern.startswith('%'):
            pattern = pattern[1:]
            return f"~{col}.str.endswith('{pattern}', na=False)"
        elif pattern.endswith('%'):
            pattern = pattern[:-1]
            return f"~{col}.str.startswith('{pattern}', na=False)"
        else:
            return f"{col} != '{pattern}'"
    
    result = regex_module.sub(
        r"(\w+)\s+NOT\s+LIKE\s+['\"]([^'\"]+)['\"]",
        replace_not_like,
        result,
        flags=regex_module.IGNORECASE
    )

    # Handle LIKE operator with wildcards
    # column LIKE '%pattern%' -> column.str.contains('pattern', case=False)
    def replace_like(match):
        col = match.group(1).strip()
        pattern = match.group(2).strip().strip("'\"")
        # Convert SAS wildcards to regex
        if pattern.startswith('%') and pattern.endswith('%'):
            # Contains
            pattern = pattern[1:-1]
            return f"{col}.str.contains('{pattern}', case=False, na=False)"
        elif pattern.startswith('%'):
            # Ends with
            pattern = pattern[1:]
            return f"{col}.str.endswith('{pattern}', na=False)"
        elif pattern.endswith('%'):
            # Starts with
            pattern = pattern[:-1]
            return f"{col}.str.startswith('{pattern}', na=False)"
        else:
            # Exact match
            return f"{col} == '{pattern}'"
    
    result = regex_module.sub(
        r"(\w+)\s+LIKE\s+['\"]([^'\"]+)['\"]",
        replace_like,
        result,
        flags=regex_module.IGNORECASE
    )
    
    # Replace logical operators - case insensitive
    result = regex_module.sub(r'\bAND\b', '&', result, flags=regex_module.IGNORECASE)
    result = regex_module.sub(r'\bOR\b', '|', result, flags=regex_module.IGNORECASE)
    result = regex_module.sub(r'\bNOT\s+', '~', result, flags=regex_module.IGNORECASE)
    
    # Replace single quotes with double quotes for string values (pandas prefers double)
    # But be careful not to replace quotes inside strings
    result = result.replace("'", '"')
    
    return result


def translate_r_to_pandas(expression: str, df: pd.DataFrame) -> str:
    """
    Translate R dplyr filter syntax to pandas query syntax.
    
    R operators supported:
    - &, | work directly
    - == , !=, >, <, >=, <= work directly
    - %in% -> .isin([...])
    - is.na() -> .isna()
    - !is.na() -> .notna()
    - c(...) -> [...]
    - str_detect() -> .str.contains()
    """
    if not expression or not expression.strip():
        return ""
    
    result = expression.strip()
    
    # Handle !is.na(column) -> column.notna()
    result = regex_module.sub(
        r'!is\.na\((\w+)\)',
        r'\1.notna()',
        result
    )
    
    # Handle is.na(column) -> column.isna()
    result = regex_module.sub(
        r'is\.na\((\w+)\)',
        r'\1.isna()',
        result
    )
    
    # Handle %in% with c() - column %in% c("val1", "val2")
    def replace_in(match):
        col = match.group(1).strip()
        values = match.group(2).strip()
        return f"{col}.isin([{values}])"
    
    result = regex_module.sub(
        r'(\w+)\s+%in%\s+c\(([^)]+)\)',
        replace_in,
        result
    )
    
    # Handle negated str_detect
    result = regex_module.sub(
        r'!str_detect\((\w+),\s*([^)]+)\)',
        lambda m: f"~{m.group(1)}.str.contains({m.group(2)}, case=False, na=False)",
        result
    )

    # Handle str_detect(column, "pattern") -> column.str.contains("pattern", na=False)
    def replace_str_detect(match):
        col = match.group(1).strip()
        pattern = match.group(2).strip()
        return f"{col}.str.contains({pattern}, case=False, na=False)"
    
    result = regex_module.sub(
        r'str_detect\((\w+),\s*([^)]+)\)',
        replace_str_detect,
        result
    )
    
    # R uses TRUE/FALSE, pandas uses True/False
    result = regex_module.sub(r'\bTRUE\b', 'True', result)
    result = regex_module.sub(r'\bFALSE\b', 'False', result)
    
    return result


def translate_python_to_pandas(expression: str, df: pd.DataFrame) -> str:
    """
    Python/pandas expressions are mostly used directly.
    Just validate and clean up the expression.
    """
    if not expression or not expression.strip():
        return ""
    
    return expression.strip()


def validate_expression_columns(expression: str, df: pd.DataFrame, syntax: str = 'python') -> List[str]:
    """
    Extract and validate column names referenced in the expression.
    Returns list of unknown columns.
    
    Args:
        expression: The filter expression
        df: The dataframe to validate against
        syntax: The syntax type ('sas', 'r', 'python') to know which keywords to exclude
    """
    # Python/pandas keywords and functions
    python_keywords = {
        'and', 'or', 'not', 'in', 'is', 'true', 'false', 'none',
        'isin', 'isna', 'notna', 'str', 'contains', 'startswith', 'endswith',
        'case', 'na', 'regex'
    }
    
    # SAS WHERE clause keywords (case-insensitive)
    sas_keywords = {
        'and', 'or', 'not', 'in', 'is', 'missing', 'like',
        'eq', 'ne', 'gt', 'lt', 'ge', 'le', 'between'
    }
    
    # R dplyr keywords
    r_keywords = {
        'true', 'false', 'na', 'c', 'is', 'str_detect'
    }
    
    # Combine keywords based on syntax
    all_keywords = python_keywords.copy()
    if syntax.lower() == 'sas':
        all_keywords.update(sas_keywords)
    elif syntax.lower() == 'r':
        all_keywords.update(r_keywords)
    
    # First, remove all quoted strings so we don't mistake string values for column names
    # Remove single-quoted strings: 'value'
    expr_no_strings = regex_module.sub(r"'[^']*'", '', expression)
    # Remove double-quoted strings: "value"
    expr_no_strings = regex_module.sub(r'"[^"]*"', '', expr_no_strings)
    
    # Find all word tokens (only from the expression with strings removed)
    tokens = regex_module.findall(r'\b([A-Za-z_][A-Za-z0-9_]*)\b', expr_no_strings)
    
    # Filter to potential column names (case-insensitive keyword check)
    potential_columns = set()
    for token in tokens:
        if token.lower() not in all_keywords:
            potential_columns.add(token)
    
    # Check which ones don't exist
    unknown = []
    for col in potential_columns:
        # Check case-insensitive match against actual columns
        if col not in df.columns:
            # Try to find similar column names (case-insensitive)
            similar = [c for c in df.columns if c.upper() == col.upper()]
            if not similar:
                unknown.append(col)
    
    return unknown


def normalize_column_names_in_expression(expression: str, df: pd.DataFrame) -> str:
    """
    Normalize column names in the expression to match the actual case in the dataframe.
    This allows users to type WEEK when the actual column is 'week'.
    """
    # Build a case-insensitive lookup: lowercase -> actual column name
    column_lookup = {col.lower(): col for col in df.columns}
    
    # Find all word tokens that could be column names
    # We need to be careful to only replace whole words, not parts of strings
    
    def replace_column(match):
        token = match.group(0)
        # Check if this token (case-insensitive) matches a column
        lower_token = token.lower()
        if lower_token in column_lookup:
            actual_col = column_lookup[lower_token]
            # Only replace if the case is different
            if token != actual_col:
                return actual_col
        return token
    
    # Replace column names outside of quoted strings
    # First, protect quoted strings by replacing them with placeholders
    import re
    
    # Find all quoted strings and replace with placeholders
    single_quoted = re.findall(r"'[^']*'", expression)
    double_quoted = re.findall(r'"[^"]*"', expression)
    
    # Replace quotes with placeholders
    placeholder_map = {}
    result = expression
    for i, sq in enumerate(single_quoted):
        placeholder = f"__SQ_PLACEHOLDER_{i}__"
        placeholder_map[placeholder] = sq
        result = result.replace(sq, placeholder, 1)
    for i, dq in enumerate(double_quoted):
        placeholder = f"__DQ_PLACEHOLDER_{i}__"
        placeholder_map[placeholder] = dq
        result = result.replace(dq, placeholder, 1)
    
    # Now replace column names (word boundaries)
    result = re.sub(r'\b([A-Za-z_][A-Za-z0-9_]*)\b', replace_column, result)
    
    # Restore quoted strings
    for placeholder, original in placeholder_map.items():
        result = result.replace(placeholder, original)
    
    return result


def apply_expression_filter(df: pd.DataFrame, expression: str, syntax: str) -> pd.DataFrame:
    """
    Apply an expression filter to the dataframe.
    Returns the filtered dataframe.
    """
    if not expression or not expression.strip():
        return df
    
    # Normalize column names to match actual case in dataframe
    expression = normalize_column_names_in_expression(expression, df)
    
    # Translate expression based on syntax
    if syntax.lower() == 'sas':
        pandas_expr = translate_sas_to_pandas(expression, df)
    elif syntax.lower() == 'r':
        pandas_expr = translate_r_to_pandas(expression, df)
    else:  # python/pandas
        pandas_expr = translate_python_to_pandas(expression, df)
    
    if not pandas_expr:
        return df
    
    logger.info(f"Translated expression ({syntax}): {expression} -> {pandas_expr}")
    
    # Try using pandas query() first (safer, more restricted)
    try:
        # For expressions with method calls like .isin(), .isna(), we need eval()
        if '.isin(' in pandas_expr or '.isna(' in pandas_expr or '.notna(' in pandas_expr or '.str.' in pandas_expr:
            # Use eval with the dataframe's column namespace
            mask = df.eval(pandas_expr)
            return df[mask]
        else:
            # Use query for simple comparisons
            return df.query(pandas_expr)
    except Exception as e:
        logger.error(f"Error applying expression filter: {e}")
        raise HTTPException(
            status_code=400,
            detail=f"Invalid expression: {str(e)}. Translated expression was: {pandas_expr}"
        )
