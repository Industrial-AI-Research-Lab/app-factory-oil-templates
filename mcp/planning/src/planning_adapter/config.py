"""Static configuration constants for the Planning MCP.

This module holds immutable, import-side-effect-free constants:
transport binding, bounds, defaults, allowed literals, contractor scaler,
pipeline design flags, objective mapping, error codes, resource adapter
setting name, and independent bounded HTTPS/S3 download policy.

No scheduling, estimation, services, tools, storage, I/O, environment reads,
or framework objects live here.
"""

from typing import Final

__all__ = (
    "PLANNING_MAX_ROWS",
    "PLANNING_MAX_EDGES_PER_ROW",
    "PLANNING_MAX_STRUCTURE_ITEMS",
    "MIN_GENETIC_GENERATIONS",
    "MAX_GENETIC_GENERATIONS",
    "DEFAULT_ALGORITHM",
    "DEFAULT_OBJECTIVE",
    "DEFAULT_PROJECT_NAME",
    "DEFAULT_CONTRACTOR_SIZE",
    "DEFAULT_RESOURCE_MODEL",
    "DEFAULT_INCLUDE_SAMPO_DUMP",
    "DEFAULT_GENETIC_GENERATIONS",
    "DEFAULT_DEADLINE",
    "ALLOWED_ALGORITHMS",
    "ALLOWED_OBJECTIVES",
    "ALLOWED_CONTRACTOR_SIZES",
    "ALLOWED_RESOURCE_MODELS",
    "ALLOWED_INPUT_CONNECTION_TYPES",
    "ALLOWED_NORMALIZED_CONNECTION_TYPES",
    "CONTRACTOR_SCALERS",
    "RESTORE_CONNECTIONS_DEFAULT",
    "REFILL_RESOURCE_REQUIREMENTS_DEFAULT",
    "RESTRUCTURE_PARALLEL_DEFAULT",
    "ALL_CONNECTIONS",
    "ELIMINATE_RESOURCE_PEAKS_BY_OBJECTIVE",
    "ELIMINATE_RESOURCE_PEAKS_NON_GENETIC_DEFAULT",
    "RM_ADAPTER_CONN_STR_ENV_NAME",
    "PLANNING_ALLOWED_URL_SCHEMES",
    "PLANNING_CSV_ENCODINGS",
    "PLANNING_MAX_DOWNLOAD_BYTES",
    "PLANNING_FETCH_TIMEOUT_SECONDS",
    "PLANNING_MAX_REDIRECTS",
    "PLANNING_CSV_ALLOWED_HOSTS",
    "PLANNING_CSV_ALLOWED_S3_BUCKETS",
    "E_EMPTY_ROWS",
    "E_LIMIT_ROWS",
    "E_LIMIT_EDGES",
    "E_LIMIT_STRUCTURE",
    "E_DUPLICATE_ID",
    "E_UNKNOWN_PREDECESSOR",
    "E_CYCLE_DETECTED",
    "E_EDGE_INVALID",
    "E_VOLUME_INVALID",
    "E_MEASUREMENT_MISSING",
    "E_ENRICHED_INVALID",
    "E_RESOURCE_MODEL_MISSING",
    "E_RESOURCE_REQUIREMENTS_INVALID",
    "E_NO_CONTRACTORS",
    "E_SCHEDULING_FAILED",
    "E_OPTIONS_INVALID",
    "E_DEADLINE_INVALID",
    "E_INTERNAL",
    "E_URL_INVALID",
    "E_URL_FORBIDDEN",
    "E_DOWNLOAD_FAILED",
    "E_ENCODING_UNSUPPORTED",
    "E_CSV_INVALID",
    "ERROR_CODES",
)

# Bounds
PLANNING_MAX_ROWS: Final[int] = 200
PLANNING_MAX_EDGES_PER_ROW: Final[int] = 16
PLANNING_MAX_STRUCTURE_ITEMS: Final[int] = 16
MIN_GENETIC_GENERATIONS: Final[int] = 1
MAX_GENETIC_GENERATIONS: Final[int] = 200

# Tool defaults
DEFAULT_ALGORITHM: Final[str] = "heft"
DEFAULT_OBJECTIVE: Final[str] = "min_time"
DEFAULT_PROJECT_NAME: Final[str] = "planning-project"
DEFAULT_CONTRACTOR_SIZE: Final[str] = "mid"
DEFAULT_RESOURCE_MODEL: Final[str] = "standard"
DEFAULT_INCLUDE_SAMPO_DUMP: Final[bool] = False
DEFAULT_GENETIC_GENERATIONS: Final[None] = None
DEFAULT_DEADLINE: Final[None] = None

# Allowed literals
ALLOWED_ALGORITHMS: Final[frozenset[str]] = frozenset(
    {
        "topological",
        "randomized_topological",
        "heft",
        "heft_between",
        "genetic",
    }
)
ALLOWED_OBJECTIVES: Final[frozenset[str]] = frozenset(
    {
        "min_time",
        "deadline",
        "resource_optimization",
    }
)
ALLOWED_CONTRACTOR_SIZES: Final[frozenset[str]] = frozenset(
    {
        "min",
        "mid",
        "max",
    }
)
ALLOWED_RESOURCE_MODELS: Final[frozenset[str]] = frozenset(
    {
        "standard",
        "historical",
    }
)
ALLOWED_INPUT_CONNECTION_TYPES: Final[frozenset[str]] = frozenset(
    {
        "FS",
        "SS",
        "FF",
        "SF",
    }
)
ALLOWED_NORMALIZED_CONNECTION_TYPES: Final[frozenset[str]] = frozenset(
    {
        "FS",
        "SS",
        "FF",
    }
)

# Mirrors backend _SIZES_TO_FACTOR (min=1, mid=5, max=10).
CONTRACTOR_SCALERS: Final[dict[str, int]] = {
    "min": 1,
    "mid": 5,
    "max": 10,
}

# ALL_CONNECTIONS mirrors the backend; the rest replace per-project
# user settings the tool cannot ask for.
RESTORE_CONNECTIONS_DEFAULT: Final[bool] = False
REFILL_RESOURCE_REQUIREMENTS_DEFAULT: Final[bool] = True
RESTRUCTURE_PARALLEL_DEFAULT: Final[bool] = True
ALL_CONNECTIONS: Final[bool] = True

# Objective-derived peak elimination: explicit design mapping.
ELIMINATE_RESOURCE_PEAKS_BY_OBJECTIVE: Final[dict[str, bool]] = {
    "min_time": False,
    "deadline": False,
    "resource_optimization": True,
}
ELIMINATE_RESOURCE_PEAKS_NON_GENETIC_DEFAULT: Final[bool] = False

# Resource adapter setting name only: never read or log the value.
RM_ADAPTER_CONN_STR_ENV_NAME: Final[str] = "RM_ADAPTER_CONN_STR"

# Exact closed error code set
E_EMPTY_ROWS: Final[str] = "E_EMPTY_ROWS"
E_LIMIT_ROWS: Final[str] = "E_LIMIT_ROWS"
E_LIMIT_EDGES: Final[str] = "E_LIMIT_EDGES"
E_LIMIT_STRUCTURE: Final[str] = "E_LIMIT_STRUCTURE"
E_DUPLICATE_ID: Final[str] = "E_DUPLICATE_ID"
E_UNKNOWN_PREDECESSOR: Final[str] = "E_UNKNOWN_PREDECESSOR"
E_CYCLE_DETECTED: Final[str] = "E_CYCLE_DETECTED"
E_EDGE_INVALID: Final[str] = "E_EDGE_INVALID"
E_VOLUME_INVALID: Final[str] = "E_VOLUME_INVALID"
E_MEASUREMENT_MISSING: Final[str] = "E_MEASUREMENT_MISSING"
E_ENRICHED_INVALID: Final[str] = "E_ENRICHED_INVALID"
E_RESOURCE_MODEL_MISSING: Final[str] = "E_RESOURCE_MODEL_MISSING"
E_RESOURCE_REQUIREMENTS_INVALID: Final[str] = "E_RESOURCE_REQUIREMENTS_INVALID"
E_NO_CONTRACTORS: Final[str] = "E_NO_CONTRACTORS"
E_SCHEDULING_FAILED: Final[str] = "E_SCHEDULING_FAILED"
E_OPTIONS_INVALID: Final[str] = "E_OPTIONS_INVALID"
E_DEADLINE_INVALID: Final[str] = "E_DEADLINE_INVALID"
E_INTERNAL: Final[str] = "E_INTERNAL"

# Independent csv_url boundary codes
E_URL_INVALID: Final[str] = "E_URL_INVALID"
E_URL_FORBIDDEN: Final[str] = "E_URL_FORBIDDEN"
E_DOWNLOAD_FAILED: Final[str] = "E_DOWNLOAD_FAILED"
E_ENCODING_UNSUPPORTED: Final[str] = "E_ENCODING_UNSUPPORTED"
E_CSV_INVALID: Final[str] = "E_CSV_INVALID"

ERROR_CODES: Final[tuple[str, ...]] = (
    E_EMPTY_ROWS,
    E_LIMIT_ROWS,
    E_LIMIT_EDGES,
    E_LIMIT_STRUCTURE,
    E_DUPLICATE_ID,
    E_UNKNOWN_PREDECESSOR,
    E_CYCLE_DETECTED,
    E_EDGE_INVALID,
    E_VOLUME_INVALID,
    E_MEASUREMENT_MISSING,
    E_ENRICHED_INVALID,
    E_RESOURCE_MODEL_MISSING,
    E_RESOURCE_REQUIREMENTS_INVALID,
    E_NO_CONTRACTORS,
    E_SCHEDULING_FAILED,
    E_OPTIONS_INVALID,
    E_DEADLINE_INVALID,
    E_INTERNAL,
    E_URL_INVALID,
    E_URL_FORBIDDEN,
    E_DOWNLOAD_FAILED,
    E_ENCODING_UNSUPPORTED,
    E_CSV_INVALID,
)

# Independent csv_url policy
# Conservative defaults mirroring CSV download bounds; enforced by source_loader.
PLANNING_ALLOWED_URL_SCHEMES: Final[tuple[str, ...]] = ("https", "s3")
PLANNING_CSV_ENCODINGS: Final[tuple[str, ...]] = ("utf-8-sig", "cp1251")
PLANNING_MAX_DOWNLOAD_BYTES: Final[int] = 10_000_000
PLANNING_FETCH_TIMEOUT_SECONDS: Final[int] = 15
PLANNING_MAX_REDIRECTS: Final[int] = 3

# Environment variable names only (no reads, no values, no I/O).
PLANNING_CSV_ALLOWED_HOSTS: Final[str] = "PLANNING_CSV_ALLOWED_HOSTS"
PLANNING_CSV_ALLOWED_S3_BUCKETS: Final[str] = "PLANNING_CSV_ALLOWED_S3_BUCKETS"
