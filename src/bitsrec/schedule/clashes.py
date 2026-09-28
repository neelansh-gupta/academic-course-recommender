"""Section clash checks from stored timetable rows.

A meeting clash is a shared day and hour. A midsem or comprehensive clash is
the same printed date and session. Missing times stay unverified. Two sections
of one course are compared the same way as sections of different courses.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

_DAY_ORDER = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5}


def compare_sections(left: dict, right: dict) -> dict:
    clashes: list[dict] = []
    unverified: list[dict] = []
    left_slots = _slots(left)
    right_slots = _slots(right)
    if not left_slots or not right_slots:
        if not left_slots:
            unverified.append(_missing_meeting(left))
        if not right_slots and _identity(left) != _identity(right):
            unverified.append(_missing_meeting(right))
    else:
        shared = sorted(set(left_slots) & set(right_slots), key=_slot_key)
        for day, hour in shared:
            clashes.append(
                {
                    "type": "meeting",
                    "day": day,
                    "hour": hour,
                    "kinds": [left.get("section_kind"), right.get("section_kind")],
                }
            )
    for exam in ("midsem", "compre"):
        outcome = _compare_exam(left, right, exam)
        if outcome["status"] == "clash":
            clashes.append(outcome["clash"])
        elif outcome["status"] == "unverified":
            unverified.append(outcome["unverified"])
    return {"clashes": clashes, "unverified": unverified}


def feasible_sections(course_code: str, current_sections: list[dict], db_path: str | Path) -> list[dict]:
    return assess_sections(course_code, current_sections, db_path)["feasible"]


def load_catalog(db_path: str | Path) -> dict[str, list[dict]]:
    return _load_all(db_path)


def assess_sections(
    course_code: str,
    current_sections: list[dict],
    db_path: str | Path,
    catalog: dict[str, list[dict]] | None = None,
) -> dict:
    catalog = load_catalog(db_path) if catalog is None else catalog
    warnings: list[str] = []
    held: list[dict] = []
    unresolved: list[dict] = []
    for ref in current_sections or []:
        found = _lookup(catalog, ref.get("course_code"), ref.get("section"))
        if found is None:
            unresolved.append({"course_code": ref.get("course_code"), "section": ref.get("section")})
            warnings.append(
                f"No timetable row for {ref.get('course_code')} {ref.get('section')}."
            )
            continue
        if found["cancelled"]:
            warnings.append(
                f"{found['course_code']} {found['section']} is cancelled and does not occupy a slot."
            )
            continue
        held.append(found)

    candidates = [row for row in catalog.get(course_code, [])]
    if not candidates:
        warnings.append(f"No timetable rows were stored for {course_code}.")
    held_ids = {_identity(row) for row in held}
    feasible = []
    clashing = []
    unverified_rows = []
    already = []
    cancelled = []
    for candidate in sorted(candidates, key=lambda row: row["section"] or ""):
        if candidate["cancelled"]:
            cancelled.append(candidate)
            continue
        if _identity(candidate) in held_ids:
            already.append(candidate)
            continue
        clashes = []
        pending = []
        for other in held:
            compared = compare_sections(candidate, other)
            for clash in compared["clashes"]:
                clashes.append({**clash, "against": _brief(other)})
            for item in compared["unverified"]:
                pending.append({**item, "against": _brief(other)})
        for ref in unresolved:
            pending.append(
                {
                    "type": "held_section",
                    "reason": "A held section has no timetable row, so overlap cannot be checked.",
                    "against": ref,
                }
            )
        record = {**candidate, "clashes": clashes, "unverified": pending}
        if clashes:
            clashing.append(record)
        elif pending:
            unverified_rows.append(record)
        else:
            feasible.append(record)
    return {
        "course_code": course_code,
        "feasible": feasible,
        "clashing": clashing,
        "unverified": unverified_rows,
        "already_registered": already,
        "cancelled": cancelled,
        "warnings": warnings,
    }


def find_clashes(section_refs: list[dict], db_path: str | Path) -> dict:
    catalog = _load_all(db_path)
    resolved = []
    unresolved = []
    for ref in section_refs:
        found = _lookup(catalog, ref.get("course_code"), ref.get("section"))
        if found is None or found["cancelled"]:
            unresolved.append(
                {
                    "course_code": ref.get("course_code"),
                    "section": ref.get("section"),
                    "reason": "cancelled" if found and found["cancelled"] else "not_stored",
                }
            )
            continue
        resolved.append(found)
    pairs = []
    for index, left in enumerate(resolved):
        for right in resolved[index + 1 :]:
            if _identity(left) == _identity(right):
                continue
            compared = compare_sections(left, right)
            pairs.append(
                {
                    "left": _brief(left),
                    "right": _brief(right),
                    "clashes": compared["clashes"],
                    "unverified": compared["unverified"],
                }
            )
    return {"pairs": pairs, "unresolved": unresolved}


def _compare_exam(left: dict, right: dict, exam: str) -> dict:
    left_date, left_session = left.get(f"{exam}_date"), left.get(f"{exam}_session")
    right_date, right_session = right.get(f"{exam}_date"), right.get(f"{exam}_session")
    if left_date and left_session and right_date and right_session:
        if left_date == right_date and left_session == right_session:
            return {
                "status": "clash",
                "clash": {"type": exam, "date": left_date, "session": left_session},
            }
        return {"status": "clear"}
    missing = []
    if not left_date or not left_session:
        missing.append(_brief(left))
    if not right_date or not right_session:
        missing.append(_brief(right))
    return {
        "status": "unverified",
        "unverified": {
            "type": exam,
            "reason": f"The {exam} date and session were not both stored.",
            "missing_on": missing,
        },
    }


def _missing_meeting(section: dict) -> dict:
    return {
        "type": "meeting",
        "reason": "No meeting times were stored for this section.",
        "section": _brief(section),
    }


def _slots(section: dict) -> set[tuple[str, int]]:
    raw = section.get("slots")
    if raw is None:
        raw = section.get("slots_json") or []
    if isinstance(raw, str):
        raw = json.loads(raw) if raw else []
    found = set()
    for slot in raw:
        day = slot.get("day")
        hour = slot.get("hour")
        if day is None or hour is None:
            continue
        found.add((day, int(hour)))
    return found


def _slot_key(slot: tuple[str, int]) -> tuple:
    return (_DAY_ORDER.get(slot[0], 99), slot[1])


def _identity(section: dict) -> tuple[str | None, str | None]:
    return (section.get("course_code"), section.get("section"))


def _brief(section: dict) -> dict:
    return {
        "course_code": section.get("course_code"),
        "section": section.get("section"),
        "section_kind": section.get("section_kind"),
    }


def _lookup(catalog: dict[str, list[dict]], course_code: str | None, section: str | None) -> dict | None:
    if not course_code or not section:
        return None
    for row in catalog.get(course_code, []):
        if row["section"] == section:
            return row
    return None


def _load_all(db_path: str | Path) -> dict[str, list[dict]]:
    path = Path(db_path).resolve()
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT course_code, section, section_kind, days_hours_raw, slots_json,
                   midsem_date, midsem_session, compre_date, compre_session,
                   cancelled, source_page, room
            FROM timetable_sections
            """
        ).fetchall()
    finally:
        connection.close()
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        item = {
            "course_code": row["course_code"],
            "section": row["section"],
            "section_kind": row["section_kind"],
            "days_hours_raw": row["days_hours_raw"],
            "slots": json.loads(row["slots_json"] or "[]"),
            "midsem_date": row["midsem_date"],
            "midsem_session": row["midsem_session"],
            "compre_date": row["compre_date"],
            "compre_session": row["compre_session"],
            "cancelled": bool(row["cancelled"]),
            "source_page": row["source_page"],
            "room": row["room"],
        }
        grouped.setdefault(item["course_code"], []).append(item)
    return grouped
