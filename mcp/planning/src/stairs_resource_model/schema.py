"""Structural aliases retained for the resource adapter."""

from typing import Any, TypeAlias

ResourceDict: TypeAlias = dict[str, Any]
WorkerReqs: TypeAlias = dict[str, list[dict[str, Any]]]
WorkUnit: TypeAlias = dict[str, Any]
