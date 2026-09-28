"""Choose one section per requested course so the set does not clash.

The search stays inside sections that are feasible against the sections already
held. A course with no comparable section is left unplaced.
"""

from __future__ import annotations

from pathlib import Path

from bitsrec.paths import raw_dir
from bitsrec.schedule.clashes import assess_sections, compare_sections, load_catalog
from bitsrec.schedule.hours import hours_for_clock, load_hour_legend
from bitsrec.schedule.select import _held_slots, _violations

_COURSE_LIMIT = 6
_SECTION_LIMIT = 8
_SEARCH_LIMIT = 4000


def combine_sections(
    course_codes: list[str],
    held_sections: list[dict],
    db_path: str | Path,
    schedule: dict | None = None,
) -> dict:
    requested = []
    for code in course_codes or []:
        cleaned = " ".join(str(code).upper().split())
        if cleaned and cleaned not in requested:
            requested.append(cleaned)
    requested = requested[:_COURSE_LIMIT]
    schedule = schedule or {
        "avoid_clocks": [],
        "free_days": [],
        "avoid_long_gaps": False,
        "compact": False,
    }
    catalog = load_catalog(db_path)
    legend = {}
    if schedule.get("avoid_clocks"):
        legend = load_hour_legend(raw_dir() / "timetable.pdf")
    avoid_hours: set[int] = set()
    missing_clock = False
    for clock in schedule.get("avoid_clocks") or []:
        hours = hours_for_clock(legend, int(clock["hour"]), str(clock["meridiem"]))
        if not hours:
            missing_clock = True
        avoid_hours.update(hours)
    held = held_sections or []
    held_slots = _held_slots(held, catalog)
    options = []
    unplaced = []
    if missing_clock:
        return {
            "chosen": [],
            "unplaced": [{"course_code": code, "reason": "The timetable legend does not map the requested clock time."} for code in requested],
            "complete": False,
            "searched": 0,
            "truncated": False,
            "warning": "No combination was built because a requested clock time could not be mapped.",
        }
    for code in requested:
        assessed = assess_sections(code, held, db_path, catalog=catalog)
        usable = []
        for section in assessed["feasible"]:
            reasons = _violations(
                section,
                held_slots,
                avoid_hours,
                set(schedule.get("free_days") or []),
                bool(schedule.get("avoid_long_gaps")),
            )
            if reasons:
                continue
            if reasons is None:
                continue
            usable.append(section)
        if not usable:
            reason = "No stored section is feasible against the held sections and the timetable request."
            if assessed["unverified"] and not assessed["feasible"] and not assessed["clashing"]:
                reason = "Stored sections are missing meeting or exam times, so a clash-free choice cannot be confirmed."
            elif assessed["clashing"] and not assessed["feasible"]:
                reason = "Every stored section clashes with a held section."
            unplaced.append({"course_code": code, "reason": reason})
            continue
        options.append((code, usable[:_SECTION_LIMIT]))

    best: dict | None = None
    searched = 0
    truncated = False

    def consider(chosen: list[dict]) -> None:
        nonlocal best
        if len(chosen) != len(options):
            return
        score = _span(chosen, held_slots)
        if best is None or score < best["score"]:
            best = {"score": score, "chosen": list(chosen)}

    def search(index: int, chosen: list[dict]) -> None:
        nonlocal searched, truncated
        if truncated:
            return
        searched += 1
        if searched > _SEARCH_LIMIT:
            truncated = True
            return
        if index == len(options):
            consider(chosen)
            return
        for section in options[index][1]:
            if any(_blocks(section, other) for other in chosen):
                continue
            chosen.append(section)
            search(index + 1, chosen)
            chosen.pop()

    if options:
        search(0, [])
    chosen = [] if best is None else [_public(section) for section in best["chosen"]]
    placed = {item["course_code"] for item in chosen}
    for code, _sections in options:
        if code not in placed:
            unplaced.append(
                {
                    "course_code": code,
                    "reason": "No clash-free combination with the other requested courses was found.",
                }
            )
    complete = bool(options) and not unplaced and not truncated
    warning = None
    if len(course_codes or []) > _COURSE_LIMIT:
        warning = f"Only the first {_COURSE_LIMIT} courses were compared."
    elif truncated and not chosen:
        warning = "The section search stopped before a full clash-free set was confirmed."
    elif not chosen and options:
        warning = "No clash-free combination was found."
    return {
        "chosen": chosen,
        "unplaced": unplaced,
        "complete": complete,
        "searched": searched,
        "truncated": truncated,
        "warning": warning,
    }


def _blocks(left: dict, right: dict) -> bool:
    compared = compare_sections(left, right)
    return bool(compared["clashes"] or compared["unverified"])


def _span(chosen: list[dict], held_slots: list[dict]) -> tuple:
    by_day: dict[str, list[int]] = {}
    for section in chosen:
        for slot in section.get("slots") or []:
            by_day.setdefault(slot["day"], []).append(int(slot["hour"]))
    for slot in held_slots:
        by_day.setdefault(slot["day"], []).append(int(slot["hour"]))
    width = 0
    for hours in by_day.values():
        width += max(hours) - min(hours)
    return (width, len(by_day), [section.get("section") for section in chosen])


def _public(section: dict) -> dict:
    return {
        "course_code": section.get("course_code"),
        "section": section.get("section"),
        "section_kind": section.get("section_kind"),
        "days_hours_raw": section.get("days_hours_raw"),
        "room": section.get("room"),
        "midsem_date": section.get("midsem_date"),
        "midsem_session": section.get("midsem_session"),
        "compre_date": section.get("compre_date"),
        "compre_session": section.get("compre_session"),
    }
