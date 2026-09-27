import json
from pathlib import Path

import pytest


@pytest.fixture
def data():
    return json.loads((Path(__file__).parent / "fixtures/small.json").read_text())


def export(tmp_path, data, family="knowledge", version="1.0.0", formats=None):
    from reporting.render import render_report

    source = tmp_path / "input.json"
    source.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return render_report(
        source, family, version, formats or ["html", "json"], tmp_path / "out"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("project_id", "../escape"),
        ("run_id", ""),
        ("answer", None),
        ("tables", {}),
        ("sources", None),
        ("limitations", ""),
    ],
)
def test_invalid_required_fields_reject_before_generation(tmp_path, data, field, value):
    from reporting.render import ReportError

    data[field] = value
    with pytest.raises(ReportError) as error:
        export(tmp_path, data)
    assert error.value.stage == "validate"
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("value", [True, "12.5", float("nan"), float("inf")])
def test_invalid_measurements_are_rejected(tmp_path, data, value):
    from reporting.render import ReportError

    data["tables"][0]["rows"][0]["values"]["value"] = value
    with pytest.raises(ReportError) as error:
        export(tmp_path, data)
    assert error.value.code == "INVALID_INPUT"


def test_unknown_source_reference_is_rejected(tmp_path, data):
    from reporting.render import ReportError

    data["tables"][0]["rows"][0]["source_ids"] = ["missing"]
    with pytest.raises(ReportError) as error:
        export(tmp_path, data)
    assert error.value.stage == "validate"


@pytest.mark.parametrize(
    "family,version,formats",
    [
        ("missing", "1.0.0", ["html"]),
        ("knowledge", "unknown", ["html"]),
        ("knowledge", "1.0.0", ["exe"]),
    ],
)
def test_unknown_export_options_reject_before_generation(
    tmp_path, data, family, version, formats
):
    from reporting.render import ReportError

    with pytest.raises(ReportError) as error:
        export(tmp_path, data, family, version, formats)
    assert error.value.stage == "validate"
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("formats", [None, [], {}, "html", ["html", "html"], [None]])
def test_invalid_format_list_is_rejected(tmp_path, formats):
    from reporting.render import ReportError, render_report

    with pytest.raises(ReportError) as error:
        render_report(
            Path(__file__).parent / "fixtures/small.json",
            "knowledge",
            "1.0.0",
            formats,
            tmp_path / "out",
        )
    assert error.value.code == "INVALID_FORMATS"
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("field", ["sources", "tables"])
def test_duplicate_identifiers_are_rejected(tmp_path, data, field):
    from reporting.render import ReportError

    data[field].append(data[field][0])
    with pytest.raises(ReportError) as error:
        export(tmp_path, data)
    assert error.value.code == "INVALID_INPUT"


def test_template_version_changes_heading_preserving_result(tmp_path, data):
    first = export(tmp_path, data)
    second = export(tmp_path, data, version="1.1.0")
    assert first["export_id"] != second["export_id"]
    assert first["result"] == second["result"]
    first_dir = Path(first["manifest_path"]).parent
    second_dir = Path(second["manifest_path"]).parent
    assert (first_dir / "report.html").read_text() != (
        second_dir / "report.html"
    ).read_text()
    assert (first_dir / "report.json").read_bytes() == (
        second_dir / "report.json"
    ).read_bytes()


@pytest.mark.parametrize("family", ["news", "knowledge"])
def test_empty_report_exports_all_formats(tmp_path, data, family):
    data["tables"][0]["rows"] = []
    data["sources"] = []
    data["limitations"] = []
    if family == "news":
        data["metadata"] = {
            "topic": "Новости",
            "period": "2026-09-01",
            "conditions": "Проверенные",
            "publication_count": 0,
            "event_count": 0,
            "categories": [],
        }
    manifest = export(
        tmp_path,
        data,
        family,
        formats=["txt", "json", "csv", "html", "docx", "xlsx", "pdf"],
    )
    assert all(item["status"] == "generated" for item in manifest["formats"].values())
    assert (
        "Нет данных"
        in (Path(manifest["manifest_path"]).parent / "report.html").read_text()
    )


def test_long_table_keeps_last_row_in_pdf_and_docx(tmp_path, data):
    from docx import Document
    from pypdf import PdfReader

    data["tables"][0]["rows"] = [
        {
            "values": {
                "name": f"Строка {index}: " + "Длинное описание " * 8,
                "value": index,
            },
            "source_ids": ["s1"],
        }
        for index in range(120)
    ]
    manifest = export(tmp_path, data, formats=["pdf", "docx", "xlsx"])
    folder = Path(manifest["manifest_path"]).parent
    assert all(item["status"] == "generated" for item in manifest["formats"].values())
    pages = PdfReader(folder / "report.pdf").pages
    assert len(pages) > 1
    assert "Строка 119" in "".join(page.extract_text() for page in pages)
    doc = Document(folder / "report.docx")
    assert "Строка 119" in doc.tables[0].rows[-1].cells[0].text


def test_symlinked_project_directory_cannot_escape_output_root(tmp_path):
    from reporting.render import ReportError, render_report

    outside = tmp_path / "outside"
    outside.mkdir()
    output = tmp_path / "out"
    output.mkdir()
    (output / "report-demo").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ReportError) as error:
        render_report(
            Path(__file__).parent / "fixtures/small.json",
            "knowledge",
            "1.0.0",
            ["txt"],
            output,
        )
    assert error.value.code == "INVALID_OUTPUT_PATH"
    assert list(outside.iterdir()) == []
