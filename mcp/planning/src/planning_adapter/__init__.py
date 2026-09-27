"""Planning adapter connecting the local core, SAMPO, and MCP transport.

This package is the public integration boundary for the Planning MCP. It owns
request/result models, configuration constants, source loading, resource-model
adaptation, graph construction, and scheduling orchestration. It never imports
FastMCP or the transport package.
"""

from .config import (
    E_INTERNAL,
    E_OPTIONS_INVALID,
    E_SCHEDULING_FAILED,
    E_URL_INVALID,
)
from .models import (
    ContractorSize,
    Edge,
    EnrichedUnit,
    PlanningAlgorithm,
    PlanningEdge,
    PlanningObjective,
    PlanningRow,
    PlanningWarning,
    ResourceAssignment,
    ResourceModelKind,
    ScheduleProjectRequest,
    ScheduleResult,
    ScheduledWork,
    StructureItem,
)
from .service import schedule_project

__all__ = (
    "ContractorSize",
    "E_INTERNAL",
    "E_OPTIONS_INVALID",
    "E_SCHEDULING_FAILED",
    "E_URL_INVALID",
    "Edge",
    "EnrichedUnit",
    "PlanningAlgorithm",
    "PlanningEdge",
    "PlanningObjective",
    "PlanningRow",
    "PlanningWarning",
    "ResourceAssignment",
    "ResourceModelKind",
    "ScheduleProjectRequest",
    "ScheduleResult",
    "ScheduledWork",
    "StructureItem",
    "schedule_project",
)
