import json
import subprocess
import sys
from pathlib import Path


def test_cli_renders_manifest_as_json(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "reporting",
            "render",
            str(Path(__file__).parent / "fixtures/small.json"),
            "--template-id",
            "knowledge",
            "--template-version",
            "1.0.0",
            "--formats",
            "txt",
            "json",
            "--output-dir",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    manifest = json.loads(result.stdout)
    assert all(item["status"] == "generated" for item in manifest["formats"].values())


def test_cli_reports_structured_validation_failure(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "reporting",
            "render",
            str(tmp_path / "missing.json"),
            "--template-id",
            "knowledge",
            "--template-version",
            "1.0.0",
            "--formats",
            "txt",
            "--output-dir",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert json.loads(result.stdout)["error"]["stage"] == "validate"
