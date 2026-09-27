"""Canonical mapped CSV parser for Planning MCP"""

import ast
import csv
import io
import itertools
import json
import math
import re
from collections.abc import Mapping

from pydantic import ValidationError

from .config import E_CSV_INVALID, E_DUPLICATE_ID, E_EDGE_INVALID, E_EMPTY_ROWS, E_ENRICHED_INVALID, E_INTERNAL, E_LIMIT_EDGES, E_LIMIT_ROWS, E_LIMIT_STRUCTURE, E_MEASUREMENT_MISSING, E_VOLUME_INVALID, PLANNING_MAX_EDGES_PER_ROW, PLANNING_MAX_ROWS, PLANNING_MAX_STRUCTURE_ITEMS
from .models import EnrichedUnit, PlanningRow
from .errors import PlanningRuntimeError, PlanningValidationError
from .source_loader import download_planning_csv

__all__ = ("load_planning_rows",)
_CANONICAL = ("activity_id", "activity_name", "volume", "measurement", "structure", "edges", "granular_unit")
_REQUIRED = frozenset({"activity_id", "activity_name", "volume", "measurement"})
_EDGE_TYPES = frozenset({"FS", "SS", "FF", "SF"})
_NUMERIC_RE = re.compile(r"^[+-]?\d+(\.\d+)?([eE][+-]?\d+)?$")
_MAX_CELL_CHARS = 16000
_MAX_NESTED_DEPTH = 3


def _fail(code: str, hint: str, row: int | None = None) -> PlanningValidationError:
    """Build safe error without raw cells or URL."""
    if row is None: return PlanningValidationError(code, hint)
    return PlanningValidationError(code, f"row {row}: {hint}")


def _check_depth(value: object, depth: int, row: int) -> None:
    """Bound nested depth to lists/tuples/dicts."""
    if isinstance(value, (dict, list, tuple)):
        if depth > _MAX_NESTED_DEPTH: raise _fail(E_CSV_INVALID, "nested value too deep", row)
        for item in value.values() if isinstance(value, dict) else value:
            _check_depth(item, depth + 1, row)
    elif isinstance(value, (str, int, float, bool)) or value is None: return
    else: raise _fail(E_CSV_INVALID, "unsupported nested value", row)


def _load_nested(text: str, row: int) -> object:
    """Load nested value via JSON first, literal_eval fallback."""
    try: value = json.loads(text)
    except Exception:
        try: value = ast.literal_eval(text)
        except Exception: raise _fail(E_CSV_INVALID, "invalid nested value", row) from None
    if not isinstance(value, (list, tuple, dict)): raise _fail(E_CSV_INVALID, "invalid nested value", row)
    _check_depth(value, 1, row)
    return value


def _score_candidate(content: str, candidate: str) -> tuple[int, int]:
    """
    Score how well a delimiter fits the content sample.

    Args:
        content: Raw CSV text.
        candidate: Delimiter to try (``;`` or ``,``).

    Returns:
        ``(required_headers_hit, stable_rows)`` over the first 6 records,
        or ``(-1, -1)`` when the candidate cannot parse the sample.
    """
    try:
        reader = csv.reader(io.StringIO(content), delimiter=candidate, strict=True)
        sample = list(itertools.islice(reader, 6))
    except csv.Error: return (-1, -1)
    if not sample: return (-1, -1)
    header = [cell.strip() for cell in sample[0]]
    required = len(_REQUIRED.intersection(header))
    stable = sum(1 for record in sample[1:] if len(record) == len(sample[0]))
    return (required, stable)


def _select_delimiter(content: str) -> str:
    """Detect only ';' or ','; tie/default/headers-only selects ';'."""
    if _score_candidate(content, ",") > _score_candidate(content, ";"): return ","
    return ";"


def _parse_structure(text: str, row: int) -> list:
    """Parse structure cell preserving order."""
    if text.strip() == "": return []
    value = _load_nested(text, row)
    if not isinstance(value, (list, tuple)): raise _fail(E_CSV_INVALID, "invalid structure value", row)
    if len(value) > PLANNING_MAX_STRUCTURE_ITEMS: raise _fail(E_LIMIT_STRUCTURE, "too many structure items", row)
    items = []
    for entry in value:
        if not isinstance(entry, (list, tuple)) or len(entry) != 4: raise _fail(E_CSV_INVALID, "invalid structure value", row)
        code, name, level, priority = entry
        if not isinstance(code, str) or not code.strip(): raise _fail(E_CSV_INVALID, "invalid structure value", row)
        if not isinstance(name, str) or not name.strip(): raise _fail(E_CSV_INVALID, "invalid structure value", row)
        if type(level) is not int or type(priority) is not int: raise _fail(E_CSV_INVALID, "invalid structure value", row)
        items.append((code, name, level, priority))
    return items


def _parse_edges(text: str, row: int) -> list:
    """Parse edges cell preserving order."""
    if text.strip() == "": return []
    value = _load_nested(text, row)
    if not isinstance(value, (list, tuple)): raise _fail(E_EDGE_INVALID, "invalid edge value", row)
    if len(value) > PLANNING_MAX_EDGES_PER_ROW: raise _fail(E_LIMIT_EDGES, "too many edges", row)
    edges = []
    for entry in value:
        if not isinstance(entry, (list, tuple)) or len(entry) != 3: raise _fail(E_EDGE_INVALID, "invalid edge value", row)
        predecessor, conn_type, lag = entry
        if not isinstance(predecessor, str) or not predecessor.strip(): raise _fail(E_EDGE_INVALID, "invalid edge value", row)
        if conn_type not in _EDGE_TYPES: raise _fail(E_EDGE_INVALID, "invalid edge value", row)
        if type(lag) is not int: raise _fail(E_EDGE_INVALID, "invalid edge value", row)
        edges.append((predecessor, conn_type, lag))
    return edges


def _parse_enriched(text: str, row: int) -> EnrichedUnit | None:
    """Map granular_unit dict/list to EnrichedUnit."""
    if text.strip() == "": return None
    value = _load_nested(text, row)
    try:
        if isinstance(value, dict):
            if set(value) - {"code", "name", "measurement", "category"}: raise _fail(E_ENRICHED_INVALID, "invalid enriched value", row)
            code, name = value.get("code"), value.get("name")
            measurement, category = value.get("measurement"), value.get("category", "")
            if category is None: category = ""
        elif isinstance(value, (list, tuple)) and len(value) == 4:
            code, name, measurement, category = value
            if category is None: category = ""
        else: raise _fail(E_ENRICHED_INVALID, "invalid enriched value", row)
        if not isinstance(code, str) or not code.strip(): raise _fail(E_ENRICHED_INVALID, "invalid enriched value", row)
        if not isinstance(name, str) or not name.strip(): raise _fail(E_ENRICHED_INVALID, "invalid enriched value", row)
        if not isinstance(measurement, str) or not measurement.strip(): raise _fail(E_ENRICHED_INVALID, "invalid enriched value", row)
        if not isinstance(category, str): raise _fail(E_ENRICHED_INVALID, "invalid enriched value", row)
        return EnrichedUnit(code=code, name=name, measurement=measurement, category=category)
    except ValueError: raise
    except Exception: raise _fail(E_ENRICHED_INVALID, "invalid enriched value", row) from None


def load_planning_rows(csv_url: object, *, environ: Mapping[str, str] | None = None, http_client: object | None = None, s3_client: object | None = None) -> list[PlanningRow]:
    """Fetch csv_url via source loader and map canonical CSV to PlanningRow."""
    try:
        try: content = download_planning_csv(csv_url, environ=environ, http_client=http_client, s3_client=s3_client).content
        except ValueError: raise
        except RuntimeError: raise
        except Exception: raise RuntimeError(E_INTERNAL) from None
        if not isinstance(content, str): raise RuntimeError(E_INTERNAL) from None
        if content.startswith("\ufeff"): content = content[1:]
        if content.strip() == "": raise _fail(E_EMPTY_ROWS, "empty rows")
        dialect: str = _select_delimiter(content)
        try: records = list(csv.reader(io.StringIO(content), delimiter=dialect, strict=True))
        except csv.Error: raise _fail(E_CSV_INVALID, "malformed csv record") from None
        except Exception: raise RuntimeError(E_INTERNAL) from None
        if not records: raise _fail(E_EMPTY_ROWS, "empty rows")
        for raw_cell in records[0]:
            if len(raw_cell) > _MAX_CELL_CHARS: raise _fail(E_CSV_INVALID, "cell too large")
        header = [cell.strip() for cell in records[0]]
        if len(set(header)) != len(header): raise _fail(E_CSV_INVALID, "duplicate header")
        for name in header:
            if name not in _CANONICAL: raise _fail(E_CSV_INVALID, "unknown header")
        if not _REQUIRED.issubset(set(header)): raise _fail(E_CSV_INVALID, "missing required header")
        index = {name: pos for pos, name in enumerate(header)}
        data = records[1:]
        if len(data) > PLANNING_MAX_ROWS: raise _fail(E_LIMIT_ROWS, "too many rows")
        if len(data) == 0: raise _fail(E_EMPTY_ROWS, "empty rows")
        rows: list[PlanningRow] = []
        seen: set[str] = set()
        has_structure = "structure" in index
        has_edges = "edges" in index
        has_granular = "granular_unit" in index
        for pos, record in enumerate(data, start=1):
            if len(record) != len(header): raise _fail(E_CSV_INVALID, "row width mismatch", pos)
            for cell in record:
                if len(cell) > _MAX_CELL_CHARS: raise _fail(E_CSV_INVALID, "cell too large", pos)
            activity_id = record[index["activity_id"]]
            activity_name = record[index["activity_name"]]
            measurement = record[index["measurement"]]
            if not activity_id.strip() or not activity_name.strip(): raise _fail(E_CSV_INVALID, "missing required value", pos)
            if not measurement.strip(): raise _fail(E_MEASUREMENT_MISSING, "missing measurement", pos)
            key = activity_id.strip()
            if key in seen: raise _fail(E_DUPLICATE_ID, "duplicate activity id", pos)
            seen.add(key)
            volume_text = record[index["volume"]].strip()
            if not volume_text or _NUMERIC_RE.fullmatch(volume_text) is None: raise _fail(E_VOLUME_INVALID, "invalid volume", pos)
            try: volume = float(volume_text)
            except Exception: raise _fail(E_VOLUME_INVALID, "invalid volume", pos) from None
            if not math.isfinite(volume) or volume < 0: raise _fail(E_VOLUME_INVALID, "invalid volume", pos)
            structure = _parse_structure(record[index["structure"]], pos) if has_structure else []
            edges = _parse_edges(record[index["edges"]], pos) if has_edges else []
            enriched = _parse_enriched(record[index["granular_unit"]], pos) if has_granular else None
            try:
                rows.append(PlanningRow(activity_id=activity_id, activity_name=activity_name, volume=volume, measurement=measurement, structure=structure, edges=edges, enriched=enriched))  # type: ignore[arg-type]
            except ValidationError as exc:
                try:
                    errs = exc.errors()
                    loc: object = errs[0].get("loc", ()) if errs else ()
                    loc = loc[0] if loc else None
                except Exception: raise RuntimeError(E_INTERNAL) from None
                if loc == "enriched": code = E_ENRICHED_INVALID
                elif loc == "edges": code = E_EDGE_INVALID
                elif loc == "volume": code = E_VOLUME_INVALID
                elif loc == "measurement": code = E_MEASUREMENT_MISSING
                elif loc in ("activity_id", "activity_name", "structure"): code = E_CSV_INVALID
                else: raise RuntimeError(E_INTERNAL) from None
                raise _fail(code, "invalid row value", pos) from None
            except Exception: raise RuntimeError(E_INTERNAL) from None
        return rows
    except PlanningValidationError:
        raise
    except PlanningRuntimeError:
        raise
    except Exception: raise PlanningRuntimeError(E_INTERNAL) from None
