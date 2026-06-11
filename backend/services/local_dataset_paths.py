"""Filesystem path helpers for repo-local datasets/ development data."""

from pathlib import Path


def get_datasets_folder() -> Path:
    """Return the repo-root datasets/ directory used for local development."""
    return Path(__file__).resolve().parents[2] / "datasets"


def resolve_local_dataset_path(file_path: str) -> Path:
    """Resolve a user-facing dataset name to a path under the repo datasets/ folder."""
    dataset_path = Path(file_path)
    if dataset_path.exists():
        return dataset_path.resolve()

    datasets_folder = get_datasets_folder()
    if not dataset_path.is_absolute():
        datasets_candidate = datasets_folder / dataset_path
        if datasets_candidate.exists():
            return datasets_candidate.resolve()

    return dataset_path if dataset_path.is_absolute() else datasets_folder / dataset_path
