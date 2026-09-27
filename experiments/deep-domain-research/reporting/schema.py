import math
import re


class ReportError(ValueError):
    def __init__(self, code, stage, details):
        self.code, self.stage, self.details = code, stage, details
        super().__init__(str(details))


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
        source_ids.append(source["id"])
    unique(source_ids, "sources.id")
    for item in data["limitations"]:
        text(item, "limitations[]")
    table_ids = []
    for table in data["tables"]:
        validate_table(table, source_ids)
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
