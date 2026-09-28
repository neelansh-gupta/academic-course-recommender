"""Read the Stage 1 database into the indexes the eligibility engine needs."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from bitsrec.ingest.codes import find_codes


@dataclass(frozen=True)
class CourseRecord:
    course_code: str
    title: str | None
    level: str | None
    units: float | None
    description_present: bool
    prerequisites_text: str | None
    prerequisite_codes: tuple[str, ...]


@dataclass(frozen=True)
class ListedCourse:
    programme: str
    course_code: str
    role: str
    units: float | None
    title: str | None
    source_section: str | None
    source_page: int | None


@dataclass(frozen=True)
class RuleRecord:
    programme: str
    category: str
    min_units: float | None
    max_units: float | None
    min_courses: int | None
    max_courses: int | None
    units_raw: str | None
    courses_raw: str | None
    source_section: str | None
    source_page: int | None


@dataclass(frozen=True)
class SectionRecord:
    course_code: str
    cohort_rule: str | None
    cancelled: bool


@dataclass(frozen=True)
class AcademicStore:
    courses: dict[str, CourseRecord]
    listings: tuple[ListedCourse, ...]
    by_programme_role: dict[tuple[str, str], dict[str, ListedCourse]]
    rules: tuple[RuleRecord, ...]
    equivalents: tuple[tuple[str, str], ...]
    sections_by_code: dict[str, tuple[SectionRecord, ...]]
    clauses: dict[str, str]
    titles_by_code: dict[str, tuple[str, ...]]

    def listings_for(self, programme: str, role: str) -> dict[str, ListedCourse]:
        return self.by_programme_role.get((programme, role), {})


def load_store(db_path: str | Path) -> AcademicStore:
    path = Path(db_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Academic database was not found at {path}.")
    return _load_cached((str(path), path.stat().st_mtime_ns))


@lru_cache(maxsize=4)
def _load_cached(key: tuple[str, int]) -> AcademicStore:
    connection = sqlite3.connect(f"file:{key[0]}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return _read(connection)
    finally:
        connection.close()


def _read(connection: sqlite3.Connection) -> AcademicStore:
    courses = {
        row["course_code"]: CourseRecord(
            course_code=row["course_code"],
            title=_blank(row["title"]),
            level=_blank(row["level"]),
            units=row["units"],
            description_present=bool(row["description"] and str(row["description"]).strip()),
            prerequisites_text=_blank(row["prerequisites_text"]),
            prerequisite_codes=tuple(_codes(row["prerequisite_codes_json"])),
        )
        for row in connection.execute(
            """
            SELECT course_code, title, level, units, description,
                   prerequisites_text, prerequisite_codes_json
            FROM courses
            """
        )
    }
    listings = tuple(
        ListedCourse(
            programme=row["programme"],
            course_code=row["course_code"],
            role=row["role"],
            units=row["units"],
            title=_blank(row["title"]),
            source_section=_blank(row["source_section"]),
            source_page=row["source_page"],
        )
        for row in connection.execute(
            """
            SELECT programme, course_code, role, units, title, source_section, source_page
            FROM programme_courses
            """
        )
        if not _placeholder(row["course_code"])
    )
    rules = tuple(
        RuleRecord(
            programme=row["programme"],
            category=row["category"],
            min_units=row["min_units"],
            max_units=row["max_units"],
            min_courses=row["min_courses"],
            max_courses=row["max_courses"],
            units_raw=_blank(row["units_raw"]),
            courses_raw=_blank(row["courses_raw"]),
            source_section=_blank(row["source_section"]),
            source_page=row["source_page"],
        )
        for row in connection.execute(
            """
            SELECT programme, category, min_units, max_units, min_courses, max_courses,
                   units_raw, courses_raw, source_section, source_page
            FROM programme_rules
            """
        )
    )
    equivalents = tuple(
        (row["course_code"], row["equivalent_code"])
        for row in connection.execute(
            "SELECT course_code, equivalent_code FROM course_equivalents"
        )
    )
    grouped: dict[str, list[SectionRecord]] = {}
    for row in connection.execute(
        "SELECT course_code, cohort_rule, cancelled FROM timetable_sections"
    ):
        grouped.setdefault(row["course_code"], []).append(
            SectionRecord(
                course_code=row["course_code"],
                cohort_rule=_blank(row["cohort_rule"]),
                cancelled=bool(row["cancelled"]),
            )
        )
    clauses = {
        row["clause_id"]: row["body"]
        for row in connection.execute("SELECT clause_id, body FROM regulation_clauses")
    }
    titles: dict[str, list[str]] = {}
    for row in listings:
        if row.title:
            titles.setdefault(row.course_code, []).append(row.title)
    return AcademicStore(
        courses=courses,
        listings=listings,
        by_programme_role=_index_listings(listings),
        rules=rules,
        equivalents=equivalents,
        sections_by_code={code: tuple(rows) for code, rows in grouped.items()},
        clauses=clauses,
        titles_by_code={code: tuple(values) for code, values in titles.items()},
    )


def _index_listings(listings: tuple[ListedCourse, ...]) -> dict[tuple[str, str], dict[str, ListedCourse]]:
    indexed: dict[tuple[str, str], dict[str, ListedCourse]] = {}
    for row in listings:
        bucket = indexed.setdefault((row.programme, row.role), {})
        current = bucket.get(row.course_code)
        if current is None or _prefer_listing(row, current):
            bucket[row.course_code] = row
    return indexed


def _prefer_listing(new: ListedCourse, old: ListedCourse) -> bool:
    if old.units is None and new.units is not None:
        return True
    if new.units is None and old.units is not None:
        return False
    if new.title and (not old.title or len(new.title) < len(old.title)):
        return True
    return False


def _codes(raw: str | None) -> list[str]:
    if not raw or not str(raw).strip():
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return find_codes(str(raw))
    if not isinstance(parsed, list):
        return []
    return [code for code in parsed if isinstance(code, str) and code.strip()]


def _blank(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _placeholder(course_code: str) -> bool:
    return course_code.startswith("SLOT ") or course_code.startswith("XXX ")
