"""Assemble a recommendation page from the eligibility, preference, and timetable stages."""

from __future__ import annotations

from pathlib import Path

from bitsrec.engine import analyze_profile
from bitsrec.llm import recommend
from bitsrec.schedule import assess_sections


def build_feed(
    profile: dict,
    query: str,
    db_path: str | Path,
    *,
    limit: int = 20,
    offset: int = 0,
    unverified_offset: int = 0,
    held_sections: list[dict] | None = None,
) -> dict:
    academic = analyze_profile(profile, db_path)
    matched = recommend(profile, query, db_path, held_sections=held_sections, academic=academic)
    recommendations = matched["recommendations"]
    unverified = matched["unverified"]
    page_limit = _bound(limit, default=20, upper=100)
    page_offset = max(0, offset)
    hidden_offset = max(0, unverified_offset)
    return {
        "programmes": academic["programmes"],
        "requirements": academic["requirements"],
        "registration_cap": academic["registration_cap"],
        "in_progress": academic["in_progress"],
        "academic_warnings": academic["warnings"],
        "preference_warnings": matched["warnings"],
        "preferences": matched["preferences"],
        "interests": matched["interests"],
        "categories": matched["categories"],
        "schedule": matched["schedule"],
        "unrecognized": matched["unrecognized"],
        "contradictions": matched["contradictions"],
        "eligible_count": matched["eligible_count"],
        "not_matched_count": matched["not_matched_count"],
        "recommendation_count": len(recommendations),
        "unverified_count": len(unverified),
        "recommendations": recommendations[page_offset : page_offset + page_limit],
        "unverified": unverified[hidden_offset : hidden_offset + page_limit],
        "offset": page_offset,
        "unverified_offset": hidden_offset,
        "limit": page_limit,
    }


def build_sections(course_code: str, held_sections: list[dict], db_path: str | Path) -> dict:
    code = " ".join(str(course_code or "").upper().split())
    return assess_sections(code, held_sections or [], db_path)


def _bound(value: int, *, default: int, upper: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    if number < 1:
        return default
    return min(number, upper)
