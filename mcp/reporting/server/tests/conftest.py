import pytest


@pytest.fixture(autouse=True)
def configured_upload_origin(monkeypatch):
    monkeypatch.setenv(
        "REPORTING_UPLOAD_ALLOWED_ORIGINS",
        "https://uploads.test",
    )
