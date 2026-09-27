import math
import re

from app.config import graph_limits
from app.errors import ReportError


def require(condition, field):
    if not condition:
        raise ReportError("INVALID_INPUT", "validate", {"field": field})


def text(value, field, nonempty=False):
    require(isinstance(value, str), field)
    require(not nonempty or bool(value.strip()), field)


def sequence(value, field):
    require(isinstance(value, list), field)


def unique(items, field):
    require(len(items) == len(set(items)), field)


def validate(data, family):
    require(isinstance(data, dict), "root")
    for key in (
        "project_id",
        "run_id",
        "result_id",
        "result_version",
        "title",
        "answer",
    ):
        text(data.get(key), key, key != "answer")
    for key in ("project_id", "run_id"):
        require(bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", data[key])), key)
    for key in ("tables", "sources", "limitations"):
        sequence(data.get(key), key)
    source_ids = []
    for source in data["sources"]:
        require(isinstance(source, dict), "source")
        for key in ("id", "title"):
            text(source.get(key), f"source.{key}", True)
        require("url" in source, "source.url")
        if source["url"] is not None:
            text(source["url"], "source.url")
        if family == "news":
            validate_date_provenance(source)
        source_ids.append(source["id"])
    unique(source_ids, "sources.id")
    known_source_ids = set(source_ids)
    for item in data["limitations"]:
        text(item, "limitations[]")
    validate_graph(data, family, known_source_ids)
    table_ids = []
    for table in data["tables"]:
        validate_table(table, known_source_ids)
        table_ids.append(table["id"])
    unique(table_ids, "tables.id")
    meta = data.get("metadata")
    require(isinstance(meta, dict), "metadata")
    fields = (
        ("question", "schema_version", "collection_version")
        if family == "knowledge"
        else ("topic", "period", "conditions")
    )
    for key in fields:
        text(meta.get(key), f"metadata.{key}")
    if family == "news":
        for key in ("publication_count", "event_count"):
            require(
                isinstance(meta.get(key), int)
                and not isinstance(meta[key], bool)
                and meta[key] >= 0,
                f"metadata.{key}",
            )
        sequence(meta.get("categories"), "metadata.categories")
        for category in meta["categories"]:
            text(category, "metadata.categories[]")
    return data


def validate_graph(data, family, source_ids):
    if family != "knowledge":
        require("graph" not in data, "graph")
        return
    graph = data.get("graph")
    if graph is None:
        return
    require(isinstance(graph, dict), "graph")
    sequence(graph.get("nodes"), "graph.nodes")
    sequence(graph.get("edges"), "graph.edges")
    max_nodes, max_edges = graph_limits()
    if len(graph["nodes"]) > max_nodes or len(graph["edges"]) > max_edges:
        raise ReportError(
            "GRAPH_TOO_LARGE",
            "validate",
            {
                "max_nodes": max_nodes,
                "actual_nodes": len(graph["nodes"]),
                "max_edges": max_edges,
                "actual_edges": len(graph["edges"]),
            },
        )
    node_ids = []
    for node in graph["nodes"]:
        require(isinstance(node, dict), "graph.node")
        for key in ("id", "label", "type"):
            text(node.get(key), f"graph.node.{key}", True)
        validate_source_ids(node.get("source_ids"), source_ids, "graph.node")
        node_ids.append(node["id"])
    unique(node_ids, "graph.nodes.id")
    known_node_ids = set(node_ids)
    edge_ids = []
    for edge in graph["edges"]:
        require(isinstance(edge, dict), "graph.edge")
        for key in ("id", "source", "target", "label"):
            text(edge.get(key), f"graph.edge.{key}", True)
        require(edge["source"] in known_node_ids, "graph.edge.source")
        require(edge["target"] in known_node_ids, "graph.edge.target")
        validate_source_ids(edge.get("source_ids"), source_ids, "graph.edge")
        edge_ids.append(edge["id"])
    unique(edge_ids, "graph.edges.id")


def validate_source_ids(values, known_ids, field):
    sequence(values, f"{field}.source_ids")
    for identifier in values:
        text(identifier, f"{field}.source_ids[]")
        require(identifier in known_ids, f"{field}.source_ids[]")
    unique(values, f"{field}.source_ids")


def validate_table(table, source_ids):
    require(isinstance(table, dict), "table")
    for key in ("id", "title"):
        text(table.get(key), f"table.{key}", True)
    sequence(table.get("columns"), "table.columns")
    require(bool(table["columns"]), "table.columns")
    sequence(table.get("rows"), "table.rows")
    keys = []
    for column in table["columns"]:
        require(isinstance(column, dict), "column")
        for key in ("key", "label"):
            text(column.get(key), f"column.{key}", True)
        require(column.get("type") in ("string", "number", "boolean"), "column.type")
        require("unit" in column, "column.unit")
        if column["unit"] is not None:
            text(column["unit"], "column.unit")
        keys.append(column["key"])
    unique(keys, "columns.key")
    for row in table["rows"]:
        require(isinstance(row, dict), "row")
        values = row.get("values")
        require(isinstance(values, dict) and set(values) == set(keys), "row.values")
        sequence(row.get("source_ids"), "row.source_ids")
        for identifier in row["source_ids"]:
            text(identifier, "row.source_ids[]")
            require(identifier in source_ids, "row.source_ids[]")
        unique(row["source_ids"], "row.source_ids")
        for column in table["columns"]:
            value = values[column["key"]]
            if value is None:
                continue
            kind = column["type"]
            valid = (kind == "string" and isinstance(value, str)) or (
                kind == "boolean" and isinstance(value, bool)
            )
            if kind == "number":
                valid = isinstance(value, (int, float)) and not isinstance(value, bool)
                if valid and isinstance(value, float):
                    valid = math.isfinite(value)
            require(valid, f"row.values.{column['key']}")


def validate_date_provenance(source):
    from datetime import date

    fields = {
        "published_date",
        "search_published_date",
        "selected_published_date",
        "date_basis",
        "date_limitation",
    }
    if not (fields - {"published_date"}).intersection(source):
        return
    require(fields.issubset(source), "source.date_provenance")
    for key in ("published_date", "search_published_date", "selected_published_date"):
        value = source[key]
        if value is not None:
            text(value, f"source.{key}", True)
            try:
                valid = (
                    len(value) == 10 and date.fromisoformat(value).isoformat() == value
                )
            except ValueError:
                valid = False
            require(valid, f"source.{key}")
    basis = source["date_basis"]
    require(basis in ("extract", "search", None), "source.date_basis")
    limitation = source["date_limitation"]
    if limitation is not None:
        text(limitation, "source.date_limitation", True)
    page, search, selected = (
        source["published_date"],
        source["search_published_date"],
        source["selected_published_date"],
    )
    if basis == "search":
        require(
            page is None and search is not None and selected == search,
            "source.date_basis",
        )
        text(limitation, "source.date_limitation", True)
    elif basis == "extract":
        require(
            page is not None and selected == page and search in (None, page),
            "source.date_basis",
        )
    else:
        require(selected is None, "source.selected_published_date")
