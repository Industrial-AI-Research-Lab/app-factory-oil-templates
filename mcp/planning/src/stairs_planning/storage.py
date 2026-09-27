"""Compatibility export for the unchanged ``stairs-storage`` package."""

from stairs_storage import MschmAdapter

ModelsAdapter = MschmAdapter
PostgresModelStorage = MschmAdapter

__all__ = ("ModelsAdapter", "PostgresModelStorage")
