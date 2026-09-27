"""Compatibility export for the unchanged resource-model source package.

Runtime code imports ``stairs_resource_model`` directly. This module exists
only for callers that still use the old MCP-local import path.
"""

from stairs_resource_model.res_time_model import ResTimeModel
from stairs_resource_model.schema import ResourceDict, WorkerReqs, WorkUnit

__all__ = ("ResTimeModel", "ResourceDict", "WorkerReqs", "WorkUnit")
