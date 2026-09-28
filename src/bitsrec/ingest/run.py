"""Stage 1 entry point.

Extracts the supplied PDFs into data/processed/bits.sqlite and a JSON summary.
Academic facts are taken from those documents. Fields that cannot be read are
stored as NULL and listed for verification.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import zipfile
from pathlib import Path

from bitsrec.ingest.bulletin import parse_bulletin
from bitsrec.ingest.handouts import parse_handout_file
from bitsrec.ingest.regulations import parse_regulations
from bitsrec.ingest.store import write_dataset
from bitsrec.ingest.timetable import parse_timetable
from bitsrec.paths import default_dataset_zip, processed_dir, project_root, raw_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the structured BITS academic dataset.")
    parser.add_argument("--dataset", type=Path, default=default_dataset_zip(), help="Path to dataset.zip")
    parser.add_argument("--raw", type=Path, default=raw_dir(), help="Directory for extracted PDFs")
    parser.add_argument("--out", type=Path, default=processed_dir(), help="Directory for the SQLite database")
    args = parser.parse_args()
    summary = run_ingest(args.dataset, args.raw, args.out)
    print(json.dumps(summary, indent=2))


def run_ingest(dataset_zip: Path, raw: Path, out: Path) -> dict:
    extract_zip(dataset_zip, raw)
    regulations_pdf = raw / "Academic-Regulations-2023.pdf"
    bulletin_pdf = raw / "bulletin.pdf"
    timetable_pdf = raw / "timetable.pdf"
    handout_dir = raw / "handouts"
    for path in (regulations_pdf, bulletin_pdf, timetable_pdf, handout_dir):
        if not path.exists():
            raise FileNotFoundError(f"Missing extracted input: {path}")

    print("Parsing academic regulations...")
    regulations = parse_regulations(regulations_pdf)
    print("Parsing bulletin...")
    bulletin = parse_bulletin(bulletin_pdf)
    print("Parsing timetable...")
    timetable = parse_timetable(timetable_pdf)
    handouts, logs = _parse_handouts(handout_dir)

    out.mkdir(parents=True, exist_ok=True)
    db_path = out / "bits.sqlite"
    print(f"Writing {db_path}...")
    counts = write_dataset(
        db_path,
        {
            "regulations": regulations,
            "bulletin": bulletin,
            "timetable": timetable,
            "handouts": handouts,
            "logs": logs,
        },
    )
    summary = _summary(db_path, counts)
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _export_json(db_path, out)
    return summary


def extract_zip(dataset_zip: Path, raw: Path) -> None:
    marker = raw / "bulletin.pdf"
    if marker.exists() and (raw / "handouts").exists() and (raw / "timetable.pdf").exists():
        return
    if not dataset_zip.exists():
        raise FileNotFoundError(
            f"Dataset archive not found at {dataset_zip}. Place dataset.zip in the project root."
        )
    raw.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dataset_zip) as archive:
        for info in archive.infolist():
            name = info.filename
            if name.startswith("__MACOSX") or "/._" in name or name.endswith("/"):
                continue
            target = raw / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as dest:
                shutil.copyfileobj(source, dest)


def _parse_handouts(handout_dir: Path) -> tuple[list[dict], list[dict]]:
    files = sorted(handout_dir.glob("*.pdf"))
    records: list[dict] = []
    logs: list[dict] = []
    print(f"Parsing {len(files)} handouts...")
    for index, path in enumerate(files, start=1):
        try:
            records.extend(parse_handout_file(path))
        except Exception as exc:  # keep one bad PDF from aborting the corpus
            logs.append(
                {
                    "severity": "error",
                    "stage": "handouts",
                    "message": f"{type(exc).__name__}: {exc}",
                    "source": path.name,
                }
            )
        if index % 100 == 0:
            print(f"  {index}/{len(files)}")
    return records, logs


def _summary(db_path: Path, counts: dict) -> dict:
    connection = sqlite3.connect(db_path)
    try:
        def scalar(query: str) -> int:
            row = connection.execute(query).fetchone()
            return int(row[0]) if row else 0

        role_rows = connection.execute(
            "SELECT role, COUNT(*) FROM programme_courses GROUP BY role ORDER BY role"
        ).fetchall()
        unverified_handouts = scalar(
            "SELECT COUNT(*) FROM handouts WHERE needs_verification = 1"
        )
        midsem_known = scalar("SELECT COUNT(*) FROM handouts WHERE has_midsem IS NOT NULL")
        courses_with_prereq = scalar(
            "SELECT COUNT(*) FROM courses WHERE prerequisites_text IS NOT NULL"
        )
    finally:
        connection.close()
    return {
        "database": str(db_path.relative_to(project_root())),
        "counts": counts,
        "programme_course_roles": {role: count for role, count in role_rows},
        "handouts_needing_verification": unverified_handouts,
        "handouts_with_midsem_verified": midsem_known,
        "courses_with_prerequisite_text": courses_with_prereq,
    }


def _export_json(db_path: Path, out: Path) -> None:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        for table in (
            "courses",
            "programme_rules",
            "programme_courses",
            "handouts",
            "timetable_sections",
            "course_equivalents",
            "regulation_clauses",
        ):
            rows = [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]
            (out / f"{table}.json").write_text(
                json.dumps(rows, ensure_ascii=False),
                encoding="utf-8",
            )
    finally:
        connection.close()


if __name__ == "__main__":
    main()
