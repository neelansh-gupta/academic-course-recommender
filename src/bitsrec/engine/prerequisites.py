"""Prerequisite checks from stored description text.

A description with no prerequisite line means none were printed. No description
means the prerequisite is unverified. Alternatives joined by "or" are options.
Codes inside one alternative are all required. A slash group such as
ECE/EEE/INSTR F211 is one option with several departments.
"""

from __future__ import annotations

import re

from bitsrec.ingest.codes import find_codes, normalize_code

_SLASH_DEPARTMENTS = re.compile(
    r"((?:[A-Z]{2,6}/)+[A-Z]{2,6})\s+([A-Z]\d{3}[A-Z]?(?:-\d+)?)"
)
_SPACED_SLASH = re.compile(
    r"([A-Z]{2,6}\s+[A-Z]\d{3}[A-Z]?(?:-\d+)?"
    r"(?:\s*/\s*[A-Z]{2,6}\s+[A-Z]\d{3}[A-Z]?(?:-\d+)?)+)"
)
_OR_SPLIT = re.compile(r"\s+or\s+", re.IGNORECASE)
_SENTENCE = re.compile(r"\.\s+(?=[A-Z][a-z])")


def prerequisite_status(
    text: str | None,
    codes: list[str] | tuple[str, ...],
    *,
    description_present: bool,
    completed: set[str],
    equivalent,
) -> str:
    stored = [code for code in codes if code]
    if not text and not stored:
        return "none_stated" if description_present else "unverified"
    clause = _first_sentence(text) if text else ""
    branches = [part.strip() for part in _OR_SPLIT.split(clause) if part.strip()] if clause else []
    if not branches and stored:
        branches = [""]
    if not branches:
        return "unverified"

    def holds(code: str) -> bool:
        return any(equivalent(code, done) for done in completed)

    parsed = False
    for branch in branches:
        groups = _slash_groups(branch)
        grouped = {code for group in groups for code in group}
        atomic = [code for code in find_codes(branch) if code not in grouped]
        if not groups and not atomic:
            if len(branches) == 1 and stored:
                atomic = list(stored)
            else:
                continue
        parsed = True
        groups_ok = all(any(holds(option) for option in group) for group in groups)
        atomic_ok = all(holds(code) for code in atomic)
        if groups_ok and atomic_ok:
            return "satisfied"
    if not parsed:
        return "unverified"
    return "not_satisfied"


def _first_sentence(text: str) -> str:
    return _SENTENCE.split(text.strip(), maxsplit=1)[0]


def _slash_groups(text: str) -> list[list[str]]:
    groups: list[list[str]] = []
    spans: list[tuple[int, int]] = []
    for match in _SLASH_DEPARTMENTS.finditer(text):
        number = match.group(2)
        groups.append([normalize_code(department, number) for department in match.group(1).split("/")])
        spans.append(match.span())
    for match in _SPACED_SLASH.finditer(text):
        if any(match.start() < end and match.end() > start for start, end in spans):
            continue
        codes = find_codes(match.group(1).replace("/", " "))
        if len(codes) >= 2:
            groups.append(codes)
    return groups
