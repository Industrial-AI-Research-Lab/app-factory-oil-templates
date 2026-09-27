"""Contracts for packaged source repositories and adapter ownership."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_required_source_packages_are_packaged() -> None:
    """The MCP image contains the source packages needed at runtime."""
    required = (
        ROOT
        / "sources"
        / "stairs-resource-model"
        / "stairs_resource_model"
        / "res_time_model.py",
        ROOT
        / "sources"
        / "stairs-resource-model"
        / "stairs_resource_model"
        / "schema.py",
        ROOT
        / "sources"
        / "stairs-storage"
        / "src"
        / "stairs_storage"
        / "adapters"
        / "mschm.py",
        ROOT
        / "sources"
        / "stairs-storage"
        / "src"
        / "stairs_storage"
        / "adapters"
        / "models.py",
        ROOT
        / "sources"
        / "stairs-backend"
        / "services"
        / "scheduler"
        / "work_estimator"
        / "field_dev.py",
    )
    assert all(path.is_file() for path in required)


def test_adapters_import_source_packages_directly() -> None:
    """Planning adapters do not execute the former local core copies."""
    resource_adapter = (
        ROOT / "src" / "planning_adapter" / "resource_adapter.py"
    ).read_text(encoding="utf-8")
    storage_adapter = (
        ROOT / "src" / "planning_adapter" / "storage_adapter.py"
    ).read_text(encoding="utf-8")
    assert (
        "from stairs_resource_model.res_time_model import ResTimeModel"
        in resource_adapter
    )
    assert "from stairs_storage import MschmAdapter" in storage_adapter


def test_local_planning_modules_are_compatibility_exports() -> None:
    """The old import paths contain no copied resource/storage algorithms."""
    resource_model = (
        ROOT / "src" / "stairs_planning" / "resource_model.py"
    ).read_text(encoding="utf-8")
    storage = (ROOT / "src" / "stairs_planning" / "storage.py").read_text(
        encoding="utf-8"
    )
    assert "pickle" not in resource_model
    assert "create_engine" not in storage
