import csv

from .content import MISSING, display, headers, metadata, rows

IDENTITY_KEYS = ("project_id", "run_id", "result_id", "result_version")
READER_IDENTITY_LABELS = ("Проект", "Запуск", "Результат", "Версия результата")


def csv_cell(value):
    if isinstance(value, str) and (
        value.lstrip().startswith(("=", "+", "-", "@"))
        or value.startswith(("\t", "\r"))
    ):
        return "'" + value
    return display(value)


def report_rows(data, style, family, reader=False):
    yield [style["heading"], data["title"]]
    labels = READER_IDENTITY_LABELS if reader else IDENTITY_KEYS
    yield [
        item for label, key in zip(labels, IDENTITY_KEYS) for item in (label, data[key])
    ]
    yield from metadata(data, family)
    yield ["Ответ", data["answer"]]
    for table in data["tables"]:
        yield [table["title"]] if reader else [table["id"], table["title"]]
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


def write_csv(path, data, style, family, reader=False):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerows(
            [csv_cell(value) for value in row]
            for row in report_rows(data, style, family, reader)
        )
