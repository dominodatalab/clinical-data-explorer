"""Column-label loader.

Reads the friendly-name lookup file (`column_labels_simple.csv`) produced
by the `extract_column_labels` workflow and returns it as a
`{column_name: label}` dict. The file is optional; callers must handle
the missing-file case (returns `None`).

Extracted from the `/column_labels` route handler in `backend/app.py`
per REFACTOR_PLAN.md §1, step 1.4. The route stays in `backend/app.py`
until step 1.5d (P5); it now just calls into this loader and shapes the
JSON response.
"""
import csv
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# The lookup file is project-relative — it lives next to the running app
# (extract_column_labels writes it into the project root).
_COLUMN_LABELS_PATH = Path('column_labels_simple.csv')


def load_column_labels():
    """Load column labels from column_labels_simple.csv if present.

    Returns a `{column_name: label}` dict, or `None` if the lookup file
    does not exist. Raises on parse/IO errors so callers can surface them
    in their own error envelope (matches pre-refactor behavior of the
    `/column_labels` route, which returned `available: False` on either
    missing-file OR exception, with `error` populated only in the
    exception branch).
    """
    if not _COLUMN_LABELS_PATH.exists():
        return None
    labels = {}
    with open(_COLUMN_LABELS_PATH, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            labels[row['column_name']] = row['label']
    return labels
