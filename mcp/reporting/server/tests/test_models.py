import json

import pytest
from pydantic import TypeAdapter, ValidationError

from app.models import FormatResult, OutputTarget, RenderReportRequest


def request_data():
    return {
        "report_data": {},
        "template_id": "knowledge",
        "template_version": "1.0.0",
        "outputs": [
            {
                "format": "pdf",
                "filename": "Отчёт.pdf",
                "content_type": "application/pdf",
                "upload_url": "https://uploads.test/report?signature=pdf",
            },
            {
                "format": "html",
                "filename": "report.html",
                "content_type": "text/html",
                "upload_url": "https://uploads.test/report?signature=html",
            },
        ],
        "manifest_upload_url": (
            "https://uploads.test/export-manifest.json?signature=manifest"
        ),
    }


def single_output_request_data():
    data = request_data()
    data["outputs"] = data["outputs"][:1]
    return data


def test_output_destinations_must_be_unique_without_signature_query():
    with pytest.raises(ValidationError):
        RenderReportRequest.model_validate(request_data())


def test_manifest_destination_must_not_overwrite_an_output():
    data = request_data()
    data["outputs"][1]["upload_url"] = "https://uploads.test/report.html?signature=html"
    data["manifest_upload_url"] = "https://uploads.test/report?signature=manifest"

    with pytest.raises(ValidationError):
        RenderReportRequest.model_validate(data)


@pytest.mark.parametrize(
    ("location", "unsafe_url"),
    [
        ("output", "https://attacker.test/report?signature=stolen"),
        ("manifest", "http://uploads.test/manifest?signature=stolen"),
    ],
)
def test_upload_destinations_require_an_allowed_https_origin(location, unsafe_url):
    data = single_output_request_data()
    if location == "output":
        data["outputs"][0]["upload_url"] = unsafe_url
    else:
        data["manifest_upload_url"] = unsafe_url

    with pytest.raises(ValidationError):
        RenderReportRequest.model_validate(data)


def test_upload_destinations_allow_opted_in_plain_http_object_storage(monkeypatch):
    monkeypatch.setenv("ALLOW_INSECURE_OBJECT_STORAGE", "true")
    monkeypatch.setenv(
        "REPORTING_UPLOAD_ALLOWED_ORIGINS",
        "http://minio:9000",
    )
    data = single_output_request_data()
    data["outputs"][0]["upload_url"] = "http://minio:9000/report.pdf?signature=pdf"
    data["manifest_upload_url"] = (
        "http://minio:9000/export-manifest.json?signature=manifest"
    )

    accepted = RenderReportRequest.model_validate(data)

    assert accepted.outputs[0].upload_url.scheme == "http"
    assert accepted.manifest_upload_url.scheme == "http"


def test_plain_http_object_storage_remains_disabled_without_opt_in(monkeypatch):
    monkeypatch.delenv("ALLOW_INSECURE_OBJECT_STORAGE", raising=False)
    monkeypatch.setenv(
        "REPORTING_UPLOAD_ALLOWED_ORIGINS",
        "http://minio:9000",
    )
    data = single_output_request_data()
    data["outputs"][0]["upload_url"] = "http://minio:9000/report.pdf"
    data["manifest_upload_url"] = "http://minio:9000/export-manifest.json"

    with pytest.raises(ValidationError):
        RenderReportRequest.model_validate(data)


@pytest.mark.parametrize("location", ["output", "manifest"])
def test_upload_destinations_reject_userinfo(location):
    data = single_output_request_data()
    if location == "output":
        data["outputs"][0][
            "upload_url"
        ] = "https://user:secret@uploads.test/report?signature=signed"
    else:
        data["manifest_upload_url"] = (
            "https://user:secret@uploads.test/manifest?signature=signed"
        )

    with pytest.raises(ValidationError):
        RenderReportRequest.model_validate(data)


def test_upload_destinations_fail_closed_without_configured_origins(monkeypatch):
    monkeypatch.delenv("REPORTING_UPLOAD_ALLOWED_ORIGINS")

    with pytest.raises(ValidationError):
        RenderReportRequest.model_validate(single_output_request_data())


def test_malformed_configured_origin_raises_stable_configuration_error(monkeypatch):
    monkeypatch.setenv(
        "REPORTING_UPLOAD_ALLOWED_ORIGINS",
        "https://uploads.test:not-a-port",
    )

    with pytest.raises(
        RuntimeError,
        match="REPORTING_UPLOAD_ALLOWED_ORIGINS contains an invalid origin",
    ):
        RenderReportRequest.model_validate(single_output_request_data())


def test_validation_error_text_hides_signed_urls():
    data = request_data()
    data["outputs"] = [data["outputs"][0]]
    data["outputs"][0]["content_type"] = "text/plain"

    with pytest.raises(ValidationError) as captured:
        RenderReportRequest.model_validate(data)

    assert "signature=pdf" not in str(captured.value)
    assert "signature=manifest" not in str(captured.value)
    structured = json.dumps(captured.value.errors(), default=str)
    assert "signature=pdf" not in structured
    assert "signature=manifest" not in structured
    assert "signature=" not in captured.value.json()


@pytest.mark.parametrize("entrypoint", ["constructor", "json", "type_adapter"])
def test_all_validation_entrypoints_hide_signed_urls(entrypoint):
    data = single_output_request_data()
    data["outputs"][0]["content_type"] = "text/plain"

    with pytest.raises(ValidationError) as captured:
        if entrypoint == "constructor":
            RenderReportRequest(**data)
        elif entrypoint == "json":
            RenderReportRequest.model_validate_json(json.dumps(data))
        else:
            TypeAdapter(RenderReportRequest).validate_python(data)

    structured = json.dumps(captured.value.errors(), default=str)
    assert "signature=" not in str(captured.value)
    assert "signature=" not in structured
    assert "signature=" not in captured.value.json()


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "ready", "sha256": None},
        {"status": "ready", "size_bytes": -1},
        {
            "status": "ready",
            "error": {"code": "UPLOAD_FAILED", "stage": "upload", "message": "x"},
        },
        {"status": "failed", "error": None},
        {
            "status": "failed",
            "error": {"code": "RENDER_FAILED", "stage": "render", "message": "x"},
        },
        {
            "status": "failed",
            "size_bytes": 0,
            "sha256": None,
            "error": {"code": "UPLOAD_FAILED", "stage": "upload", "message": "x"},
        },
    ],
)
def test_format_result_rejects_impossible_status_combinations(overrides):
    data = {
        "status": "ready",
        "filename": "report.pdf",
        "content_type": "application/pdf",
        "size_bytes": 1,
        "sha256": "a" * 64,
        "error": None,
    }

    with pytest.raises(ValidationError):
        FormatResult.model_validate({**data, **overrides})


def test_zero_outputs_are_rejected():
    data = single_output_request_data()
    data["outputs"] = []

    with pytest.raises(ValidationError):
        RenderReportRequest.model_validate(data)


def test_outputs_are_capped_at_seven():
    data = single_output_request_data()
    content_types = {
        "txt": "text/plain",
        "json": "application/json",
        "csv": "text/csv",
        "html": "text/html",
        "pdf": "application/pdf",
        "docx": (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    data["outputs"] = [
        {
            "format": fmt,
            "filename": f"report.{fmt}",
            "content_type": content_type,
            "upload_url": f"https://uploads.test/{fmt}?signature=secret-{fmt}",
        }
        for fmt, content_type in content_types.items()
    ]

    accepted = RenderReportRequest.model_validate(data)

    assert len(accepted.outputs) == 7
    with pytest.raises(ValidationError):
        RenderReportRequest.model_validate(
            {**data, "outputs": data["outputs"] + [data["outputs"][0]]}
        )


def test_duplicate_formats_are_rejected():
    data = single_output_request_data()
    duplicate = {
        **data["outputs"][0],
        "filename": "duplicate.pdf",
        "upload_url": "https://uploads.test/duplicate?signature=secret-duplicate",
    }
    data["outputs"].append(duplicate)

    with pytest.raises(ValidationError):
        RenderReportRequest.model_validate(data)


def test_content_type_mismatch_is_rejected():
    data = single_output_request_data()
    data["outputs"][0]["content_type"] = "text/plain"

    with pytest.raises(ValidationError):
        RenderReportRequest.model_validate(data)


def test_request_schema_declares_supported_literals():
    output_schema = OutputTarget.model_json_schema()["properties"]
    request_schema = RenderReportRequest.model_json_schema()["properties"]

    assert output_schema["format"]["enum"] == [
        "txt",
        "json",
        "csv",
        "html",
        "pdf",
        "docx",
        "xlsx",
    ]
    assert request_schema["template_id"]["enum"] == ["news", "knowledge"]
    assert request_schema["template_version"]["enum"] == [
        "1.0.0",
        "1.1.0",
        "1.2.0",
        "1.3.0",
        "1.4.0",
    ]


def test_news_broad_version_requires_source_artifacts():
    data = single_output_request_data()
    data["template_id"] = "news"
    data["template_version"] = "1.3.0"

    with pytest.raises(ValidationError):
        RenderReportRequest.model_validate(data)


@pytest.mark.parametrize(
    "filename",
    [
        "",
        "report final.pdf",
        "../report.pdf",
        "folder/report.pdf",
        "\x00.pdf",
        "a" * 161,
    ],
)
def test_unsafe_filenames_are_rejected(filename):
    data = single_output_request_data()
    assert data["outputs"][0]["filename"] == "Отчёт.pdf"
    data["outputs"][0]["filename"] = filename

    with pytest.raises(ValidationError):
        RenderReportRequest.model_validate(data)


def test_filename_accepts_cyrillic_at_length_limit():
    data = single_output_request_data()
    data["outputs"][0]["filename"] = "Я" * 160

    accepted = RenderReportRequest.model_validate(data)

    assert accepted.outputs[0].filename == "Я" * 160


@pytest.mark.parametrize("location", ["request", "output"])
def test_unknown_request_fields_are_rejected(location):
    data = single_output_request_data()
    target = data if location == "request" else data["outputs"][0]
    target["unexpected"] = "value"

    with pytest.raises(ValidationError):
        RenderReportRequest.model_validate(data)
