import hashlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def data():
    return json.loads((Path(__file__).parent / "fixtures/small.json").read_text())


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
def test_invalid_required_fields_reject_before_generation(data, field, value):
    from app.errors import ReportError
    from app.rendering.render import render_bytes

    data[field] = value
    with pytest.raises(ReportError) as error:
        render_bytes(data, "knowledge", "1.0.0", ["json"])

    assert error.value.stage == "validate"


@pytest.mark.parametrize("value", [True, "12.5", float("nan"), float("inf")])
def test_invalid_measurements_are_rejected(data, value):
    from app.errors import ReportError
    from app.rendering.render import render_bytes

    data["tables"][0]["rows"][0]["values"]["value"] = value
    with pytest.raises(ReportError) as error:
        render_bytes(data, "knowledge", "1.0.0", ["json"])

    assert error.value.code == "INVALID_INPUT"


def test_unknown_source_reference_is_rejected(data):
    from app.errors import ReportError
    from app.rendering.render import render_bytes

    data["tables"][0]["rows"][0]["source_ids"] = ["missing"]
    with pytest.raises(ReportError) as error:
        render_bytes(data, "knowledge", "1.0.0", ["json"])

    assert error.value.stage == "validate"


@pytest.mark.parametrize(
    "template_id,template_version,formats,code",
    [
        ("missing", "1.0.0", ["html"], "UNKNOWN_TEMPLATE"),
        ("knowledge", "unknown", ["html"], "UNKNOWN_TEMPLATE"),
        ("knowledge", "1.0.0", ["exe"], "INVALID_FORMATS"),
    ],
)
def test_unknown_render_options_are_rejected(
    data, template_id, template_version, formats, code
):
    from app.errors import ReportError
    from app.rendering.render import render_bytes

    with pytest.raises(ReportError) as error:
        render_bytes(data, template_id, template_version, formats)

    assert error.value.code == code
    assert error.value.stage == "validate"


@pytest.mark.parametrize("formats", [None, [], {}, "html", ["html", "html"], [None]])
def test_invalid_format_list_is_rejected(data, formats):
    from app.errors import ReportError
    from app.rendering.render import render_bytes

    with pytest.raises(ReportError) as error:
        render_bytes(data, "knowledge", "1.0.0", formats)

    assert error.value.code == "INVALID_FORMATS"


@pytest.mark.parametrize("field", ["sources", "tables"])
def test_duplicate_identifiers_are_rejected(data, field):
    from app.errors import ReportError
    from app.rendering.render import render_bytes

    data[field].append(data[field][0])
    with pytest.raises(ReportError) as error:
        render_bytes(data, "knowledge", "1.0.0", ["json"])

    assert error.value.code == "INVALID_INPUT"


def test_result_checksum_matches_returned_canonical_json(data):
    from app.rendering.render import render_bytes

    rendered = render_bytes(data, "knowledge", "1.0.0", ["json"])
    payload = rendered.formats["json"].payload

    assert rendered.result_sha256 == hashlib.sha256(payload).hexdigest()
    assert json.loads(payload) == data


def test_unencodable_unicode_is_rejected_as_invalid_input(data):
    from app.errors import ReportError
    from app.rendering.render import render_bytes

    data["answer"] = "\ud800"
    with pytest.raises(ReportError) as error:
        render_bytes(data, "knowledge", "1.0.0", ["json"])

    assert error.value.code == "INVALID_INPUT"
    assert error.value.stage == "validate"
