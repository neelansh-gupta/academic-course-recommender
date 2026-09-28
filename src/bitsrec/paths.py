"""Project paths. Raw PDFs and the built database live outside the package."""

from __future__ import annotations

from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def schema_path() -> Path:
    return project_root() / "schema" / "schema.sql"


def raw_dir() -> Path:
    return project_root() / "data" / "raw"


def processed_dir() -> Path:
    return project_root() / "data" / "processed"


def default_dataset_zip() -> Path:
    return project_root() / "dataset.zip"
