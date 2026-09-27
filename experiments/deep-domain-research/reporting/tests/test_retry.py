import hashlib
import json
from pathlib import Path

import pytest


def test_retry_regenerates_only_failed_format_from_snapshot(tmp_path):
    from reporting.render import render_report, retry_report

    source = Path(__file__).parent / "fixtures/small.json"
    manifest = render_report(source, "knowledge", "1.0.0", ["txt", "json"], tmp_path)
    folder = Path(manifest["manifest_path"]).parent
    original = (folder / "report.json").read_bytes()
    original_mtime = (folder / "report.json").stat().st_mtime_ns
    (folder / "report.txt").unlink()
    manifest["formats"]["txt"].update(
        status="failed",
        error={"code": "RENDER_FAILED", "stage": "render", "message": "Interrupted"},
    )
    Path(manifest["manifest_path"]).write_text(json.dumps(manifest), encoding="utf-8")
    result = retry_report(manifest["manifest_path"])
    assert result["export_id"] == manifest["export_id"]
    assert result["formats"]["txt"]["status"] == "generated"
    assert (folder / "report.json").read_bytes() == original
    assert (folder / "report.json").stat().st_mtime_ns == original_mtime
    assert result["result"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()


def test_retry_rejects_modified_source_snapshot(tmp_path):
    from reporting.render import ReportError, render_report, retry_report

    manifest = render_report(
        Path(__file__).parent / "fixtures/small.json",
        "knowledge",
        "1.0.0",
        ["txt"],
        tmp_path,
    )
    folder = Path(manifest["manifest_path"]).parent
    with (folder / "report-data.json").open("ab") as stream:
        stream.write(b"\n")
    with pytest.raises(ReportError) as error:
        retry_report(manifest["manifest_path"])
    assert error.value.code == "SNAPSHOT_CHANGED"


def test_concurrent_retry_is_rejected_without_touching_manifest(tmp_path):
    from reporting.render import ReportError, render_report, retry_report
    from reporting.state import export_lock

    manifest = render_report(
        Path(__file__).parent / "fixtures/small.json",
        "knowledge",
        "1.0.0",
        ["txt"],
        tmp_path,
    )
    path = Path(manifest["manifest_path"])
    before = path.read_bytes()
    with export_lock(path.parent):
        with pytest.raises(ReportError) as error:
            retry_report(path)
        assert error.value.code == "EXPORT_BUSY"
    assert path.read_bytes() == before


def test_oversized_excel_cell_fails_without_losing_other_formats(tmp_path):
    from reporting.render import render_report

    data = json.loads((Path(__file__).parent / "fixtures/small.json").read_text())
    data["answer"] = "Ж" * 32768
    source = tmp_path / "large.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    manifest = render_report(
        source, "knowledge", "1.0.0", ["xlsx", "txt"], tmp_path / "out"
    )
    assert manifest["formats"]["txt"]["status"] == "generated"
    assert manifest["formats"]["xlsx"]["status"] == "failed"
    assert manifest["formats"]["xlsx"]["error"]["stage"] == "render"


def test_unrepresentable_excel_number_fails_without_corrupting_json(tmp_path):
    from reporting.render import render_report

    data = json.loads((Path(__file__).parent / "fixtures/small.json").read_text())
    data["tables"][0]["rows"][0]["values"]["value"] = 1234567890123456789
    source = tmp_path / "number.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    manifest = render_report(
        source, "knowledge", "1.0.0", ["xlsx", "json"], tmp_path / "out"
    )
    assert manifest["formats"]["xlsx"]["status"] == "failed"
    output = Path(manifest["manifest_path"]).parent / "report.json"
    assert json.loads(output.read_text()) == data


def test_news_counts_remain_numeric_in_workbook(tmp_path):
    from openpyxl import load_workbook
    from reporting.render import render_report

    data = json.loads((Path(__file__).parent / "fixtures/small.json").read_text())
    data["metadata"] = {
        "topic": "Тема",
        "period": "Период",
        "conditions": "Условия",
        "publication_count": 23,
        "event_count": 7,
        "categories": [],
    }
    source = tmp_path / "news.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    manifest = render_report(source, "news", "1.0.0", ["xlsx"], tmp_path / "out")
    sheet = load_workbook(Path(manifest["manifest_path"]).parent / "report.xlsx")[
        "Результат"
    ]
    rows = {row[0].value: row for row in sheet}
    assert rows["Количество публикаций"][1].value == 23
    assert rows["Количество публикаций"][1].data_type == "n"
    assert rows["Количество событий"][1].value == 7


def test_broken_html_template_fails_html_and_pdf_preserving_json(tmp_path):
    from reporting.render import render_report, retry_report

    source = Path(__file__).parent / "fixtures/small.json"
    manifest = render_report(source, "knowledge", "1.0.0", ["json"], tmp_path)
    folder = Path(manifest["manifest_path"]).parent
    broken = b"{{ missing_required_template_field }}"
    (folder / "template.html.j2").write_bytes(broken)
    manifest["snapshot"]["template.html.j2"] = hashlib.sha256(broken).hexdigest()
    for fmt in ("html", "pdf"):
        manifest["formats"][fmt] = {"status": "failed", "error": {"stage": "render"}}
    Path(manifest["manifest_path"]).write_text(json.dumps(manifest), encoding="utf-8")
    result = retry_report(manifest["manifest_path"])
    for fmt in ("html", "pdf"):
        assert result["formats"][fmt]["status"] == "failed"
        assert result["formats"][fmt]["error"]["stage"] == "render"
        assert not (folder / f"report.{fmt}").exists()
    assert result["formats"]["json"]["status"] == "generated"
    assert (folder / "report.json").read_bytes() == source.read_bytes()


@pytest.mark.parametrize("formats", [["txt", "json", "csv"], ["json", "txt", "csv"]])
def test_interrupted_export_retries_all_unfinished_formats(
    tmp_path, monkeypatch, formats
):
    from reporting.render import render_report, retry_report

    write_text = Path.write_text

    def interrupt_txt(path, *args, **kwargs):
        if path.suffix == ".txt":
            raise KeyboardInterrupt("Interrupted file write")
        return write_text(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "write_text", interrupt_txt)
        with pytest.raises(KeyboardInterrupt):
            render_report(
                Path(__file__).parent / "fixtures/small.json",
                "knowledge",
                "1.0.0",
                formats,
                tmp_path,
            )
    manifests = list(tmp_path.glob("report-demo/run-001/*/export-manifest.json"))
    assert len(manifests) == 1
    path = manifests[0]
    saved = json.loads(path.read_text())
    assert list(saved["formats"]) == formats
    generated = {
        fmt: ((path.parent / item["filename"]).stat().st_mtime_ns, item["sha256"])
        for fmt, item in saved["formats"].items()
        if item["status"] == "generated"
    }
    assert set(generated) == ({"json"} if formats[0] == "json" else set())
    for fmt in set(formats) - generated.keys():
        assert saved["formats"][fmt]["error"]["code"] == "NOT_GENERATED"
        assert saved["formats"][fmt]["error"]["stage"] == "render"
    result = retry_report(path)
    assert all(item["status"] == "generated" for item in result["formats"].values())
    for fmt, (mtime, checksum) in generated.items():
        item = result["formats"][fmt]
        assert item["sha256"] == checksum
        assert (path.parent / item["filename"]).stat().st_mtime_ns == mtime
