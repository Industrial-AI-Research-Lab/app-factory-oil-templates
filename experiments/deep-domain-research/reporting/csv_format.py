import csv

from .content import MISSING, display, headers, metadata, rows


def csv_cell(value):
    if isinstance(value, str) and (
        value.lstrip().startswith(("=", "+", "-", "@"))
        or value.startswith(("\t", "\r"))
    ):
        return "'" + value
    return display(value)


def report_rows(data, style, family):
    yield [style["heading"], data["title"]]
    yield [
        "project_id",
        data["project_id"],
        "run_id",
        data["run_id"],
        "result_id",
        data["result_id"],
        "result_version",
        data["result_version"],
    ]
    yield from metadata(data, family)
    yield ["Ответ", data["answer"]]
    for table in data["tables"]:
        yield [table["id"], table["title"]]
        yield headers(table)
        yield from rows(table)
        if not table["rows"]:
            yield [MISSING]
    yield ["Источники"]
    for source in data["sources"]:
        yield [source["id"], source["title"], source["url"]]
    yield ["Ограничения"]
    for value in data["limitations"] or [MISSING]:
        yield [value]


def write_csv(path, data, style, family):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerows(
            [csv_cell(value) for value in row]
            for row in report_rows(data, style, family)
        )
