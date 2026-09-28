"""Course-handout parser.

Missing evaluation facts stay null and are listed in unverified_fields.
A property is recorded only when the handout text states it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from bitsrec.ingest.codes import code_from_filename, find_codes, normalize_code
from bitsrec.ingest.pdfutil import open_pdf

HEADER_FIELDS = {
    "course_codes": re.compile(
        r"(?:course\s*(?:no|number|code)|course\s*no\.)\s*[:\-]\s*(.+)",
        re.IGNORECASE,
    ),
    "title": re.compile(r"course\s*title\s*[:\-]\s*(.+)", re.IGNORECASE),
    "instructor": re.compile(
        r"instructor[-\s]*in[-\s]*charge\s*[:\-]\s*(.+)",
        re.IGNORECASE,
    ),
}

EVAL_START_RE = re.compile(
    r"(?:evaluation\s+scheme|evaluation\s+plan|scheme\s+of\s+evaluation)\s*:?",
    re.IGNORECASE,
)
SECTION_BREAK_RE = re.compile(
    r"^(?:\d+\.\s*)?(?:make-?up\s*policy|office\s+consultation|chamber\s+consultation|"
    r"notices|general\s+instructions|nc\s+criterion|criterion\s+for\s+nc)\b",
    re.IGNORECASE,
)
MAKEUP_RE = re.compile(
    r"(?:make-?up\s*policy)\s*:?\s*(.*)",
    re.IGNORECASE,
)
WEIGHT_RE = re.compile(r"^(\d{1,3}(?:\.\d+)?)\s*%?$")
HEADER_NOISE = {
    "component",
    "wt (%)",
    "wt(%)",
    "weightage",
    "duration",
    "date & time",
    "date, time",
    "comments",
    "remarks",
    "evaluation",
    "evaluation component",
    "ec.no.",
    "ec no",
    "s.no",
    "s.no.",
    "sl.no",
    "sl. no.",
}

MIDSEM_RE = re.compile(r"mid[-\s]*sem(?:ester)?", re.IGNORECASE)
COMPRE_RE = re.compile(r"compre(?:hensive)?", re.IGNORECASE)
PROJECT_RE = re.compile(r"\bproject\b|term\s+paper", re.IGNORECASE)
QUIZ_RE = re.compile(r"\bquiz", re.IGNORECASE)
ASSIGNMENT_RE = re.compile(r"\bassignment\b", re.IGNORECASE)


def parse_handout_file(path: Path) -> list[dict]:
    document = open_pdf(str(path))
    try:
        text = "\n".join((page.get_text("text") or "") for page in document)
    finally:
        document.close()
    return parse_handout_text(text, path.name)


def parse_handout_text(text: str, source_name: str) -> list[dict]:
    cleaned = text.replace("\x00", "")
    low_text = len(cleaned.strip()) < 80
    filename_code = code_from_filename(source_name)
    codes = _header_codes(cleaned)
    if filename_code and filename_code not in codes:
        codes.insert(0, filename_code)
    if not codes:
        codes = find_codes(cleaned[:1500])[:4]

    record = _parse_body(cleaned, source_name, low_text)
    if not codes:
        record["course_code"] = None
        record["needs_verification"] = 1
        record["unverified_fields"].append("course_code")
        return [record]

    records = []
    for code in codes:
        item = dict(record)
        item["course_code"] = code
        item["unverified_fields"] = list(record["unverified_fields"])
        records.append(item)
    return records


def _header_codes(text: str) -> list[str]:
    head = "\n".join(text.splitlines()[:80])
    codes: list[str] = []
    for line in head.splitlines():
        match = HEADER_FIELDS["course_codes"].search(line)
        if not match:
            continue
        raw = match.group(1)
        raw = raw.replace("[", " ").replace("]", " ")
        for code in find_codes(raw.replace("/", " ")):
            if code not in codes:
                codes.append(code)
        # Slash-joined codes sometimes lose the space the regex expects.
        for piece in re.split(r"[/,]| and ", raw, flags=re.IGNORECASE):
            compact = re.sub(r"\s+", " ", piece).strip(" []")
            found = find_codes(compact)
            if not found:
                m = re.fullmatch(r"([A-Z]{2,6})\s*([A-Z]\d{3}[A-Z]?(?:-\d+)?)", compact)
                if m:
                    found = [normalize_code(m.group(1), m.group(2))]
            for code in found:
                if code not in codes:
                    codes.append(code)
    return codes


def _parse_body(text: str, source_name: str, low_text: bool) -> dict:
    title = _first_field(text, HEADER_FIELDS["title"])
    instructor = _first_field(text, HEADER_FIELDS["instructor"])
    campus = _campus(text)
    semester = _semester(text)
    description = _section_body(
        text,
        r"(?:1\.\s*)?course\s+description\s*:?",
        r"(?:2\.\s*)?(?:scope|objective|text\s*books|course\s+plan|evaluation)",
    )
    evaluation_excerpt, components = _evaluation(text)
    makeup = _makeup(text)
    attendance_excerpt, attendance_status = _attendance(text)

    unverified: list[str] = []
    if low_text:
        unverified.extend(
            ["evaluation", "attendance_policy", "makeup_policy", "instructor", "syllabus"]
        )
    if not evaluation_excerpt:
        unverified.append("evaluation")
    if attendance_status is None:
        unverified.append("attendance_policy")
    if not makeup:
        unverified.append("makeup_policy")
    if not instructor:
        unverified.append("instructor")

    has_midsem = _flag(components, MIDSEM_RE, evaluation_excerpt, low_text)
    has_compre = _flag(components, COMPRE_RE, evaluation_excerpt, low_text)
    has_project = _flag(components, PROJECT_RE, evaluation_excerpt, low_text)
    has_quiz = _flag(components, QUIZ_RE, evaluation_excerpt, low_text)
    has_assignment = _flag(components, ASSIGNMENT_RE, evaluation_excerpt, low_text)

    confidence = 0.25 if low_text else (0.86 if components else 0.55)
    return {
        "source_file": source_name,
        "campus": campus,
        "semester_label": semester,
        "instructor_in_charge": instructor,
        "instructors": _instructors_block(text),
        "description": description,
        "syllabus": _syllabus(text),
        "attendance_excerpt": attendance_excerpt,
        "attendance_status": attendance_status,
        "makeup_policy": makeup,
        "has_midsem": has_midsem,
        "has_compre": has_compre,
        "has_project": has_project,
        "has_quiz": has_quiz,
        "has_assignment": has_assignment,
        "evaluation_json": json.dumps(components, ensure_ascii=False),
        "evaluation_excerpt": evaluation_excerpt,
        "unverified_fields": unverified,
        "confidence": confidence,
        "needs_verification": 1 if low_text or not components else 0,
        "title": _clean_title(title),
    }


def _flag(components: list[dict], pattern: re.Pattern, excerpt: str | None, low_text: bool):
    if low_text or not excerpt:
        return None
    blob = " ".join(component["name"] for component in components) + " " + excerpt
    return 1 if pattern.search(blob) else 0


def _first_field(text: str, pattern: re.Pattern) -> str | None:
    for line in text.splitlines()[:100]:
        match = pattern.search(line)
        if match:
            value = match.group(1).strip(" []")
            return value or None
    return None


def _clean_title(title: str | None) -> str | None:
    if not title:
        return None
    return re.sub(r"\s+", " ", title).strip(" []")


def _campus(text: str) -> str | None:
    head = text[:800].lower()
    for campus in ("pilani", "goa", "hyderabad", "dubai"):
        if campus in head:
            return campus
    return None


def _semester(text: str) -> str | None:
    match = re.search(
        r"(first|second|summer)\s+semester[,\s]+(\d{4}\s*[–-]\s*\d{2,4})",
        text[:1200],
        re.IGNORECASE,
    )
    if not match:
        return None
    return f"{match.group(1).title()} Semester {re.sub(r'\s+', '', match.group(2))}"


def _section_body(text: str, start_pat: str, end_pat: str) -> str | None:
    start = re.search(start_pat, text, re.IGNORECASE)
    if not start:
        return None
    rest = text[start.end() :]
    end = re.search(end_pat, rest, re.IGNORECASE)
    chunk = rest[: end.start()] if end else rest[:1500]
    chunk = re.sub(r"\s+", " ", chunk).strip(" :")
    return chunk[:2000] or None


def _instructors_block(text: str) -> str | None:
    match = re.search(
        r"instructors?\s*[:\-]\s*(.+?)(?:\n\s*\d+\.\s|\n\s*course\s+description)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    value = re.sub(r"\s+", " ", match.group(1)).strip()
    return value[:800] or None


def _syllabus(text: str) -> str | None:
    match = re.search(
        r"(?:course\s+plan|lecture\s+plan|weekly\s+plan)\s*:?(.*)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    chunk = match.group(1)
    stop = EVAL_START_RE.search(chunk)
    if stop:
        chunk = chunk[: stop.start()]
    chunk = re.sub(r"\s+", " ", chunk).strip()
    return chunk[:4000] or None


def _evaluation(text: str) -> tuple[str | None, list[dict]]:
    match = EVAL_START_RE.search(text)
    if not match:
        return None, []
    rest = text[match.end() :]
    lines = []
    for raw in rest.splitlines():
        if SECTION_BREAK_RE.match(raw.strip()):
            break
        lines.append(raw.strip())
        if len(lines) > 80:
            break
    excerpt = "\n".join(line for line in lines if line).strip()
    return (excerpt or None), _components(lines)


def _components(lines: list[str]) -> list[dict]:
    components: list[dict] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        weight = _weight(line)
        inline = _inline_component(line)
        if inline:
            components.append(inline)
            index += 1
            continue
        if weight is None:
            index += 1
            continue
        name = _component_name(lines, index)
        if not name:
            index += 1
            continue
        duration, when, comment, consumed = _component_tail(lines, index + 1)
        components.append(
            {
                "name": name,
                "weight": weight,
                "duration": duration,
                "when": when,
                "comment": comment,
            }
        )
        index = consumed
    return _dedupe_components(components)


def _inline_component(line: str) -> dict | None:
    match = re.match(
        r"^(?:\d+\s+)?([A-Za-z][A-Za-z0-9 /&+\-]{2,50}?)\s+(\d{1,3}(?:\.\d+)?)\s*%?\b(.*)$",
        line,
    )
    if not match:
        return None
    name = match.group(1).strip()
    if name.lower() in HEADER_NOISE or len(name) < 3:
        return None
    weight = float(match.group(2))
    if weight > 100:
        return None
    # "Quiz 1" is a component name. A weighted row has a percent sign or more fields.
    if "%" not in line and not match.group(3).strip():
        return None
    return {"name": name, "weight": weight, "duration": None, "when": None, "comment": match.group(3).strip() or None}


def _weight(line: str) -> float | None:
    match = WEIGHT_RE.match(line.strip())
    if not match:
        return None
    value = float(match.group(1))
    if value <= 0 or value > 100:
        return None
    return value


def _component_name(lines: list[str], weight_index: int) -> str | None:
    parts: list[str] = []
    cursor = weight_index - 1
    while cursor >= 0 and len(parts) < 3:
        line = lines[cursor].strip()
        if not line or _weight(line) is not None or _is_noise(line) or _is_meta(line):
            break
        if re.fullmatch(r"\d{1,2}", line):
            break
        parts.append(line)
        cursor -= 1
    name = " ".join(reversed(parts)).strip()
    name = re.sub(r"\s+", " ", name)
    if len(name) < 3 or name.lower() in HEADER_NOISE:
        return None
    return name


def _component_tail(lines: list[str], start: int) -> tuple[str | None, str | None, str | None, int]:
    duration = when = comment = None
    cursor = start
    while cursor < len(lines):
        line = lines[cursor].strip()
        if not line:
            cursor += 1
            continue
        if _weight(line) is not None or _inline_component(line) or SECTION_BREAK_RE.match(line):
            break
        if _is_noise(line) or re.fullmatch(r"\d{1,2}", line):
            cursor += 1
            continue
        lower = line.lower()
        if duration is None and ("min" in lower or "hour" in lower):
            duration = line
        elif when is None and (_looks_like_date(line) or re.search(r"\b(FN|AN)\d?\b", line)):
            when = line
        elif comment is None and re.search(r"book|open|closed|take[\s-]*home", line, re.IGNORECASE):
            comment = line
        else:
            # A new component name is starting.
            break
        cursor += 1
    return duration, when, comment, cursor


def _is_noise(line: str) -> bool:
    return line.lower().strip(" .:") in HEADER_NOISE


def _is_meta(line: str) -> bool:
    return bool(
        re.search(r"\bmins?\b|\bhours?\b|closed book|open book|january|february|march|april|may|june|july|august|september|october|november|december", line, re.IGNORECASE)
        or re.search(r"\d{1,2}/\d{1,2}", line)
    )


def _looks_like_date(line: str) -> bool:
    return bool(
        re.search(
            r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)",
            line,
            re.IGNORECASE,
        )
        or re.search(r"\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+", line)
        or re.search(r"\d{1,2}/\d{1,2}", line)
    )


def _dedupe_components(components: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    unique = []
    for component in components:
        key = (component["name"].lower(), component["weight"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(component)
    return unique


def _makeup(text: str) -> str | None:
    match = MAKEUP_RE.search(text)
    if not match:
        return None
    rest = text[match.start() :]
    lines = []
    started = False
    for raw in rest.splitlines():
        line = raw.strip()
        if not started:
            inline = MAKEUP_RE.search(line)
            if inline and inline.group(1).strip():
                lines.append(inline.group(1).strip())
            started = True
            continue
        if re.match(r"^\d+\.\s+\S", line) or re.match(r"^instructor[-\s]*in[-\s]*charge", line, re.IGNORECASE):
            break
        if re.search(r"birla institute|please do not print", line, re.IGNORECASE):
            continue
        if line:
            lines.append(line)
        if len(lines) >= 8:
            break
    value = re.sub(r"\s+", " ", " ".join(lines)).strip()
    return value[:1000] or None


def _attendance(text: str) -> tuple[str | None, str | None]:
    sentences = re.split(r"(?<=[.])\s+", re.sub(r"\s+", " ", text))
    hits = [
        sentence.strip()
        for sentence in sentences
        if re.search(r"\battend", sentence, re.IGNORECASE)
    ]
    if not hits:
        return None, None
    excerpt = " ".join(hits[:3])[:800]
    percent = re.search(r"(\d{1,3})\s*%\s*(?:of\s+)?(?:the\s+)?(?:classes|attendance|sessions)", excerpt, re.IGNORECASE)
    if percent is None:
        percent = re.search(r"attendance[^\d]{0,40}(\d{1,3})\s*%", excerpt, re.IGNORECASE)
    if re.search(r"no\s+(?:minimum\s+)?attendance|attendance\s+is\s+not\s+compulsory|not\s+mandatory", excerpt, re.IGNORECASE):
        return excerpt, "not_required"
    if percent:
        return excerpt, f"threshold_{percent.group(1)}_percent"
    if re.search(r"expected to attend|attend all classes|regular", excerpt, re.IGNORECASE):
        return excerpt, "expected_no_numeric_minimum_stated"
    return excerpt, "mentioned_unclassified"
