"""Public input/output models for the Planning MCP."""

import math
from datetime import date, datetime, timezone
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

__all__ = (
    "PlanningAlgorithm",
    "PlanningObjective",
    "ScheduleProjectRequest",
    "ContractorSize",
    "ResourceModelKind",
    "StructureItem",
    "Edge",
    "EnrichedUnit",
    "PlanningRow",
    "PlanningEdge",
    "ResourceAssignment",
    "PlanningWarning",
    "ScheduledWork",
    "ScheduleResult",
)

PlanningAlgorithm: TypeAlias = Literal["topological", "randomized_topological", "heft", "heft_between", "genetic"]
PlanningObjective: TypeAlias = Literal["min_time", "deadline", "resource_optimization"]
ContractorSize: TypeAlias = Literal["min", "mid", "max"]
ResourceModelKind: TypeAlias = Literal["standard", "historical"]
StructureItem: TypeAlias = tuple[str, str, StrictInt, StrictInt]
Edge: TypeAlias = tuple[str, Literal["FS", "SS", "FF", "SF"], StrictInt]


def _require_non_blank(value: str) -> str:
    """Reject blank strings, preserving original whitespace."""
    if not value.strip():
        raise ValueError("must be non-empty")
    return value


def _reject_bool(value: Any) -> Any:
    """Reject bool before Pydantic coercion."""
    if isinstance(value, bool):
        raise ValueError("must not be a boolean")
    return value


def _require_finite(value: Any) -> Any:
    """Require finite number, passing other types through for Pydantic."""
    if isinstance(value, bool):
        raise ValueError("must not be a boolean")
    if isinstance(value, (int, float)) and not math.isfinite(value):
        raise ValueError("must be finite")
    return value


class EnrichedUnit(BaseModel):
    """Optional semantic enrichment trace."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str | None = None
    name: str | None = None
    measurement: str | None = None
    category: str | None = None
    distance: float | None = None
    rank: int | None = None

    @field_validator("distance", mode="before")
    @classmethod
    def _validate_distance(cls, value: Any) -> Any:
        """Reject bool and non-finite distance."""
        if value is None:
            return None
        _reject_bool(value)
        _require_finite(value)
        return value

    @field_validator("rank", mode="before")
    @classmethod
    def _validate_rank(cls, value: Any) -> Any:
        """Reject bool rank before coercion."""
        if value is None:
            return None
        _reject_bool(value)
        return value


class PlanningRow(BaseModel):
    """Single planning input row, structurally compatible with CSV CsvRow."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    activity_id: str
    activity_name: str
    volume: float
    measurement: str
    structure: list[StructureItem] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    enriched: EnrichedUnit | None = None

    @field_validator("activity_id", "activity_name", "measurement")
    @classmethod
    def _validate_non_blank(cls, value: str) -> str:
        """Reject blank strings."""
        return _require_non_blank(value)

    @field_validator("volume", mode="before")
    @classmethod
    def _validate_volume(cls, value: Any) -> Any:
        """Reject bool and non-finite/negative volume."""
        _reject_bool(value)
        _require_finite(value)
        if isinstance(value, (int, float)) and value < 0:
            raise ValueError("must be >= 0")
        return value


class PlanningEdge(BaseModel):
    """Normalized internal edge after validation, before graph build."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    predecessor_id: str
    successor_id: str
    connection_type: Literal["FS", "SS", "FF"]
    lag: int

    @field_validator("predecessor_id", "successor_id")
    @classmethod
    def _validate_non_blank(cls, value: str) -> str:
        """Reject blank strings."""
        return _require_non_blank(value)

    @field_validator("lag", mode="before")
    @classmethod
    def _validate_lag(cls, value: Any) -> Any:
        """Reject bool lag before coercion."""
        _reject_bool(value)
        return value


class ResourceAssignment(BaseModel):
    """Resource allocation for a scheduled work item."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    count: int

    @field_validator("name")
    @classmethod
    def _validate_non_blank(cls, value: str) -> str:
        """Reject blank strings."""
        return _require_non_blank(value)

    @field_validator("count", mode="before")
    @classmethod
    def _validate_count(cls, value: Any) -> Any:
        """Reject bool, non-finite, and negative count before coercion."""
        _reject_bool(value)
        value = _require_finite(value)
        if isinstance(value, (int, float)) and value < 0:
            raise ValueError("must be >= 0")
        return value


class PlanningWarning(BaseModel):
    """Non-fatal planning warning with a safe message."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    activity_id: str | None = None
    field: str | None = None
    message: str

    @field_validator("code", "message")
    @classmethod
    def _validate_non_blank(cls, value: str) -> str:
        """Reject blank strings."""
        return _require_non_blank(value)


class ScheduledWork(BaseModel):
    """Single scheduled work item."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str
    start: datetime
    finish: datetime
    duration_days: float
    resources: list[ResourceAssignment] = Field(default_factory=list)
    contractor: str | None = None

    @field_validator("id", "name")
    @classmethod
    def _validate_non_blank(cls, value: str) -> str:
        """Reject blank strings."""
        return _require_non_blank(value)

    @field_validator("start", "finish")
    @classmethod
    def _ensure_tz_aware(cls, value: datetime) -> datetime:
        """Attach UTC to naive datetimes for valid date-time output."""
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @field_validator("duration_days", mode="before")
    @classmethod
    def _validate_duration(cls, value: Any) -> Any:
        """Reject bool, non-finite, and negative duration."""
        _reject_bool(value)
        _require_finite(value)
        if isinstance(value, (int, float)) and value < 0:
            raise ValueError("must be >= 0")
        return value


class ScheduleResult(BaseModel):
    """Stable planning result."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_name: str
    project_start_date: date
    algorithm: PlanningAlgorithm
    optimization_objective: PlanningObjective
    execution_time: float
    works: list[ScheduledWork] = Field(default_factory=list)
    edges: list[PlanningEdge] = Field(default_factory=list)
    warnings: list[PlanningWarning] = Field(default_factory=list)
    sampo_dump: dict | None = None

    @field_validator("project_name")
    @classmethod
    def _validate_non_blank(cls, value: str) -> str:
        """Reject blank strings."""
        return _require_non_blank(value)

    @field_validator("execution_time", mode="before")
    @classmethod
    def _validate_execution_time(cls, value: Any) -> Any:
        """Reject bool, non-finite, and negative execution time."""
        _reject_bool(value)
        _require_finite(value)
        if isinstance(value, (int, float)) and value < 0:
            raise ValueError("must be >= 0")
        return value


class ScheduleProjectRequest(BaseModel):
    """Public schedule_project request over a CSV URL (frozen)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    csv_url: str
    algorithm: PlanningAlgorithm
    optimization_objective: PlanningObjective
    genetic_generations: int | None = Field(default=None, ge=1, le=200)
    deadline: date | None = None

    @field_validator("csv_url")
    @classmethod
    def _validate_csv_url(cls, value: str) -> str:
        """Reject blank URLs and non-https/s3 schemes lexically."""
        _require_non_blank(value)
        cleaned = value.strip().lower()
        if not cleaned.startswith(("https://", "s3://")):
            raise ValueError("must use https:// or s3:// scheme")
        return value

    @field_validator("genetic_generations", mode="before")
    @classmethod
    def _validate_generations(cls, value: Any) -> Any:
        """Reject bool generations before coercion."""
        if value is None:
            return None
        return _reject_bool(value)

    @model_validator(mode="after")
    def _validate_combinations(self) -> "ScheduleProjectRequest":
        """Enforce genetic-only generations and deadline rules."""
        if self.genetic_generations is not None and self.algorithm != "genetic":
            raise ValueError("genetic_generations requires algorithm='genetic'")
        if self.optimization_objective == "deadline":
            if self.algorithm != "genetic":
                raise ValueError("objective 'deadline' requires algorithm='genetic'")
            if self.deadline is None:
                raise ValueError("deadline is required with objective='deadline'")
        elif self.deadline is not None:
            raise ValueError("deadline requires genetic algorithm with 'deadline'")
        return self
