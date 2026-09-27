import csv
import io
import json
from pathlib import Path

import pytest


def fixture():
    return json.loads((Path(__file__).parent / "fixtures/small.json").read_text())


def csv_records(payload):
    return list(csv.reader(io.StringIO(payload.decode("utf-8-sig"), newline="")))


@pytest.mark.parametrize(
    "value",
    ["=1+1", " +1", "-12.5", "@SUM(A1)", "\ttext", "\rtext", "  =1", "", "ordinary"],
)
def test_csv_escapes_formula_text_preserving_numeric_measurements(value):
    from app.rendering.render import render_bytes

    data = fixture()
    data["answer"] = value
    data["tables"][0]["rows"][0]["values"] = {
        "name": value,
        "value": -12.5,
    }
    rendered = render_bytes(data, "knowledge", "1.0.0", ["csv", "json"])
    records = csv_records(rendered.formats["csv"].payload)
    expected = value if value in ("", "ordinary") else "'" + value

    assert ["Ответ", expected] in records
    assert [expected, "-12.5", "s1"] in records
    assert json.loads(rendered.formats["json"].payload) == data


def test_csv_escapes_titles_metadata_headers_sources_and_limitations():
    from app.rendering.render import render_bytes

    data = fixture()
    data["title"] = "=title"
    data["metadata"]["question"] = "+question"
    data["tables"][0]["columns"][0]["label"] = "@label"
    data["sources"][0]["title"] = "-source"
    data["limitations"] = ["\tlimitation"]
    rendered = render_bytes(data, "knowledge", "1.0.0", ["csv"])
    cells = [
        cell for row in csv_records(rendered.formats["csv"].payload) for cell in row
    ]

    assert all(
        value in cells
        for value in ["'=title", "'+question", "'@label", "'-source", "'\tlimitation"]
    )
