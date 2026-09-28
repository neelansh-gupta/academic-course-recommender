"""Course-code normalization. Codes are recognized, never invented."""

from __future__ import annotations

import re

# Department token, then a level letter and three digits, optional suffix.
# Examples: CS F301, BITS F421T, MATH U101, BITS F101-1, EEE G516.
COURSE_CODE_RE = re.compile(
    r"\b([A-Z]{2,6})\s+([A-Z]\d{3}[A-Z]?(?:-\d+)?)\b"
)

FILENAME_CODE_RE = re.compile(
    r"^\d+_([A-Z]{2,6})_([A-Z]\d{3}[A-Z0-9-]*)\.pdf$",
    re.IGNORECASE,
)

# Placeholder used in the bulletin for project-type courses of any discipline.
PATTERN_DEPARTMENTS = {"XXX"}


def normalize_code(department: str, number: str) -> str:
    dept = re.sub(r"[^A-Z]", "", department.upper())
    num = number.upper().replace(" ", "")
    return f"{dept} {num}"


def department_of(course_code: str) -> str:
    return course_code.split()[0]


def level_of(course_code: str) -> str | None:
    parts = course_code.split()
    if len(parts) < 2 or not parts[1]:
        return None
    return parts[1][0]


def find_codes(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for dept, number in COURSE_CODE_RE.findall(text or ""):
        if dept in PATTERN_DEPARTMENTS:
            continue
        code = normalize_code(dept, number)
        if code not in seen:
            seen.add(code)
            found.append(code)
    return found


def code_from_filename(name: str) -> str | None:
    match = FILENAME_CODE_RE.match(Path_name(name))
    if not match:
        return None
    return normalize_code(match.group(1), match.group(2))


def Path_name(name: str) -> str:
    return name.replace("\\", "/").rsplit("/", 1)[-1]


def parse_hours(token: str | None) -> float | None:
    if token is None:
        return None
    raw = token.strip().rstrip("*")
    if raw in {"", "-"}:
        return None
    try:
        return float(raw)
    except ValueError:
        return None
