"""Chart suggestions and a prerequisite order. Neither one marks courses complete."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from bitsrec.engine.catalog import load_store
from bitsrec.engine.eligibility import analyze_profile
from bitsrec.engine.prerequisites import prerequisite_status

_YEAR = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}
_HALF = {"first": 1, "second": 2}


def suggest_completed(profile: dict, db_path: str | Path) -> dict:
    academic = analyze_profile(profile, db_path)
    current = int(profile["current_semester"])
    store = load_store(db_path)
    path = Path(db_path).resolve()
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    placed: dict[str, dict] = {}
    conflicts: set[str] = set()
    skipped = 0
    try:
        for programme in academic["programmes"]:
            footer = programme.get("footer")
            if not footer:
                continue
            rows = connection.execute(
                """
                SELECT course_code, title, units, year_label, semester_label,
                       source_page, source_section, confidence
                FROM programme_courses
                WHERE programme = ? AND role = 'semester_required'
                """,
                (footer,),
            )
            for row in rows:
                code = row["course_code"]
                if code.startswith("SLOT ") or code.startswith("XXX "):
                    continue
                if code not in store.courses:
                    skipped += 1
                    continue
                index = _chart_index(row["year_label"], row["semester_label"])
                if index is None:
                    skipped += 1
                    continue
                if index >= current:
                    continue
                item = {
                    "course_code": code,
                    "title": store.courses[code].title or row["title"],
                    "units": store.courses[code].units if store.courses[code].units is not None else row["units"],
                    "chart_semester": index,
                    "year_label": row["year_label"],
                    "semester_label": row["semester_label"],
                    "source_page": row["source_page"],
                    "source_section": row["source_section"],
                    "confidence": row["confidence"],
                    "programme": footer,
                    "assumed": True,
                }
                previous = placed.get(code)
                if previous and previous["chart_semester"] != index:
                    conflicts.add(code)
                elif previous is None:
                    placed[code] = item
    finally:
        connection.close()
    for code in conflicts:
        placed.pop(code, None)
    suggested = sorted(placed.values(), key=lambda item: (item["chart_semester"], item["course_code"]))
    return {
        "needs_confirmation": True,
        "applied": False,
        "current_semester": current,
        "suggested": suggested,
        "conflicting_codes": sorted(conflicts),
        "skipped_without_position": skipped,
        "warning": (
            "These courses are printed in an earlier year and half on the extracted semester chart. "
            "Rows with no year were skipped. Nothing is marked complete until you add it yourself."
        ),
    }


def plan_terms(profile: dict, db_path: str | Path) -> dict:
    academic = analyze_profile(profile, db_path)
    store = load_store(db_path)
    completed = set()
    for row in academic["requirements"]:
        completed.update(row.get("completed_codes") or [])
    completed.update(profile.get("completed_courses") or [])
    cap = None if academic["registration_cap"] is None else academic["registration_cap"].get("max_units")
    nodes = _required_nodes(store, academic, completed)
    ready, blocked, unverified = _split_by_prerequisite(store, nodes, completed)
    terms, unpacked = _pack(ready, cap)
    pools = []
    for row in academic["requirements"]:
        if row["category"] in {"CDC", "NAMED_ON_SEMESTER_CHART", "DEGREE_TOTAL", "PS_OR_THESIS"}:
            continue
        if row.get("remaining_courses") or row.get("remaining_units"):
            pools.append(
                {
                    "category": row["category"],
                    "programme": row["programme"],
                    "remaining_courses": row.get("remaining_courses"),
                    "remaining_units": row.get("remaining_units"),
                    "source_section": row.get("source_section"),
                    "source_page": row.get("source_page"),
                }
            )
    return {
        "registration_cap": academic["registration_cap"],
        "terms": terms,
        "blocked": blocked,
        "unverified": unverified,
        "units_unverified": unpacked,
        "open_pools": pools,
        "note": (
            "This order uses unfinished named courses, stored prerequisites, and the registration cap. "
            "It is not the official semester chart. Elective pools are not assigned to a term."
        ),
    }


def _chart_index(year: str | None, semester: str | None) -> int | None:
    if year not in _YEAR or semester not in _HALF:
        return None
    return (_YEAR[year] - 1) * 2 + _HALF[semester]


def _required_nodes(store, academic: dict, completed: set[str]) -> dict[str, dict]:
    nodes: dict[str, dict] = {}
    for programme in academic["programmes"]:
        catalogue = programme.get("catalogue")
        if catalogue:
            for code, row in store.listings_for(catalogue, "CDC").items():
                if code in completed or code not in store.courses:
                    continue
                nodes.setdefault(code, {"course_code": code, "title": store.courses[code].title, "units": _units(store, code, row), "kinds": []})
                if "CDC" not in nodes[code]["kinds"]:
                    nodes[code]["kinds"].append("CDC")
        footer = programme.get("footer")
        if footer:
            for code, row in store.listings_for(footer, "semester_required").items():
                if code in completed or code not in store.courses or code.startswith("SLOT "):
                    continue
                title = (row.title or "").lower()
                if "practice school" in title or "thesis" in title:
                    continue
                if code in store.listings_for(programme.get("catalogue") or "", "CDC"):
                    continue
                nodes.setdefault(code, {"course_code": code, "title": store.courses[code].title, "units": _units(store, code, row), "kinds": []})
                if "NAMED" not in nodes[code]["kinds"]:
                    nodes[code]["kinds"].append("NAMED")
    return nodes


def _split_by_prerequisite(store, nodes: dict[str, dict], completed: set[str]):
    incoming = {code: set() for code in nodes}
    equivalent = _same(store)
    for code, node in nodes.items():
        course = store.courses[code]
        status = prerequisite_status(
            course.prerequisites_text,
            course.prerequisite_codes,
            description_present=course.description_present,
            completed=completed | set(nodes),
            equivalent=equivalent,
        )
        node["prerequisite_status"] = status
        if status == "unverified":
            continue
        if status == "not_satisfied":
            node["blocked_by"] = _missing_prerequisite(course, completed, nodes, equivalent)
            continue
        for required in course.prerequisite_codes:
            if required in nodes and required != code:
                incoming[code].add(required)
    ready_codes = [
        code
        for code, node in nodes.items()
        if node["prerequisite_status"] in {"satisfied", "none_stated"} and "blocked_by" not in node
    ]
    blocked = [
        {
            "course_code": code,
            "title": nodes[code]["title"],
            "reason": "A printed prerequisite"
            + (f" ({nodes[code]['blocked_by']})" if nodes[code].get("blocked_by") else "")
            + " is not completed and is not itself an unfinished named course.",
        }
        for code in nodes
        if nodes[code]["prerequisite_status"] == "not_satisfied"
    ]
    unverified = [
        {
            "course_code": code,
            "title": nodes[code]["title"],
            "reason": "The extracted description does not state whether the prerequisite is satisfied.",
        }
        for code, node in nodes.items()
        if node["prerequisite_status"] == "unverified"
    ]
    ordered = _layers(ready_codes, incoming)
    flat = []
    for layer in ordered:
        for code in layer:
            flat.append(nodes[code])
    return flat, blocked, unverified


def _missing_prerequisite(course, completed: set[str], nodes: dict[str, dict], equivalent) -> str | None:
    for required in course.prerequisite_codes:
        if required in nodes or required in completed:
            continue
        if any(equivalent(required, done) for done in completed):
            continue
        return required
    return None


def _layers(codes: list[str], incoming: dict[str, set[str]]) -> list[list[str]]:
    remaining = set(codes)
    layers = []
    while remaining:
        ready = sorted(code for code in remaining if not (incoming.get(code, set()) & remaining))
        if not ready:
            layers.append(sorted(remaining))
            break
        layers.append(ready)
        remaining -= set(ready)
    return layers


def _pack(nodes: list[dict], cap: float | None) -> tuple[list[dict], list[dict]]:
    terms = []
    current = []
    used = 0.0
    unpacked = []

    def flush() -> None:
        nonlocal current, used
        if not current:
            return
        terms.append(
            {
                "term": len(terms) + 1,
                "units": used,
                "courses": current,
            }
        )
        current = []
        used = 0.0

    for node in nodes:
        units = node.get("units")
        if units is None:
            unpacked.append({"course_code": node["course_code"], "title": node["title"], "reason": "Units were not stored, so it was not placed in a term."})
            continue
        if cap is not None and current and used + units > cap:
            flush()
        current.append(
            {
                "course_code": node["course_code"],
                "title": node["title"],
                "units": units,
                "kinds": node["kinds"],
                "exceeds_cap": cap is not None and units > cap,
            }
        )
        used += units
    flush()
    return terms, unpacked


def _units(store, code: str, row) -> float | None:
    if row is not None and row.units is not None:
        return row.units
    course = store.courses.get(code)
    return None if course is None else course.units


def _same(store):
    parent: dict[str, str] = {}

    def find(code: str) -> str:
        parent.setdefault(code, code)
        while parent[code] != code:
            parent[code] = parent[parent[code]]
            code = parent[code]
        return code

    for left, right in store.equivalents:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    def equivalent(required: str, completed: str) -> bool:
        return required == completed or find(required) == find(completed)

    return equivalent
