"""First-semester timetable parser.

Rows are read from word coordinates. A course is kept only when a computer
code and a course number are both present. Exam slots and meeting times are
left empty when the row does not print them.
"""

from __future__ import annotations

import re
from pathlib import Path

from bitsrec.ingest.codes import find_codes, normalize_code
from bitsrec.ingest.pdfutil import lines_from_words, open_pdf, words_of

DAY_MAP = {
    "M": "Mon",
    "T": "Tue",
    "W": "Wed",
    "Th": "Thu",
    "F": "Fri",
    "S": "Sat",
}
SECTION_RE = re.compile(r"^[LTP]\d{1,2}[A-Z]{0,4}$")
SESSION_RE = re.compile(r"^(FN1|FN2|AN1|AN2|FN|AN)$")
DATE_RE = re.compile(r"^\d{2}/\d{2}$")
COM_RE = re.compile(r"^\d{3,5}$")
NUMBER_RE = re.compile(r"^[A-Z]\d{3}[A-Z]?(?:-\d+)?$")
DEPT_RE = re.compile(r"^[A-Z]{2,6}$")
COHORT_NOTE = (
    "Courses with com cod >= 5000 are meant only for 2026 admissions "
    "into FD, HD and PHD and not for others."
)


def parse_timetable(path: Path) -> dict:
    document = open_pdf(str(path))
    try:
        sections: list[dict] = []
        equivalents: list[dict] = []
        mode = "seek"
        for index, page in enumerate(document):
            text = page.get_text("text") or ""
            if "COURSEWISE TIMETABLE" in text:
                mode = "courses"
            elif "LIST OF EQUIVALENT COURSES" in text or "LIST OF EQUIVALENT" in text:
                mode = "equivalents"
            elif mode == "equivalents" and "LIBRARY AND BITS COOP" in text:
                mode = "done"
            if mode == "courses":
                sections.extend(_page_sections(page, index + 1))
            elif mode == "equivalents":
                equivalents.extend(_page_equivalents(page, index + 1))
    finally:
        document.close()
    return {
        "source_document": path.name,
        "sections": sections,
        "equivalents": equivalents,
        "cohort_note": COHORT_NOTE,
    }


def _page_sections(page, page_number: int) -> list[dict]:
    lines = lines_from_words(words_of(page), y_tol=2.0)
    sections: list[dict] = []
    current_course: dict | None = None
    current: dict | None = None
    for line in lines:
        if _is_chrome(line["text"]):
            continue
        parsed = _parse_course_line(line)
        if parsed:
            current_course = parsed
            current = _new_section(parsed, page_number)
            sections.append(current)
            continue
        if current_course is None:
            continue
        title = _title_fragment(line)
        if title and current and not current.get("title"):
            current["title"] = title
            current_course["title"] = title
            continue
        section = _parse_section_line(line, current_course, page_number)
        if section:
            current = section
            sections.append(current)
            continue
        extra = _instructor_fragment(line)
        if extra and current is not None:
            current["extra_instructors"].append(extra)
    for section in sections:
        instructors = [section["instructor"]] if section.get("instructor") else []
        instructors.extend(section.pop("extra_instructors", []))
        section["instructors_json"] = instructors
        section["instructor"] = "; ".join(instructors) if instructors else None
    return sections


def _is_chrome(text: str) -> bool:
    upper = text.upper()
    if "COURSEWISE TIMETABLE" in upper or upper.startswith("NOTE:"):
        return True
    if "COM COD" in upper or "COURSE NO" in upper or "INSTRUCTOR-IN-CHARGE" in upper:
        return True
    if upper in {"CREDIT", "MIDSEM", "COMPRE"}:
        return True
    return False


def _parse_course_line(line: dict) -> dict | None:
    words = line["words"]
    com = next((w for w in words if w[0] < 75 and COM_RE.match(w[4])), None)
    number = next((w for w in words if 90 <= w[0] < 140 and NUMBER_RE.match(w[4])), None)
    dept = next((w for w in words if 70 <= w[0] < 110 and DEPT_RE.match(w[4])), None)
    if not com or not number or not dept:
        return None
    course_code = normalize_code(dept[4], number[4])
    title_words = [w[4] for w in words if 130 <= w[0] < 300 and w is not number and w is not dept]
    credits = _credits(words)
    section = _section_token(words)
    tail = _tail(words)
    return {
        "com_code": int(com[4]),
        "course_code": course_code,
        "title": " ".join(title_words).strip() or None,
        "section": section,
        **credits,
        **tail,
    }


def _credits(words: list) -> dict:
    band = [w for w in words if 290 <= w[0] < 400 and re.fullmatch(r"\d+|-|\d+\*", w[4])]
    band.sort(key=lambda w: w[0])
    values = [_num(w[4]) for w in band[:5]]
    while len(values) < 5:
        values.append(None)
    lecture, practical, tutorial, self_study, units = values
    return {
        "lecture_hours": lecture,
        "practical_hours": practical,
        "tutorial_hours": tutorial,
        "self_study_hours": self_study,
        "units": units,
    }


def _num(token: str) -> float | None:
    if token == "-":
        return None
    try:
        return float(token.rstrip("*"))
    except ValueError:
        return None


def _section_token(words: list) -> str | None:
    for word in words:
        if 385 <= word[0] < 460 and SECTION_RE.match(word[4]):
            return word[4]
    return None


def _tail(words: list) -> dict:
    instructor = [w[4] for w in words if 430 <= w[0] < 575]
    room = next((w[4] for w in words if 560 <= w[0] < 635 and (re.fullmatch(r"\d{3,4}", w[4]) or w[4].upper() in {"CANCLED", "CANCELLED"})), None)
    day_tokens = [w[4] for w in words if 625 <= w[0] < 695]
    exams = [w[4] for w in words if w[0] >= 690 and (DATE_RE.match(w[4]) or SESSION_RE.match(w[4]))]
    midsem_date, midsem_session, compre_date, compre_session = _exams(exams)
    cancelled = 1 if room and room.upper() in {"CANCLED", "CANCELLED"} else 0
    if any(token.upper() in {"CANCLED", "CANCELLED"} for token in instructor):
        cancelled = 1
    return {
        "instructor": " ".join(instructor).strip() or None,
        "room": None if cancelled else room,
        "days_hours_raw": " ".join(day_tokens).strip() or None,
        "slots_json": _slots(day_tokens),
        "midsem_date": midsem_date,
        "midsem_session": midsem_session,
        "compre_date": compre_date,
        "compre_session": compre_session,
        "cancelled": cancelled,
    }


def _exams(tokens: list[str]) -> tuple[str | None, str | None, str | None, str | None]:
    dates = [token for token in tokens if DATE_RE.match(token)]
    sessions = [token for token in tokens if SESSION_RE.match(token)]
    def pick(index: int, pool: list[str]) -> str | None:
        return pool[index] if index < len(pool) else None
    return pick(0, dates), pick(0, sessions), pick(1, dates), pick(1, sessions)


def _slots(tokens: list[str]) -> list[dict]:
    slots: list[dict] = []
    day_run: list[str] = []
    last_was_hour = False
    for token in tokens:
        if token in DAY_MAP:
            if last_was_hour:
                day_run = [token]
            else:
                day_run.append(token)
            last_was_hour = False
            continue
        if re.fullmatch(r"\d{1,2}", token) and day_run:
            hour = int(token)
            for day in day_run:
                slots.append({"day": DAY_MAP[day], "hour": hour})
            last_was_hour = True
            continue
        last_was_hour = False
    return slots


def _title_fragment(line: dict) -> str | None:
    words = line["words"]
    if any(w[0] < 75 and COM_RE.match(w[4]) for w in words):
        return None
    if _section_token(words):
        return None
    text_words = [w[4] for w in words if 120 <= w[0] < 320]
    text = " ".join(text_words).strip()
    if not text or text.lower() in {"tutorial", "practical"}:
        return None
    if NUMBER_RE.match(text_words[0]) or DEPT_RE.match(text_words[0]):
        return None
    return text


def _parse_section_line(line: dict, course: dict, page_number: int) -> dict | None:
    section = _section_token(line["words"])
    if not section:
        return None
    if any(w[0] < 75 and COM_RE.match(w[4]) for w in line["words"]):
        return None
    tail = _tail(line["words"])
    kind = "lecture" if section.startswith("L") else "tutorial" if section.startswith("T") else "practical"
    return {
        "com_code": course["com_code"],
        "course_code": course["course_code"],
        "title": course.get("title"),
        "section": section,
        "section_kind": kind,
        "lecture_hours": course.get("lecture_hours"),
        "practical_hours": course.get("practical_hours"),
        "tutorial_hours": course.get("tutorial_hours"),
        "self_study_hours": course.get("self_study_hours"),
        "units": course.get("units"),
        "cohort_rule": "2026_admissions_only" if course["com_code"] >= 5000 else None,
        "source_page": page_number,
        "confidence": 0.8,
        "extra_instructors": [],
        **tail,
    }


def _new_section(parsed: dict, page_number: int) -> dict:
    section = parsed.get("section") or "L1"
    kind = "lecture" if section.startswith("L") else "tutorial" if section.startswith("T") else "practical"
    return {
        "com_code": parsed["com_code"],
        "course_code": parsed["course_code"],
        "title": parsed.get("title"),
        "section": section,
        "section_kind": kind,
        "lecture_hours": parsed.get("lecture_hours"),
        "practical_hours": parsed.get("practical_hours"),
        "tutorial_hours": parsed.get("tutorial_hours"),
        "self_study_hours": parsed.get("self_study_hours"),
        "units": parsed.get("units"),
        "instructor": parsed.get("instructor"),
        "room": parsed.get("room"),
        "days_hours_raw": parsed.get("days_hours_raw"),
        "slots_json": parsed.get("slots_json") or [],
        "midsem_date": parsed.get("midsem_date"),
        "midsem_session": parsed.get("midsem_session"),
        "compre_date": parsed.get("compre_date"),
        "compre_session": parsed.get("compre_session"),
        "cancelled": parsed.get("cancelled", 0),
        "cohort_rule": "2026_admissions_only" if parsed["com_code"] >= 5000 else None,
        "source_page": page_number,
        "confidence": 0.84,
        "extra_instructors": [],
    }


def _instructor_fragment(line: dict) -> str | None:
    words = line["words"]
    if _section_token(words) or any(w[0] < 80 and COM_RE.match(w[4]) for w in words):
        return None
    names = [w[4] for w in words if 420 <= w[0] < 580]
    text = " ".join(names).strip()
    if not text or text.lower() in {"tutorial", "practical"}:
        return None
    if len(text) < 3:
        return None
    return text


def _page_equivalents(page, page_number: int) -> list[dict]:
    rows = []
    for line in lines_from_words(words_of(page), y_tol=2.0):
        if "EQUIVALENT" in line["text"].upper() or "COURSE NO" in line["text"].upper():
            continue
        codes = find_codes(line["text"])
        if len(codes) < 2:
            continue
        primary = codes[0]
        for equivalent in codes[1:]:
            if equivalent == primary:
                continue
            rows.append(
                {
                    "course_code": primary,
                    "equivalent_code": equivalent,
                    "source_document": "timetable.pdf",
                    "source_page": page_number,
                }
            )
    return rows
