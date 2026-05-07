import logging
import os

logger = logging.getLogger(__name__)

ONE_MB = 1024 * 1024
DATA_TO_DATAFRAME_SIZE_MULTIPLIER = 5

DATA_FILE_SIZE_LIMIT = int(os.environ.get('DATA_FILE_SIZE_LIMIT_B', 500 * ONE_MB))


class DataFileTooLarge(RuntimeError):
    """Raised when a dataset or netapp file is too large to load
    either because there is not enough space in the container or the file exceeds the
    maximum processable size."""

    pass


def enforce(file_name: str, file_size: int):
    """
    This raises if the size of the file is larger than the limits
    And also verifies that the pandas dataframe can fit in memory
    """

    if file_size > DATA_FILE_SIZE_LIMIT:
        raise DataFileTooLarge(
            f'{file_name} must be less than or equal to {DATA_FILE_SIZE_LIMIT} bytes to be processable'
        )

    # this is the estimated size that the dataframe will be when it's created
    estimated_df_size_b = file_size * DATA_TO_DATAFRAME_SIZE_MULTIPLIER

    # There is a danger with these memory estimators that k8s may change its implementation of
    # how memory limits are implemented. This currently works. These will return None if run locally
    used_b = _get_container_memory_usage_bytes()
    limit_b = _get_container_memory_limit_bytes()
    if used_b is None:
        logger.warning("Couldn't get used memory estimate when validating file size limits")

    if limit_b is None:
        logger.warning("Couldn't get memory limit estimate when validating file size limits")

    if used_b is None or limit_b is None:
        return

    remaining_bytes = limit_b - used_b
    if remaining_bytes < estimated_df_size_b:
        used_mb = used_b / ONE_MB
        limit_mb = limit_b / ONE_MB
        remaining_mb = remaining_bytes / ONE_MB

        logger.debug(
            f"There's not enough space to process {file_name}. used mb: {used_mb}, limit mb: {limit_mb}. Total remaining mb: {remaining_mb}"
        )
        raise DataFileTooLarge(f"There's not enough space to process {file_name}. There's only {remaining_mb} MB remaining.")


def _get_container_memory_usage_bytes():
    # Cgroup v2 path (Standard in modern K8s)
    v2_path = "/sys/fs/cgroup/memory.current"
    # Cgroup v1 path
    v1_path = "/sys/fs/cgroup/memory/memory.usage_in_bytes"

    if os.path.exists(v2_path):
        with open(v2_path, 'r') as f:
            return int(f.read().strip())
    elif os.path.exists(v1_path):
        with open(v1_path, 'r') as f:
            return int(f.read().strip())
    return None


def _get_container_memory_limit_bytes():
    # cgroup v2 path
    v2_path = "/sys/fs/cgroup/memory.max"
    # cgroup v1 path
    v1_path = "/sys/fs/cgroup/memory/memory.limit_in_bytes"

    try:
        if os.path.exists(v2_path):
            with open(v2_path, "r") as f:
                limit = f.read().strip()
        elif os.path.exists(v1_path):
            with open(v1_path, "r") as f:
                limit = f.read().strip()
        else:
            print("Limit file not found")
            return None

        # "max" indicates no limit is set
        if limit == "max":
            return float('inf')

        return int(limit)
    except Exception as e:
        print(f"Error: {e}")
        return None
