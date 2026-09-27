import csv
import json
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "value",
    ["=1+1", " +1", "-12.5", "@SUM(A1)", "\ttext", "\rtext", "  =1", "", "ordinary"],
)
def test_csv_escapes_formula_text_preserving_numeric_measurements(tmp_path, value):
    from reporting.render import render_report

    source = tmp_path / "input.json"
    data = json.loads((Path(__file__).parent / "fixtures/small.json").read_text())
    data["answer"] = value
    data["tables"][0]["rows"][0]["values"] = {"name": value, "value": -12.5}
    source.write_text(json.dumps(data), encoding="utf-8")
    manifest = render_report(
        source, "knowledge", "1.0.0", ["csv", "json"], tmp_path / "out"
    )
    folder = Path(manifest["manifest_path"]).parent
    with (folder / "report.csv").open(encoding="utf-8-sig", newline="") as stream:
        records = list(csv.reader(stream))
    expected = value if value in ("", "ordinary") else "'" + value
    assert ["Ответ", expected] in records
    assert [expected, "-12.5", "s1"] in records
    assert (folder / "report.json").read_bytes() == source.read_bytes()


def test_csv_escapes_titles_metadata_headers_sources_and_limitations(tmp_path):
    from reporting.render import render_report

    source = tmp_path / "input.json"
    data = json.loads((Path(__file__).parent / "fixtures/small.json").read_text())
    data["title"] = "=title"
    data["metadata"]["question"] = "+question"
    data["tables"][0]["columns"][0]["label"] = "@label"
    data["sources"][0]["title"] = "-source"
    data["limitations"] = ["\tlimitation"]
    source.write_text(json.dumps(data), encoding="utf-8")
    manifest = render_report(source, "knowledge", "1.0.0", ["csv"], tmp_path / "out")
    with (Path(manifest["manifest_path"]).parent / "report.csv").open(
        encoding="utf-8-sig", newline=""
    ) as stream:
        cells = [cell for row in csv.reader(stream) for cell in row]
    assert all(
        value in cells
        for value in ["'=title", "'+question", "'@label", "'-source", "'\tlimitation"]
    )
