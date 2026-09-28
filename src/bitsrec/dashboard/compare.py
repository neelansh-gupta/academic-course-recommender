"""Side-by-side facts for courses the student shortlisted."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from bitsrec.engine.catalog import load_store
from bitsrec.ingest.codes import find_codes


def compare_courses(course_codes: list[str], campus: str, db_path: str | Path) -> dict:
    store = load_store(db_path)
    wanted = []
    for value in course_codes or []:
        found = find_codes(str(value))
        code = found[0] if found else " ".join(str(value).upper().split())
        if code and code not in wanted:
            wanted.append(code)
    path = Path(db_path).resolve()
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    campus_key = (campus or "").strip().lower()
    courses = []
    try:
        for code in wanted[:8]:
            course = store.courses.get(code)
            handout = _handout(connection, code, campus_key)
            sections = _sections(connection, code)
            if course is None and handout is None and not sections:
                courses.append({"course_code": code, "known": False, "reason": "This code is not in the extracted catalogue, handouts, or timetable."})
                continue
            courses.append(
                {
                    "course_code": code,
                    "known": True,
                    "title": None if course is None else course.title,
                    "units": None if course is None else course.units,
                    "level": None if course is None else course.level,
                    "prerequisites_text": None if course is None else course.prerequisites_text,
                    "description_present": False if course is None else course.description_present,
                    "handout": handout,
                    "sections": sections,
                }
            )
    finally:
        connection.close()
    return {
        "campus": campus_key,
        "courses": courses,
        "note": "A null handout field means that campus handout did not state it. Another campus is not used.",
    }


def _handout(connection: sqlite3.Connection, code: str, campus: str) -> dict | None:
    rows = connection.execute(
        """
        SELECT source_file, campus, instructor_in_charge, attendance_status,
               makeup_policy, has_midsem, has_compre, has_project, has_quiz,
               has_assignment, confidence
        FROM handouts
        WHERE course_code = ?
        """,
        (code,),
    ).fetchall()
    matched = [row for row in rows if (row["campus"] or "").strip().lower() == campus]
    if not matched:
        return None
    row = matched[0]
    return {
        "source_file": row["source_file"],
        "campus": row["campus"],
        "instructor_in_charge": row["instructor_in_charge"],
        "attendance_status": row["attendance_status"],
        "makeup_policy": row["makeup_policy"],
        "has_midsem": row["has_midsem"],
        "has_compre": row["has_compre"],
        "has_project": row["has_project"],
        "has_quiz": row["has_quiz"],
        "has_assignment": row["has_assignment"],
        "confidence": row["confidence"],
        "handout_count": len(matched),
    }


def _sections(connection: sqlite3.Connection, code: str) -> list[dict]:
    rows = connection.execute(
        """
        SELECT section, section_kind, days_hours_raw, room, cancelled
        FROM timetable_sections
        WHERE course_code = ?
        ORDER BY section
        """,
        (code,),
    ).fetchall()
    return [
        {
            "section": row["section"],
            "section_kind": row["section_kind"],
            "days_hours_raw": row["days_hours_raw"],
            "room": row["room"],
            "cancelled": bool(row["cancelled"]),
        }
        for row in rows
    ]
