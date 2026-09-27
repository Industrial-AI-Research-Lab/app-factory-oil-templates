"""Safe Planning MCP resource model runtime.

Primary lookup is this package (JSON evaluator). The vendored upstream
snapshot under ``sources/stairs-resource-model`` stays on ``__path__`` as a
fallback so legacy pickle blobs from the backend-populated model DB can still
resolve their estimator classes (``stairs_resource_model.models.*``).
"""

import os

from .res_time_model import ResTimeModel

__all__ = ("ResTimeModel",)

_HERE = os.path.dirname(os.path.abspath(__file__))
_FALLBACK = os.path.normpath(os.path.join(_HERE, "..", "..", "sources", "stairs-resource-model", "stairs_resource_model"))
if os.path.isdir(_FALLBACK) and _FALLBACK not in __path__:
    __path__.append(_FALLBACK)
