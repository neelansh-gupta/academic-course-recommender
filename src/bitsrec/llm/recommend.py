"""Rank Stage 2 eligible courses against stored handout fields.

A preference is a match only when a handout for the student's campus states it.
A null field stays unverified. Courses outside the eligible set are never ranked.
Category words, makeup judgments, and timetable phrases constrain that same set.
An explanation is assembled only from those stored fields.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from bitsrec.engine import analyze_profile
from bitsrec.llm.query import parse_query
from bitsrec.paths import raw_dir
from bitsrec.schedule.clashes import load_catalog
from bitsrec.schedule.hours import load_hour_legend
from bitsrec.schedule.select import select_section

_CATEGORY_ORDER = {
    "CDC": 0,
    "NAMED": 1,
    "DEL": 2,
    "HUEL": 3,
    "MINOR_CORE": 4,
    "MINOR_ELECTIVE": 5,
    "PS_OR_THESIS": 6,
    "OPEL": 7,
}
_BOOLEAN_FIELDS = {"has_midsem", "has_compre", "has_project", "has_quiz", "has_assignment"}
_AI = ("artificial intelligence", "a.i.", "ai")
_ML = ("machine learning", "ml")
_ALIAS_GROUPS = {phrase: _AI for phrase in _AI}
_ALIAS_GROUPS.update({phrase: _ML for phrase in _ML})
_QUALITATIVE_MAKEUP = {"lenient", "strict"}


def recommend(
    profile: dict,
    query: str,
    db_path: str | Path,
    held_sections: list[dict] | None = None,
    academic: dict | None = None,
) -> dict:
    academic = analyze_profile(profile, db_path) if academic is None else academic
    parsed = parse_query(query)
    interests = _merge_interests(parsed["interests"], profile.get("interests") or [])
    warnings = _warnings(parsed, interests)
    eligible = academic["eligible"]
    if parsed["contradictions"]:
        return _empty_result(parsed, interests, warnings, len(eligible))

    campus = str(profile.get("campus") or "").strip().lower()
    handouts = _load_handouts(db_path)
    texts = _load_texts(db_path)
    held = held_sections or []
    schedule_on = _schedule_requested(parsed["schedule"], held)
    catalog = load_catalog(db_path) if schedule_on else None
    legend: dict[int, str] = {}
    if parsed["schedule"]["avoid_clocks"]:
        legend = load_hour_legend(raw_dir() / "timetable.pdf")
        if not legend:
            warnings.append(
                "The timetable legend could not be read, so the requested clock time was not mapped to an hour number."
            )
    if schedule_on and catalog is not None:
        warnings.extend(_held_day_warnings(parsed["schedule"], held, catalog))

    recommendations = []
    unverified = []
    not_matched = 0
    for course in eligible:
        if not _category_ok(course, parsed["categories"], parsed["category_mode"]):
            not_matched += 1
            continue
        outcome = _judge(course, handouts.get(course["course_code"], []), texts.get(course["course_code"], {}), parsed["preferences"], interests, campus)
        if outcome["status"] == "not_matched":
            not_matched += 1
            continue
        record = outcome["record"]
        status = outcome["status"]
        if schedule_on:
            picked = select_section(
                course["course_code"],
                held,
                db_path,
                parsed["schedule"],
                legend,
                catalog=catalog,
            )
            if picked["status"] == "not_matched":
                not_matched += 1
                continue
            if picked["status"] == "unverified":
                record["unverified_properties"].append(
                    {"property": "timetable", "reason": picked["reason"]}
                )
                status = "unverified"
            else:
                record["section"] = picked["section"]
        record["explanation"] = _explain(record)
        if status == "matched":
            recommendations.append(record)
        else:
            unverified.append(record)

    recommendations.sort(key=_sort_key)
    unverified.sort(key=_sort_key)
    return {
        "preferences": parsed["preferences"],
        "interests": interests,
        "categories": parsed["categories"],
        "schedule": parsed["schedule"],
        "contradictions": [],
        "unrecognized": parsed["unrecognized"],
        "warnings": warnings,
        "eligible_count": len(eligible),
        "not_matched_count": not_matched,
        "recommendations": recommendations,
        "unverified": unverified,
    }


def consensus(rows: list[dict], field: str) -> tuple[str, object, list[dict]]:
    """Return status, agreed value, and the handouts that state the field."""
    stated = [row for row in rows if row.get(field) is not None]
    if not stated:
        return "unverified", None, []
    values = {row[field] for row in stated}
    if len(values) > 1:
        return "conflict", None, stated
    return "stated", stated[0][field], stated


def _empty_result(parsed: dict, interests: list[str], warnings: list[str], eligible_count: int) -> dict:
    return {
        "preferences": [],
        "interests": interests,
        "categories": parsed["categories"],
        "schedule": parsed["schedule"],
        "contradictions": parsed["contradictions"],
        "unrecognized": parsed["unrecognized"],
        "warnings": warnings,
        "eligible_count": eligible_count,
        "not_matched_count": 0,
        "recommendations": [],
        "unverified": [],
    }


def _warnings(parsed: dict, interests: list[str]) -> list[str]:
    warnings = []
    for prop in parsed["contradictions"]:
        warnings.append(
            f"The query both seeks and avoids {prop}. No courses were ranked on that query."
        )
    for phrase in parsed["unrecognized"]:
        warnings.append(
            f"The query names {phrase!r} without saying whether to seek or avoid it, so it was not used as a filter."
        )
    if parsed["categories"]:
        warnings.append(
            "The query limits courses to " + ", ".join(parsed["categories"]) + "."
        )
    if interests:
        warnings.append(
            "Interest phrases are matched only against stored title, description, and handout syllabus text."
        )
        if any(phrase in _ALIAS_GROUPS for phrase in interests):
            warnings.append(
                "A short form such as AI is also checked against its spelled-out phrase in that stored text."
            )
    return warnings


def _schedule_requested(schedule: dict, held: list[dict]) -> bool:
    return bool(held) or bool(schedule["avoid_clocks"]) or bool(schedule["free_days"]) or schedule["avoid_long_gaps"] or schedule["compact"]


def _held_day_warnings(schedule: dict, held: list[dict], catalog: dict) -> list[str]:
    notes = []
    for day in schedule.get("free_days") or []:
        for ref in held:
            for row in catalog.get(ref.get("course_code"), []):
                if row["section"] != ref.get("section") or row["cancelled"]:
                    continue
                if any(slot["day"] == day for slot in row.get("slots") or []):
                    notes.append(
                        f"A held section already meets on {day}, so that day cannot be kept free. Added sections are still kept off {day} when another section exists."
                    )
    return notes


def _category_ok(course: dict, categories: list[str], mode: str) -> bool:
    if not categories:
        return True
    have = set(course.get("satisfies") or [])
    if mode == "any":
        return bool(have & set(categories))
    return set(categories) <= have


def _merge_interests(query_interests: list[str], profile_interests: list) -> list[str]:
    merged = list(query_interests)
    for value in profile_interests:
        phrase = re.sub(r"\s+", " ", str(value).strip().lower())
        if phrase and phrase not in merged:
            merged.append(phrase)
    return merged


def _judge(course: dict, rows: list[dict], texts: dict, preferences: list[dict], interests: list[str], campus: str) -> dict:
    handouts = [row for row in rows if (row["campus"] or "").strip().lower() == campus]
    matched = []
    pending = []
    for preference in preferences:
        if preference["property"] == "makeup_policy" and preference["wants"] in _QUALITATIVE_MAKEUP:
            outcome = _makeup_judgment(handouts, str(preference["wants"]))
            if outcome["status"] == "unverified":
                pending.append(outcome["pending"])
                continue
            matched.append(outcome["matched"])
            continue
        status, value, evidence = consensus(handouts, preference["property"])
        if status == "unverified":
            pending.append(
                {
                    "property": preference["property"],
                    "reason": _missing_reason(handouts, preference["property"]),
                }
            )
            continue
        if status == "conflict":
            pending.append(
                {
                    "property": preference["property"],
                    "reason": "Handouts for this campus disagree on this field.",
                    "source_files": [row["source_file"] for row in evidence],
                }
            )
            continue
        if not _value_matches(preference["property"], preference["wants"], value):
            return {"status": "not_matched"}
        matched.append(
            {
                "property": preference["property"],
                "value": value,
                "citations": _citations(evidence, preference["property"]),
            }
        )

    interest_matches = []
    for phrase in interests:
        hits = _interest_hits(phrase, texts, handouts)
        if hits is None:
            pending.append(
                {
                    "property": "interest",
                    "term": phrase,
                    "reason": "No stored title, description, or campus handout text was available to check this interest.",
                }
            )
            continue
        if not hits:
            return {"status": "not_matched"}
        interest_matches.extend(hits)

    record = _record(course, matched, interest_matches, pending)
    if pending:
        return {"status": "unverified", "record": record}
    return {"status": "matched", "record": record}


def _makeup_judgment(handouts: list[dict], wants: str) -> dict:
    stated = [row for row in handouts if row.get("makeup_policy")]
    if not stated:
        return {
            "status": "unverified",
            "pending": {
                "property": "makeup_policy",
                "reason": f"No handout for this campus states makeup_policy, so whether it is {wants} could not be verified.",
            },
        }
    hits = [row for row in stated if wants in row["makeup_policy"].lower()]
    if hits:
        return {
            "status": "matched",
            "matched": {
                "property": "makeup_policy",
                "value": wants,
                "citations": _citations(hits, "makeup_policy"),
            },
        }
    return {
        "status": "unverified",
        "pending": {
            "property": "makeup_policy",
            "reason": f"The handout states a makeup policy but does not say that it is {wants}.",
            "source_files": [row["source_file"] for row in stated],
        },
    }


def _citations(rows: list[dict], field: str) -> list[dict]:
    return [
        {
            "source_file": row["source_file"],
            "campus": row["campus"],
            "field": field,
            "value": row[field],
        }
        for row in rows
    ]


def _value_matches(prop: str, wants: object, value: object) -> bool:
    if prop in _BOOLEAN_FIELDS:
        if wants is True:
            return value == 1
        if wants is False:
            return value == 0
        return False
    return value == wants


def _missing_reason(handouts: list[dict], field: str) -> str:
    if not handouts:
        return f"No handout for this campus states {field}."
    return f"The handout for this campus did not yield {field}."


def _interest_hits(phrase: str, texts: dict, handouts: list[dict]) -> list[dict] | None:
    sources = []
    if texts.get("title"):
        sources.append(("title", "courses", None, texts["title"]))
    if texts.get("description"):
        sources.append(("description", "courses", None, texts["description"]))
    for row in handouts:
        if row.get("description"):
            sources.append(("description", "handout", row["source_file"], row["description"]))
        if row.get("syllabus"):
            sources.append(("syllabus", "handout", row["source_file"], row["syllabus"]))
    if not sources:
        return None
    hits = []
    for field, source, source_file, text in sources:
        found = _first_alias(text, phrase)
        if found is None:
            continue
        hit = {"term": found["term"], "query_term": phrase, "field": field, "source": source, "excerpt": found["excerpt"]}
        if source_file:
            hit["source_file"] = source_file
        hits.append(hit)
    return hits


def _first_alias(text: str, phrase: str) -> dict | None:
    needles = _needles(phrase)
    for needle in needles:
        excerpt = _excerpt(text, needle)
        if excerpt is None:
            continue
        return {"term": needle, "excerpt": excerpt}
    return None


def _needles(phrase: str) -> list[str]:
    key = phrase.lower().strip()
    group = _ALIAS_GROUPS.get(key)
    if not group:
        return [key]
    return [key] + [item for item in group if item != key]


def _excerpt(text: str, needle: str) -> str | None:
    if needle in {"ai", "ml"}:
        match = re.search(rf"\b{needle}\b", text, re.IGNORECASE)
    elif needle == "a.i.":
        match = re.search(r"\ba\.i\.", text, re.IGNORECASE)
    else:
        match = re.search(re.escape(needle), text, re.IGNORECASE)
    if match is None:
        return None
    start = max(0, match.start() - 40)
    end = min(len(text), match.end() + 40)
    return re.sub(r"\s+", " ", text[start:end]).strip()


def _record(course: dict, matched: list[dict], interest_matches: list[dict], pending: list[dict]) -> dict:
    return {
        "course_code": course["course_code"],
        "title": course["title"],
        "units": course["units"],
        "satisfies": course["satisfies"],
        "prerequisite_status": course["prerequisite_status"],
        "offered_this_semester": course["offered_this_semester"],
        "matched_properties": matched,
        "interest_matches": interest_matches,
        "unverified_properties": pending,
    }


def _explain(record: dict) -> str:
    categories = ", ".join(record.get("satisfies") or []) or "an eligible list"
    sentences = [f"{record['course_code']} is eligible and can count toward {categories}."]
    for prop in record.get("matched_properties") or []:
        citations = prop.get("citations") or []
        if not citations:
            continue
        files = ", ".join(citation["source_file"] for citation in citations)
        value = prop["value"]
        if isinstance(value, str) and len(value) > 80:
            value = value[:80].rstrip() + "…"
        sentences.append(f"{files} states {prop['property']} is {value}.")
    for hit in record.get("interest_matches") or []:
        where = hit.get("source_file") or hit.get("source")
        sentences.append(f"The stored {hit['field']} in {where} contains \"{hit['term']}\".")
    for item in record.get("unverified_properties") or []:
        sentences.append(item["reason"])
    section = record.get("section")
    if section and section.get("reason"):
        sentences.append(section["reason"])
    return " ".join(sentences)


def _sort_key(record: dict) -> tuple:
    categories = record.get("satisfies") or ["OPEL"]
    priority = min(_CATEGORY_ORDER.get(category, 99) for category in categories)
    return (priority, record["course_code"])


def _load_handouts(db_path: str | Path) -> dict[str, list[dict]]:
    connection = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT course_code, source_file, campus, has_midsem, has_compre, has_project,
                   has_quiz, has_assignment, attendance_status, makeup_policy,
                   description, syllabus
            FROM handouts
            """
        ).fetchall()
    finally:
        connection.close()
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["course_code"], []).append(dict(row))
    return grouped


def _load_texts(db_path: str | Path) -> dict[str, dict]:
    connection = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("SELECT course_code, title, description FROM courses").fetchall()
    finally:
        connection.close()
    return {
        row["course_code"]: {
            "title": row["title"] or "",
            "description": row["description"] or "",
        }
        for row in rows
    }
