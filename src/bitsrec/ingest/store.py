"""SQLite writer for the Stage 1 structured dataset."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from bitsrec.ingest.codes import department_of, level_of
from bitsrec.paths import schema_path


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.executescript(schema_path().read_text(encoding="utf-8"))
    return connection


def write_dataset(db_path: Path, dataset: dict) -> dict:
    connection = connect(db_path)
    try:
        _regulations(connection, dataset["regulations"])
        _rules(connection, dataset["regulations"]["minimum_units"])
        _rules(connection, dataset["bulletin"]["rules"])
        courses = _merge_courses(dataset)
        _courses(connection, courses)
        _programme_courses(connection, dataset["bulletin"]["programme_courses"])
        _equivalents(connection, dataset["timetable"]["equivalents"])
        _handouts(connection, dataset["handouts"])
        _sections(connection, dataset["timetable"]["sections"])
        _logs(connection, dataset.get("logs", []))
        connection.commit()
    finally:
        connection.close()
    return {
        "courses": len(courses),
        "programme_courses": len(dataset["bulletin"]["programme_courses"]),
        "rules": len(dataset["bulletin"]["rules"]) + len(dataset["regulations"]["minimum_units"]),
        "clauses": len(dataset["regulations"]["clauses"]),
        "descriptions": len(dataset["bulletin"]["descriptions"]),
        "handouts": len(dataset["handouts"]),
        "sections": len(dataset["timetable"]["sections"]),
        "equivalents": len(dataset["timetable"]["equivalents"]),
    }


def _regulations(connection: sqlite3.Connection, parsed: dict) -> None:
    connection.executemany(
        """
        INSERT INTO regulation_clauses (clause_id, body, page, source_document)
        VALUES (:clause_id, :body, :page, :source_document)
        """,
        parsed["clauses"],
    )


def _rules(connection: sqlite3.Connection, rows: list[dict]) -> None:
    connection.executemany(
        """
        INSERT INTO programme_rules (
            programme, degree_family, category, min_units, max_units, min_courses,
            max_courses, units_raw, courses_raw, notes, source_document, source_page,
            source_section, confidence
        ) VALUES (
            :programme, :degree_family, :category, :min_units, :max_units, :min_courses,
            :max_courses, :units_raw, :courses_raw, :notes, :source_document, :source_page,
            :source_section, :confidence
        )
        """,
        rows,
    )


def _merge_courses(dataset: dict) -> list[dict]:
    courses: dict[str, dict] = {}

    def ensure(code: str) -> dict:
        if code not in courses:
            courses[code] = {
                "course_code": code,
                "title": None,
                "department": department_of(code),
                "level": level_of(code),
                "lecture_hours": None,
                "practical_hours": None,
                "tutorial_hours": None,
                "units": None,
                "description": None,
                "topics_json": None,
                "prerequisites_text": None,
                "prerequisite_codes_json": "[]",
                "restrictions_text": None,
                "needs_verification": 0,
                "sources": [],
            }
        return courses[code]

    def prefer_title(record: dict, title: str | None, source: dict) -> None:
        if not title:
            return
        current = record["title"]
        if current is None or len(title) > len(current):
            record["title"] = title
            record["sources"].append(source)

    for row in dataset["bulletin"]["programme_courses"]:
        if str(row["course_code"]).startswith("SLOT "):
            continue
        record = ensure(row["course_code"])
        prefer_title(record, row.get("title"), _source(row))
        _fill_number(record, "units", row.get("units"))
        _fill_number(record, "lecture_hours", row.get("lecture_hours"))
        _fill_number(record, "practical_hours", row.get("practical_hours"))

    for row in dataset["bulletin"]["descriptions"]:
        record = ensure(row["course_code"])
        prefer_title(record, row.get("title"), _source(row))
        if row.get("description") and not record["description"]:
            record["description"] = row["description"]
            record["sources"].append(_source(row))
        if row.get("prerequisites_text") and not record["prerequisites_text"]:
            record["prerequisites_text"] = row["prerequisites_text"]
            record["prerequisite_codes_json"] = json.dumps(row.get("prerequisite_codes") or [])
        _fill_number(record, "units", row.get("units"))
        _fill_number(record, "lecture_hours", row.get("lecture_hours"))
        _fill_number(record, "practical_hours", row.get("practical_hours"))

    for row in dataset["timetable"]["sections"]:
        record = ensure(row["course_code"])
        if record["title"] is None:
            prefer_title(record, row.get("title"), {"document": "timetable.pdf", "page": row.get("source_page"), "section": "timetable"})
        _fill_number(record, "units", row.get("units"))
        _fill_number(record, "lecture_hours", row.get("lecture_hours"))
        _fill_number(record, "practical_hours", row.get("practical_hours"))
        _fill_number(record, "tutorial_hours", row.get("tutorial_hours"))
        if row.get("cohort_rule") and not record["restrictions_text"]:
            record["restrictions_text"] = row["cohort_rule"]

    for row in dataset["handouts"]:
        code = row.get("course_code")
        if not code:
            continue
        record = ensure(code)
        prefer_title(record, row.get("title"), {"document": row["source_file"], "page": None, "section": "handout"})
        if row.get("description") and not record["description"]:
            record["description"] = row["description"]
        if row.get("syllabus"):
            record["topics_json"] = json.dumps({"syllabus_excerpt": row["syllabus"][:2000]})

    merged = []
    for record in courses.values():
        if not record["title"]:
            record["needs_verification"] = 1
        record["sources_json"] = json.dumps(record.pop("sources"), ensure_ascii=False)
        merged.append(record)
    return merged


def _fill_number(record: dict, field: str, value) -> None:
    if value is not None and record[field] is None:
        record[field] = value


def _source(row: dict) -> dict:
    return {
        "document": row.get("source_document"),
        "page": row.get("source_page"),
        "section": row.get("source_section"),
    }


def _courses(connection: sqlite3.Connection, rows: list[dict]) -> None:
    connection.executemany(
        """
        INSERT INTO courses (
            course_code, title, department, level, lecture_hours, practical_hours,
            tutorial_hours, units, description, topics_json, prerequisites_text,
            prerequisite_codes_json, restrictions_text, needs_verification, sources_json
        ) VALUES (
            :course_code, :title, :department, :level, :lecture_hours, :practical_hours,
            :tutorial_hours, :units, :description, :topics_json, :prerequisites_text,
            :prerequisite_codes_json, :restrictions_text, :needs_verification, :sources_json
        )
        """,
        rows,
    )


def _programme_courses(connection: sqlite3.Connection, rows: list[dict]) -> None:
    seen: set[tuple] = set()
    unique = []
    for row in rows:
        key = (
            row["programme"],
            row["course_code"],
            row["role"],
            row.get("year_label"),
            row.get("semester_label"),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    connection.executemany(
        """
        INSERT INTO programme_courses (
            programme, course_code, role, year_label, semester_label, units, title,
            source_document, source_page, source_section, confidence, notes
        ) VALUES (
            :programme, :course_code, :role, :year_label, :semester_label, :units, :title,
            :source_document, :source_page, :source_section, :confidence, :notes
        )
        """,
        unique,
    )


def _equivalents(connection: sqlite3.Connection, rows: list[dict]) -> None:
    seen: set[tuple] = set()
    unique = []
    for row in rows:
        key = (row["course_code"], row["equivalent_code"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    connection.executemany(
        """
        INSERT INTO course_equivalents (course_code, equivalent_code, source_document, source_page)
        VALUES (:course_code, :equivalent_code, :source_document, :source_page)
        """,
        unique,
    )


def _handouts(connection: sqlite3.Connection, rows: list[dict]) -> None:
    payload = []
    seen: set[tuple] = set()
    for row in rows:
        if not row.get("course_code"):
            continue
        key = (row["course_code"], row["source_file"])
        if key in seen:
            continue
        seen.add(key)
        item = dict(row)
        item["unverified_fields_json"] = json.dumps(item.pop("unverified_fields"), ensure_ascii=False)
        payload.append(item)
    connection.executemany(
        """
        INSERT INTO handouts (
            course_code, source_file, campus, semester_label, instructor_in_charge,
            instructors, description, syllabus, attendance_excerpt, attendance_status,
            makeup_policy, has_midsem, has_compre, has_project, has_quiz, has_assignment,
            evaluation_json, evaluation_excerpt, unverified_fields_json, confidence,
            needs_verification
        ) VALUES (
            :course_code, :source_file, :campus, :semester_label, :instructor_in_charge,
            :instructors, :description, :syllabus, :attendance_excerpt, :attendance_status,
            :makeup_policy, :has_midsem, :has_compre, :has_project, :has_quiz, :has_assignment,
            :evaluation_json, :evaluation_excerpt, :unverified_fields_json, :confidence,
            :needs_verification
        )
        """,
        payload,
    )


def _sections(connection: sqlite3.Connection, rows: list[dict]) -> None:
    payload = []
    for row in rows:
        item = dict(row)
        item["slots_json"] = json.dumps(item.get("slots_json") or [])
        item["instructors_json"] = json.dumps(item.get("instructors_json") or [])
        payload.append(item)
    connection.executemany(
        """
        INSERT INTO timetable_sections (
            com_code, course_code, title, section, section_kind, instructor, instructors_json,
            room, days_hours_raw, slots_json, midsem_date, midsem_session, compre_date,
            compre_session, lecture_hours, practical_hours, tutorial_hours, self_study_hours,
            units, cohort_rule, cancelled, source_page, confidence
        ) VALUES (
            :com_code, :course_code, :title, :section, :section_kind, :instructor, :instructors_json,
            :room, :days_hours_raw, :slots_json, :midsem_date, :midsem_session, :compre_date,
            :compre_session, :lecture_hours, :practical_hours, :tutorial_hours, :self_study_hours,
            :units, :cohort_rule, :cancelled, :source_page, :confidence
        )
        """,
        payload,
    )


def _logs(connection: sqlite3.Connection, rows: list[dict]) -> None:
    if not rows:
        return
    connection.executemany(
        """
        INSERT INTO extraction_log (severity, stage, message, source)
        VALUES (:severity, :stage, :message, :source)
        """,
        rows,
    )
