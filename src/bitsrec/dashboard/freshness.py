"""File dates for the documents the dashboard is reading."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from bitsrec.paths import raw_dir


def dataset_freshness(db_path: str | Path) -> dict:
    raw = raw_dir()
    named = [
        ("Academic regulations", raw / "Academic-Regulations-2023.pdf"),
        ("Bulletin", raw / "bulletin.pdf"),
        ("Timetable", raw / "timetable.pdf"),
        ("Processed database", Path(db_path)),
    ]
    documents = []
    for label, path in named:
        if not path.is_file():
            documents.append({"document": label, "present": False})
            continue
        modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        documents.append(
            {
                "document": label,
                "present": True,
                "modified": modified.strftime("%Y-%m-%d %H:%M UTC"),
                "bytes": path.stat().st_size,
            }
        )
    handouts = raw / "handouts"
    count = len(list(handouts.glob("*.pdf"))) if handouts.is_dir() else 0
    return {
        "documents": documents,
        "handout_pdfs": count,
        "note": "A null field still means that document did not yield the fact. These times are file dates, not a second academic source.",
    }
