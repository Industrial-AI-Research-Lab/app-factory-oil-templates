from typing import TypedDict


class ResourcesCalculated(TypedDict):
    kind: str
    volume: int
    min_count: int
    max_count: int


class WorkUnit(TypedDict):
    name: str
    volume: float
    measurement: str


class WorkerReqs(TypedDict):
    worker_reqs: list[ResourcesCalculated]


class ResourceDict(TypedDict):
    name: str
    _count: int
