"""Thin FastMCP transport wrapper for the Planning MCP.

The planning domain is exposed by the planning_adapter package. This module
owns only transport binding, request-boundary validation, and safe error
mapping. It does not import SAMPO, resource models, storage, graph, or
scheduling code.
"""

import argparse
import logging
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import BoundedSemaphore
from datetime import date

from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError

from planning_adapter import (
    E_INTERNAL,
    E_OPTIONS_INVALID,
    E_SCHEDULING_FAILED,
    E_URL_INVALID,
    PlanningAlgorithm,
    PlanningObjective,
    ScheduleProjectRequest,
    ScheduleResult,
    schedule_project as _schedule_project,
)

logger = logging.getLogger(__name__)

__all__ = (
    "mcp",
    "main",
    "schedule_project",
)

HOST = "0.0.0.0"
try:
    PORT = int(os.environ.get("PORT", "8080"))
except ValueError:
    PORT = 8080
if not 1 <= PORT <= 65535:
    PORT = 8080
MCP_PATH = "/mcp"
SERVER_NAME = "planning"

mcp = FastMCP(SERVER_NAME, host=HOST, port=PORT)


def _bounded_env_int(name: str, default: int, maximum: int) -> int:
    """Read a positive int env var clamped to [1, maximum] or the default."""
    try:
        return max(1, min(int(os.environ.get(name, str(default))), maximum))
    except ValueError:
        return default


_MAX_CONCURRENT = _bounded_env_int("PLANNING_MAX_CONCURRENT", 2, 16)
_DEADLINE_SECONDS = _bounded_env_int("PLANNING_DEADLINE_SECONDS", 300, 3600)
_ADMISSION = BoundedSemaphore(_MAX_CONCURRENT)
_EXECUTOR = ThreadPoolExecutor(max_workers=_MAX_CONCURRENT, thread_name_prefix="planning")


@mcp.tool(name="schedule_project")
def schedule_project(
    csv_url: str,
    algorithm: PlanningAlgorithm,
    optimization_objective: PlanningObjective,
    genetic_generations: int | None = None,
    deadline: date | None = None,
) -> ScheduleResult:
    """Schedule an oil&gas field development project via the planning adapter.

    Intended only for construction scheduling of oil&gas field development;
    nothing else.
    """
    try:
        request = ScheduleProjectRequest(
            csv_url=csv_url,
            algorithm=algorithm,
            optimization_objective=optimization_objective,
            genetic_generations=genetic_generations,
            deadline=deadline,
        )
    except ValidationError as exc:
        try:
            errors = exc.errors()
            loc = errors[0].get("loc", ()) if errors else ()
            field = loc[0] if loc else None
        except Exception:
            raise ValueError(E_OPTIONS_INVALID) from None
        if field == "csv_url":
            raise ValueError(E_URL_INVALID) from None
        raise ValueError(E_OPTIONS_INVALID) from None
    except ValueError:
        raise ValueError(E_OPTIONS_INVALID) from None
    except Exception:
        raise RuntimeError(E_INTERNAL) from None

    try:
        logger.info(
            "[PLANNING_MCP][TOOL] tool=schedule_project algorithm=%s objective=%s -- start",
            request.algorithm,
            request.optimization_objective,
        )
        if not _ADMISSION.acquire(blocking=False):
            raise RuntimeError(E_SCHEDULING_FAILED) from None
        future = _EXECUTOR.submit(_schedule_project, request)
        release_on_exit = False
        try:
            try:
                result = future.result(timeout=_DEADLINE_SECONDS)
            except TimeoutError:
                if not future.cancel():
                    future.add_done_callback(lambda _: _ADMISSION.release())
                    release_on_exit = True
                raise RuntimeError(E_SCHEDULING_FAILED) from None
        finally:
            if not release_on_exit:
                _ADMISSION.release()
        logger.info("[PLANNING_MCP][TOOL] tool=schedule_project -- finished")
        return result
    except ValueError:
        raise
    except RuntimeError:
        raise
    except Exception:
        raise RuntimeError(E_INTERNAL) from None


def main(argv: list[str] | None = None) -> None:
    """Run the server on http://0.0.0.0:8080/mcp via streamable-http."""
    parser = argparse.ArgumentParser(prog="planning_mcp")
    parser.add_argument(
        "--transport",
        default="streamable-http",
        choices=("streamable-http",),
        help="MCP transport (AppFactory mode: streamable-http)",
    )
    parser.parse_args(argv)
    logger.info(
        "[PLANNING_MCP][SERVE] host=%s port=%d path=%s -- starting",
        HOST,
        PORT,
        MCP_PATH,
    )
    mcp.run(transport="streamable-http")
