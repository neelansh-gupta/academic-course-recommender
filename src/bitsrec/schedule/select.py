"""Pick a section that stays feasible and meets stated timetable preferences.

A course stays eligible when one section works and another clashes. It is
rejected only when every comparable section fails. Missing times stay unverified.
"""

from __future__ import annotations

from pathlib import Path

from bitsrec.schedule.clashes import assess_sections
from bitsrec.schedule.hours import hours_for_clock

_LONG_GAP = 2


def select_section(
    course_code: str,
    held_sections: list[dict],
    db_path: str | Path,
    schedule: dict,
    legend: dict[int, str],
    catalog: dict | None = None,
) -> dict:
    assessed = assess_sections(course_code, held_sections or [], db_path, catalog=catalog)
    avoid_hours: set[int] = set()
    missing_clock = False
    for clock in schedule.get("avoid_clocks") or []:
        hours = hours_for_clock(legend, int(clock["hour"]), str(clock["meridiem"]))
        if not hours:
            missing_clock = True
        avoid_hours.update(hours)
    if missing_clock:
        return {
            "status": "unverified",
            "section": None,
            "reason": "The timetable legend does not map the requested clock time to an hour number.",
        }
    held_slots = _held_slots(held_sections or [], catalog)
    return choose_among(
        assessed["feasible"],
        assessed["clashing"],
        assessed["unverified"],
        held_slots=held_slots,
        avoid_hours=avoid_hours,
        free_days=set(schedule.get("free_days") or []),
        avoid_gaps=bool(schedule.get("avoid_long_gaps")),
        compact=bool(schedule.get("compact")),
        empty_reason=_empty_reason(assessed),
    )


def choose_among(
    feasible: list[dict],
    clashing: list[dict],
    unverified: list[dict],
    *,
    held_slots: list[dict],
    avoid_hours: set[int],
    free_days: set[str],
    avoid_gaps: bool,
    compact: bool,
    empty_reason: str | None = None,
) -> dict:
    accepted = []
    rejected = []
    unknown = []
    for section in feasible:
        reasons = _violations(section, held_slots, avoid_hours, free_days, avoid_gaps)
        if reasons is None:
            unknown.append(section)
        elif reasons:
            rejected.append((section, reasons))
        else:
            accepted.append(section)
    if accepted:
        chosen = _best(accepted, held_slots, compact)
        return {
            "status": "chosen",
            "section": _public(chosen, _chosen_reason(chosen, clashing, rejected, compact)),
            "reason": _chosen_reason(chosen, clashing, rejected, compact),
        }
    if rejected and not unverified and not unknown:
        section, reasons = rejected[0]
        return {
            "status": "not_matched",
            "section": None,
            "reason": f"Every comparable section fails the timetable request ({reasons[0]}).",
        }
    if clashing and not feasible and not unverified:
        clash = clashing[0]["clashes"][0]
        return {
            "status": "not_matched",
            "section": None,
            "reason": (
                f"Every stored section clashes. {clashing[0]['section']} has a {clash['type']} clash."
            ),
        }
    return {
        "status": "unverified",
        "section": None,
        "reason": empty_reason or "A stored section is missing meeting or exam times, so a feasible section cannot be confirmed.",
    }


def _violations(section, held_slots, avoid_hours, free_days, avoid_gaps) -> list[str] | None:
    slots = section.get("slots") or []
    needs_times = bool(avoid_hours or free_days or avoid_gaps)
    if needs_times and not slots:
        return None
    reasons = []
    hours = {slot["hour"] for slot in slots}
    days = {slot["day"] for slot in slots}
    blocked = sorted(avoid_hours & hours)
    if blocked:
        reasons.append(f"meets at hour {blocked[0]}")
    shared_days = sorted(free_days & days)
    if shared_days:
        reasons.append(f"meets on {shared_days[0]}")
    if avoid_gaps and _gap(list(slots) + list(held_slots)) >= _LONG_GAP:
        reasons.append("leaves a gap of two or more hours on one day")
    return reasons


def _best(sections: list[dict], held_slots: list[dict], compact: bool) -> dict:
    if not compact:
        return sections[0]

    def span(section: dict) -> tuple:
        hours = [slot["hour"] for slot in (section.get("slots") or []) + held_slots]
        width = max(hours) - min(hours) if hours else 99
        return (width, section.get("section") or "")

    return min(sections, key=span)


def _chosen_reason(chosen, clashing, rejected, compact) -> str:
    parts = [f"Section {chosen['section']} is feasible"]
    if chosen.get("days_hours_raw"):
        parts[0] += f" ({chosen['days_hours_raw']})"
    parts[0] += "."
    if compact:
        parts.append("It is the most compact stored section that meets the other timetable requests.")
    if clashing:
        sample = clashing[0]
        kind = sample["clashes"][0]["type"] if sample.get("clashes") else "meeting"
        parts.append(f"Section {sample['section']} was not used because it has a {kind} clash.")
    if rejected:
        sample, reasons = rejected[0]
        parts.append(f"Section {sample['section']} was not used because it {reasons[0]}.")
    return " ".join(parts)


def _public(section: dict, reason: str) -> dict:
    return {
        "section": section.get("section"),
        "section_kind": section.get("section_kind"),
        "days_hours_raw": section.get("days_hours_raw"),
        "room": section.get("room"),
        "source_page": section.get("source_page"),
        "reason": reason,
    }


def _gap(slots: list[dict]) -> int:
    by_day: dict[str, list[int]] = {}
    for slot in slots:
        by_day.setdefault(slot["day"], []).append(int(slot["hour"]))
    worst = 0
    for hours in by_day.values():
        ordered = sorted(set(hours))
        for left, right in zip(ordered, ordered[1:]):
            worst = max(worst, right - left - 1)
    return worst


def _held_slots(held_sections: list[dict], catalog: dict | None) -> list[dict]:
    if catalog is None:
        return []
    slots = []
    for ref in held_sections:
        code = ref.get("course_code")
        name = ref.get("section")
        for row in catalog.get(code, []):
            if row["section"] == name and not row["cancelled"]:
                slots.extend(row.get("slots") or [])
    return slots


def _empty_reason(assessed: dict) -> str | None:
    for note in assessed.get("warnings") or []:
        if note.startswith("No timetable"):
            return note
    return None
