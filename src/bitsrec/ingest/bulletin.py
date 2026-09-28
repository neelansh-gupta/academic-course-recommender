"""Bulletin parser.

Reads the on-campus bulletin into programme rules, required and elective
courses, humanities-pool courses, minors, and course descriptions.
Layout-sensitive fields that do not match a course code are skipped rather
than guessed.
"""

from __future__ import annotations

import re
from pathlib import Path

from bitsrec.ingest.codes import find_codes, normalize_code, parse_hours
from bitsrec.ingest.pdfutil import column_lines, dehyphenate, lines_from_words, open_pdf, split_x_for, words_of

SOURCE = "bulletin.pdf"

_CATEGORY_LABELS = [
    ("HUEL", "Humanities Electives"),
    ("SCIENCE_FOUNDATION", "Science Foundation"),
    ("MATH_FOUNDATION", "Mathematics Foundation"),
    ("ENGINEERING_FOUNDATION", "Engineering Foundation"),
    ("TECHNICAL_ARTS", "Technical Arts"),
    ("GENERAL_AWARENESS", "General Awareness / Professional Courses"),
    ("CDC", "Core"),
    ("DEL", "Elective"),
    ("OPEL", "Open Electives"),
    ("COURSEWORK", "Course-work Sub-Total"),
    ("PS_OR_THESIS", "PS-I and II"),
    ("DEGREE_TOTAL", "Total"),
]

_COURSE_LINE_RE = re.compile(
    r"^(?:OR\s+)?([A-Z]{2,6})\s+([A-Z]\d{3}[A-Z]?)\s+(\S.*)$"
)
_HEADER_RE = re.compile(
    r"^([A-Z]{2,6})\s+([A-Z]\d{3}[A-Z]?)\s+(\S.*)$"
)
_SLOT_RE = re.compile(
    r"^(?P<kind>Open/Humanities Electives|Humanities Electives|Open Electives|Discipline Electives)"
    r"(?:\s+(?P<spec>.+))?$",
    re.IGNORECASE,
)
_FOOTER_RE = re.compile(
    r"(Discipline Core|Discipline Electives)\s*[-–]\s*(\d+)\s*Units\s*\(?\s*(\d+)\s*Courses",
    re.IGNORECASE,
)
_PREREQ_RE = re.compile(r"pre[\s\-]*requisites?\s*[:\-]?\s*(.*)", re.IGNORECASE)


def parse_bulletin(path: Path) -> dict:
    document = open_pdf(str(path))
    try:
        rules: list[dict] = []
        programme_courses: list[dict] = []
        descriptions: list[dict] = []
        catalog_state = _CatalogState()
        describing = False
        for index, page in enumerate(document):
            text = page.get_text("text") or ""
            page_number = index + 1
            if "category-wise structure of each program" in text:
                rules.extend(_category_structure(text, page_number))
            if "The following courses are needed to meet" in text and "General Institutional Requirement" in text:
                rules.append(_foundation_note(text, page_number))
            if _FOOTER_RE.search(text) and "Semester-wise" in text:
                rules.extend(_discipline_footers(text, page_number))
            semester_page = "Semester-wise" in text and (
                "First Semester" in text or "Admitted to" in text
            )
            if semester_page:
                programme_courses.extend(_semester_page(page, text, page_number))
                if catalog_state.started:
                    catalog_state.closed = True
            elif _is_catalog_page(text, page_number, catalog_state):
                catalog_state.started = True
                programme_courses.extend(_catalog_page(page, page_number, catalog_state))
            if "PART VII" in text:
                describing = False
            elif "Course Description for all On-campus Programmes" in text or describing:
                describing = True
                descriptions.extend(_description_page(page, page_number))
    finally:
        document.close()
    return {
        "source_document": SOURCE,
        "rules": rules,
        "programme_courses": programme_courses,
        "descriptions": descriptions,
    }


class _CatalogState:
    def __init__(self) -> None:
        self.started = False
        self.closed = False
        self.programme = "Unscoped catalogue"
        self.role = "UNSPECIFIED"
        self.minor = False
        self.pending: dict | None = None


def _is_catalog_page(text: str, page_number: int, state: _CatalogState) -> bool:
    if state.closed or page_number < 300:
        return False
    if "PART V" in text and "OFF-CAMPUS" in text:
        state.closed = True
        return False
    if "The list of Discipline Core Courses" in text:
        return True
    return state.started


def _category_structure(text: str, page_number: int) -> list[dict]:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    rules = []
    used = set()
    for category, label in _CATEGORY_LABELS:
        for index, line in enumerate(lines):
            if index in used:
                continue
            if _label_matches(line, label):
                specs = []
                cursor = index + 1
                while cursor < len(lines) and len(specs) < 2:
                    if any(_label_matches(lines[cursor], item[1]) for item in _CATEGORY_LABELS):
                        break
                    if _is_numeric_spec(lines[cursor]):
                        specs.append(lines[cursor])
                    elif lines[cursor] not in {"OR", "or"}:
                        break
                    cursor += 1
                units_raw = specs[0] if specs else None
                courses_raw = specs[1] if len(specs) > 1 else None
                min_units, max_units = _range_pair(units_raw)
                min_courses, max_courses = _int_range(courses_raw)
                rules.append(
                    {
                        "programme": "Integrated First Degree",
                        "degree_family": "Integrated First Degree",
                        "category": category,
                        "min_units": min_units,
                        "max_units": max_units,
                        "min_courses": min_courses,
                        "max_courses": max_courses,
                        "units_raw": units_raw,
                        "courses_raw": courses_raw,
                        "notes": f"Category '{label}' from the integrated first-degree structure.",
                        "source_document": SOURCE,
                        "source_page": page_number,
                        "source_section": "Part IV, structure of integrated first degree programmes",
                        "confidence": 0.93 if units_raw else 0.4,
                    }
                )
                used.add(index)
                break
    for rule in rules:
        if rule["category"] == "PS_OR_THESIS":
            rule["units_raw"] = "25 (PS-I and PS-II) OR 9 to 20 (thesis)"
            rule["courses_raw"] = "2 OR 1"
            rule["notes"] = (
                "Practice School I and II together are 25 units (2 courses), "
                "or a thesis is 9 to 20 units (1 course). "
                "Source: integrated first-degree structure."
            )
            rule["confidence"] = 0.9
    return rules


def _label_matches(line: str, label: str) -> bool:
    normalized = re.sub(r"^\([IVX0-9]+\)\s*", "", line, flags=re.IGNORECASE).strip()
    return normalized.lower().startswith(label.lower())


def _is_numeric_spec(line: str) -> bool:
    return bool(re.search(r"\d", line) and re.fullmatch(r"[\d\sto\(\)min\.\-\*]+", line, re.IGNORECASE))


def _range_pair(raw: str | None) -> tuple[float | None, float | None]:
    if not raw:
        return None, None
    numbers = [float(item) for item in re.findall(r"\d+(?:\.\d+)?", raw)]
    if not numbers:
        return None, None
    if len(numbers) == 1:
        return numbers[0], numbers[0] if "(min)" not in raw.lower() else None
    return numbers[0], numbers[1]


def _int_range(raw: str | None) -> tuple[int | None, int | None]:
    low, high = _range_pair(raw)
    return (int(low) if low is not None else None, int(high) if high is not None else None)


def _foundation_note(text: str, page_number: int) -> dict:
    start = text.find("The following courses are needed")
    chunk = re.sub(r"\s+", " ", text[start : start + 1800]).strip()
    return {
        "programme": "Integrated First Degree",
        "degree_family": "Integrated First Degree",
        "category": "INSTITUTIONAL_REQUIREMENT_NOTES",
        "min_units": None,
        "max_units": None,
        "min_courses": None,
        "max_courses": None,
        "units_raw": None,
        "courses_raw": None,
        "notes": chunk,
        "source_document": SOURCE,
        "source_page": page_number,
        "source_section": "Part IV, general institutional requirement",
        "confidence": 0.9,
    }


def _discipline_footers(text: str, page_number: int) -> list[dict]:
    programme = _programme_heading(text)
    rows = []
    for match in _FOOTER_RE.finditer(text):
        label = match.group(1).lower()
        category = "CDC" if "core" in label else "DEL"
        units = float(match.group(2))
        courses = int(match.group(3))
        rows.append(
            {
                "programme": programme,
                "degree_family": programme,
                "category": category,
                "min_units": units,
                "max_units": units,
                "min_courses": courses,
                "max_courses": courses,
                "units_raw": match.group(2),
                "courses_raw": match.group(3),
                "notes": match.group(0),
                "source_document": SOURCE,
                "source_page": page_number,
                "source_section": "Semester-wise pattern footer",
                "confidence": 0.9,
            }
        )
    return rows


def _programme_heading(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]
    collected: list[str] = []
    capture = False
    for line in lines:
        if re.search(r"semester-wise", line, re.IGNORECASE):
            capture = True
        if not capture:
            continue
        if line.lower() in {"year", "first semester", "u"} or line.startswith("IV-"):
            if collected:
                break
            continue
        collected.append(line)
        if len(collected) >= 4:
            break
    heading = re.sub(r"\s+", " ", " ".join(collected))
    heading = re.sub(r"\bIV-\d+\b", "", heading).strip(" -")
    paren = re.search(r"\(([^)]+)\)", heading)
    if paren and "with" in paren.group(1).lower():
        return paren.group(1).strip()
    admitted = re.search(r"[Aa]dmitted to\s+(.+)", heading)
    if admitted:
        return re.sub(r"\s+Programme$", "", admitted.group(1)).strip()
    return heading or "Unscoped programme"


def _semester_page(page, text: str, page_number: int) -> list[dict]:
    programme = _programme_heading(text)
    rows: list[dict] = []
    for semester, words in (
        ("first", [word for word in words_of(page) if word[0] < 256 and word[1] > 70]),
        ("second", [word for word in words_of(page) if word[0] >= 256 and word[1] > 70]),
    ):
        rows.extend(_semester_column(lines_from_words(words, y_tol=5.5), programme, semester, page_number))
    return rows


def _semester_column(lines: list[dict], programme: str, semester: str, page_number: int) -> list[dict]:
    rows: list[dict] = []
    year = None
    last: dict | None = None
    for line in lines:
        text = line["text"]
        if re.fullmatch(r"[IVX]{1,4}", text):
            year = text
            last = None
            continue
        prefixed = re.match(
            r"^([IVX]{1,4})\s+((?:[A-Z]{2,6}\s+[A-Z]\d{3}|Humanities|Open|Discipline|Summer)\b.*)$",
            text,
        )
        if prefixed:
            year = prefixed.group(1)
            text = prefixed.group(2)
        if text.lower().startswith("same as "):
            last = None
            continue
        if re.fullmatch(r"\d+\*?(?:\(min\))?", text):
            continue
        slot = _SLOT_RE.match(text)
        if slot:
            rows.append(_slot_row(slot, programme, year, semester, page_number))
            last = None
            continue
        match = _COURSE_LINE_RE.match(text)
        if match and match.group(1) not in {"OR"}:
            title, lecture, practical, units = _split_lpu(match.group(3))
            last = {
                "programme": programme,
                "course_code": normalize_code(match.group(1), match.group(2)),
                "role": "semester_required",
                "year_label": year,
                "semester_label": semester,
                "units": units,
                "title": title,
                "source_document": SOURCE,
                "source_page": page_number,
                "source_section": "Semester-wise pattern",
                "confidence": 0.74,
                "notes": None,
                "lecture_hours": lecture,
                "practical_hours": practical,
            }
            rows.append(last)
            continue
        if last is not None and text and not find_codes(text) and len(text) < 80:
            if text.lower() not in {"or", "u", "year"}:
                last["title"] = f"{last['title']} {text}".strip()
    return rows


def _slot_row(slot: re.Match, programme: str, year: str | None, semester: str, page_number: int) -> dict:
    kind = slot.group("kind").lower()
    if kind.startswith("open/humanities"):
        role = "HUEL_OR_OPEL"
    elif kind.startswith("humanities"):
        role = "HUEL"
    elif kind.startswith("open"):
        role = "OPEL"
    else:
        role = "DEL"
    spec = slot.group("spec") or ""
    return {
        "programme": programme,
        "course_code": f"SLOT {role}",
        "role": role,
        "year_label": year,
        "semester_label": semester,
        "units": _range_pair(spec)[0],
        "title": slot.group(0),
        "source_document": SOURCE,
        "source_page": page_number,
        "source_section": "Semester-wise pattern slot",
        "confidence": 0.8,
        "notes": spec or None,
        "lecture_hours": None,
        "practical_hours": None,
    }


def _catalog_page(page, page_number: int, state: _CatalogState) -> list[dict]:
    rows: list[dict] = []
    split = split_x_for(page)
    for column in column_lines(page, split, y_tol=2.2):
        if not column:
            continue
        origin = min(line["words"][0][0] for line in column)
        for line in column:
            text = re.sub(r"\s+", " ", line["text"]).strip()
            if not text or re.fullmatch(r"IV-\d+", text) or re.fullmatch(r"\d{1,3}", text):
                continue
            if re.fullmatch(r"L\s*P\s*U", text, flags=re.IGNORECASE):
                continue
            x0 = line["words"][0][0]
            header = _catalog_header(text, state, x0, origin)
            if header:
                if state.pending:
                    rows.append(state.pending)
                    state.pending = None
                continue
            match = _COURSE_LINE_RE.match(text)
            if match and match.group(1) != "OR":
                if state.pending:
                    rows.append(state.pending)
                title, lecture, practical, units = _split_lpu(match.group(3))
                alternative = text.startswith("OR ")
                state.pending = {
                    "programme": state.programme,
                    "course_code": normalize_code(match.group(1), match.group(2)),
                    "role": state.role,
                    "year_label": None,
                    "semester_label": None,
                    "units": units,
                    "title": title,
                    "source_document": SOURCE,
                    "source_page": page_number,
                    "source_section": "Bulletin course catalogue",
                    "confidence": 0.86 if state.role != "UNSPECIFIED" else 0.55,
                    "notes": "Listed as an alternative to the previous course." if alternative else None,
                    "lecture_hours": lecture,
                    "practical_hours": practical,
                }
                continue
            if state.pending and not find_codes(text) and len(text) < 90 and text.upper() == text:
                # Wrapped title fragments in this catalogue are printed in capitals
                # only sometimes; keep short non-code lines that look like titles.
                if not _looks_like_prose(text):
                    state.pending["title"] = f"{state.pending['title']} {text}".strip()
            elif state.pending and not find_codes(text) and len(text) < 70 and not _looks_like_prose(text):
                state.pending["title"] = f"{state.pending['title']} {text}".strip()
        if state.pending:
            rows.append(state.pending)
            state.pending = None
    return rows


def _looks_like_prose(text: str) -> bool:
    if len(text.split()) > 12:
        return True
    return text.lower().startswith(("the ", "this ", "a student", "students ", "description"))


def _catalog_header(text: str, state: _CatalogState, x0: float, origin: float) -> bool:
    lowered = text.lower()
    if "list of audit type courses" in lowered:
        state.programme = "Audit Type Courses"
        state.role = "AUDIT"
        state.minor = False
        return True
    if "pool of humanities courses" in lowered:
        state.programme = "Humanities Elective Pool"
        state.role = "HUEL"
        state.minor = False
        return True
    if lowered.startswith("minor in "):
        state.programme = text
        state.role = "MINOR_UNSPECIFIED"
        state.minor = True
        return True
    if "discipline elective" in lowered:
        state.role = "DEL"
        return True
    if lowered.startswith("core courses") or text == "CORE COURSES" or "core courses" in lowered:
        state.role = "MINOR_CORE" if state.minor else "CDC"
        return True
    if lowered.startswith("electives"):
        state.role = "MINOR_ELECTIVE" if state.minor else "DEL"
        return True
    if _is_programme_header(text, x0, origin):
        state.programme = text.title() if text.isupper() else text
        state.role = "UNSPECIFIED"
        state.minor = False
        return True
    return False


def _is_programme_header(text: str, x0: float, origin: float) -> bool:
    # Wrapped course titles are indented. Programme names share the course-code margin.
    if x0 > origin + 28:
        return False
    if find_codes(text) or _COURSE_LINE_RE.match(text):
        return False
    if len(text) < 8 or len(text) > 80:
        return False
    letters = re.sub(r"[^A-Za-z]", "", text)
    if len(letters) < 8:
        return False
    upper_ratio = sum(1 for char in letters if char.isupper()) / len(letters)
    if upper_ratio < 0.85:
        return False
    blocked = ("core courses", "elective", "l p u", "course no", "course title")
    if any(token in text.lower() for token in blocked):
        return False
    return True


def _split_lpu(title: str) -> tuple[str, float | None, float | None, float | None]:
    three = re.match(
        r"^(?P<title>.+?)\s+(?P<l>\d+\*?|-)\s+(?P<p>\d+\*?|-)\s+(?P<u>\d+\*?)$",
        title,
    )
    if three and three.group("title"):
        return (
            three.group("title").strip(),
            parse_hours(three.group("l")),
            parse_hours(three.group("p")),
            parse_hours(three.group("u")),
        )
    one = re.match(r"^(?P<title>.+?[A-Za-z)])\s+(?P<u>\d+\*?)$", title)
    if one:
        return one.group("title").strip(), None, None, parse_hours(one.group("u"))
    return title.strip(), None, None, None


def _description_page(page, page_number: int) -> list[dict]:
    rows: list[dict] = []
    for column in column_lines(page, split_x_for(page), y_tol=2.0):
        current: dict | None = None
        for line in column:
            text = line["text"]
            if _is_description_chrome(text):
                continue
            match = _HEADER_RE.match(text)
            if match and match.group(1) not in {"PRE"} and not text.lower().startswith("pre"):
                if current:
                    rows.append(_finish_description(current))
                title, lecture, practical, units = _split_lpu(match.group(3))
                current = {
                    "course_code": normalize_code(match.group(1), match.group(2)),
                    "title": title,
                    "lecture_hours": lecture,
                    "practical_hours": practical,
                    "units": units,
                    "lines": [],
                    "source_page": page_number,
                }
                continue
            if current is not None:
                current["lines"].append(text)
        if current:
            rows.append(_finish_description(current))
    return rows


def _is_description_chrome(text: str) -> bool:
    if re.fullmatch(r"VI-\d+", text) or re.fullmatch(r"\d{1,3}", text):
        return True
    if text.startswith("Course Description for all"):
        return True
    return False


def _finish_description(current: dict) -> dict:
    body = dehyphenate(current["lines"])
    prereq = None
    match = _PREREQ_RE.search(body)
    if match:
        prereq = match.group(1).strip() or None
        body = body[: match.start()].strip()
    return {
        "course_code": current["course_code"],
        "title": current["title"],
        "lecture_hours": current["lecture_hours"],
        "practical_hours": current["practical_hours"],
        "units": current["units"],
        "description": body[:4000] or None,
        "prerequisites_text": prereq,
        "prerequisite_codes": find_codes(prereq or ""),
        "source_document": SOURCE,
        "source_page": current["source_page"],
        "source_section": "Part VI course descriptions",
        "confidence": 0.78 if body else 0.45,
    }
