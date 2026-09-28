"""Turn a preference sentence into checks the stored records can answer.

A named evaluation field is used only when the sentence seeks it or avoids it.
Category words such as DEL limit the eligible set. A qualitative makeup
judgment stays a makeup check rather than a text search. Timetable phrases
become section constraints. A bare word such as "midsem" stays unrecognized.
"""

from __future__ import annotations

import re

_STOP = {
    "a",
    "an",
    "and",
    "about",
    "avoid",
    "based",
    "class",
    "classes",
    "course",
    "courses",
    "evaluation",
    "find",
    "finding",
    "finds",
    "for",
    "free",
    "has",
    "have",
    "having",
    "i",
    "in",
    "include",
    "includes",
    "including",
    "interest",
    "interested",
    "interests",
    "keep",
    "keeping",
    "lenient",
    "me",
    "my",
    "need",
    "needing",
    "needs",
    "of",
    "on",
    "or",
    "please",
    "policy",
    "policies",
    "prefer",
    "prefers",
    "related",
    "show",
    "strict",
    "suggest",
    "suggested",
    "suggesting",
    "that",
    "the",
    "to",
    "want",
    "wants",
    "wanting",
    "weekday",
    "with",
    "without",
}
_SHORT_OK = {"ai", "ml"}
_BARE_PROPERTIES = {
    "assignment",
    "assignments",
    "attendance",
    "compre",
    "comprehensive",
    "makeup",
    "makeups",
    "midsem",
    "midsemester",
    "project",
    "projects",
    "quiz",
    "quizzes",
}
_DAYS = {
    "monday": "Mon",
    "tuesday": "Tue",
    "wednesday": "Wed",
    "thursday": "Thu",
    "friday": "Fri",
    "saturday": "Sat",
}
_DAY_PATTERN = "|".join(_DAYS)

_PATTERNS: tuple[tuple[re.Pattern[str], str, object], ...] = (
    (
        re.compile(
            r"\b(?:no|without)\s+(?:a\s+|an\s+|minimum\s+)?attendance(?:\s+requirement)?\b"
            r"|\battendance\s+(?:is\s+)?not\s+(?:required|compulsory|mandatory)\b",
            re.IGNORECASE,
        ),
        "attendance_status",
        "not_required",
    ),
    (
        re.compile(
            r"\b(?:no|without|avoid(?:ing)?)\s+(?:a\s+|an\s+)?mid[-\s]?sem(?:ester)?s?\b",
            re.IGNORECASE,
        ),
        "has_midsem",
        False,
    ),
    (
        re.compile(
            r"\b(?:with|has|having|include(?:s|ing)?|want(?:s|ing)?)\s+(?:a\s+|an\s+)?mid[-\s]?sem(?:ester)?s?\b",
            re.IGNORECASE,
        ),
        "has_midsem",
        True,
    ),
    (
        re.compile(
            r"\b(?:no|without|avoid(?:ing)?)\s+(?:a\s+|an\s+)?(?:comprehensive(?:\s+exam)?|compre)\b",
            re.IGNORECASE,
        ),
        "has_compre",
        False,
    ),
    (
        re.compile(
            r"\b(?:with|has|having|include(?:s|ing)?|want(?:s|ing)?)\s+(?:a\s+|an\s+)?(?:comprehensive(?:\s+exam)?|compre)\b",
            re.IGNORECASE,
        ),
        "has_compre",
        True,
    ),
    (
        re.compile(r"\bproject[-\s]?based\b", re.IGNORECASE),
        "has_project",
        True,
    ),
    (
        re.compile(
            r"\b(?:no|without|avoid(?:ing)?)\s+(?:a\s+|an\s+)?projects?\b",
            re.IGNORECASE,
        ),
        "has_project",
        False,
    ),
    (
        re.compile(
            r"\b(?:with|has|having|include(?:s|ing)?|want(?:s|ing)?)\s+(?:a\s+|an\s+)?projects?\b",
            re.IGNORECASE,
        ),
        "has_project",
        True,
    ),
    (
        re.compile(
            r"\b(?:no|without|avoid(?:ing)?)\s+(?:a\s+|an\s+)?quizz?es?\b",
            re.IGNORECASE,
        ),
        "has_quiz",
        False,
    ),
    (
        re.compile(
            r"\b(?:with|has|having|include(?:s|ing)?|want(?:s|ing)?)\s+(?:a\s+|an\s+)?quizz?es?\b",
            re.IGNORECASE,
        ),
        "has_quiz",
        True,
    ),
    (
        re.compile(
            r"\b(?:no|without|avoid(?:ing)?)\s+(?:an\s+|a\s+)?assignments?\b",
            re.IGNORECASE,
        ),
        "has_assignment",
        False,
    ),
    (
        re.compile(
            r"\b(?:with|has|having|include(?:s|ing)?|want(?:s|ing)?)\s+(?:an\s+|a\s+)?assignments?\b",
            re.IGNORECASE,
        ),
        "has_assignment",
        True,
    ),
    (
        re.compile(
            r"\b(?:lenient|easy|flexible|generous)\s+make-?ups?(?:\s+polic(?:y|ies))?\b",
            re.IGNORECASE,
        ),
        "makeup_policy",
        "lenient",
    ),
    (
        re.compile(
            r"\b(?:strict|harsh)\s+make-?ups?(?:\s+polic(?:y|ies))?\b",
            re.IGNORECASE,
        ),
        "makeup_policy",
        "strict",
    ),
)
_CATEGORIES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:discipline|disciplinary)\s+electives?\b", re.IGNORECASE), "DEL"),
    (re.compile(r"\bhumanities\s+electives?\b", re.IGNORECASE), "HUEL"),
    (re.compile(r"\bopen\s+electives?\b", re.IGNORECASE), "OPEL"),
    (re.compile(r"\bdels?\b", re.IGNORECASE), "DEL"),
    (re.compile(r"\bhuel\b", re.IGNORECASE), "HUEL"),
    (re.compile(r"\bopels?\b", re.IGNORECASE), "OPEL"),
    (re.compile(r"\bcdcs?\b", re.IGNORECASE), "CDC"),
)
_CLOCK = re.compile(
    r"\b(?:no|without|avoid(?:ing)?)\s+(?:an?\s+)?(?:classes?\s+at\s+)?"
    r"(\d{1,2})(?::00)?\s*([ap])\.?\s*m\.?"
    r"(?:\s+class(?:es)?)?\b",
    re.IGNORECASE,
)
_FREE_DAY = re.compile(
    rf"\b(?:keep(?:ing)?\s+|leave\s+|leaving\s+)?({_DAY_PATTERN})\s+free\b"
    rf"|\b(?:no|without)\s+classes?\s+on\s+({_DAY_PATTERN})\b"
    rf"|\bfree\s+(?:on\s+)?({_DAY_PATTERN})\b",
    re.IGNORECASE,
)
_GAPS = re.compile(r"\b(?:avoid(?:ing)?|no|without)\s+long\s+gaps?\b", re.IGNORECASE)
_COMPACT = re.compile(
    r"\b(?:prefer(?:ring)?\s+(?:a\s+)?)?compact\s+(?:timetable|schedule)\b",
    re.IGNORECASE,
)


def parse_query(query: str) -> dict:
    text = query or ""
    found: list[dict] = []
    spans: list[tuple[int, int]] = []
    for pattern, prop, wants in _PATTERNS:
        for match in pattern.finditer(text):
            if _overlaps(match.span(), spans):
                continue
            spans.append(match.span())
            found.append({"property": prop, "wants": wants, "text": match.group(0)})

    categories: list[str] = []
    for pattern, category in _CATEGORIES:
        for match in pattern.finditer(text):
            if _overlaps(match.span(), spans):
                continue
            spans.append(match.span())
            if category not in categories:
                categories.append(category)

    schedule = {
        "avoid_clocks": [],
        "free_days": [],
        "avoid_long_gaps": False,
        "compact": False,
    }
    for match in _CLOCK.finditer(text):
        if _overlaps(match.span(), spans):
            continue
        spans.append(match.span())
        clock = {
            "hour": int(match.group(1)),
            "meridiem": "AM" if match.group(2).lower() == "a" else "PM",
            "text": match.group(0),
        }
        if clock not in schedule["avoid_clocks"]:
            schedule["avoid_clocks"].append(clock)
    for match in _FREE_DAY.finditer(text):
        if _overlaps(match.span(), spans):
            continue
        spans.append(match.span())
        name = next(group for group in match.groups() if group)
        day = _DAYS[name.lower()]
        if day not in schedule["free_days"]:
            schedule["free_days"].append(day)
    for match in _GAPS.finditer(text):
        if _overlaps(match.span(), spans):
            continue
        spans.append(match.span())
        schedule["avoid_long_gaps"] = True
    for match in _COMPACT.finditer(text):
        if _overlaps(match.span(), spans):
            continue
        spans.append(match.span())
        schedule["compact"] = True

    by_property: dict[str, object] = {}
    contradictions: list[str] = []
    for item in found:
        previous = by_property.get(item["property"])
        if previous is not None and previous != item["wants"]:
            if item["property"] not in contradictions:
                contradictions.append(item["property"])
        by_property[item["property"]] = item["wants"]

    leftover = list(text)
    for start, end in spans:
        leftover[start:end] = " " * (end - start)
    interests, unrecognized = _interests("".join(leftover))
    preferences = []
    if not contradictions:
        preferences = [
            {"property": prop, "wants": wants}
            for prop, wants in by_property.items()
        ]
    mode = "any" if re.search(r"\bor\b", text, re.IGNORECASE) and len(categories) > 1 else "all"
    return {
        "preferences": preferences,
        "interests": interests,
        "categories": categories,
        "category_mode": mode,
        "schedule": schedule,
        "contradictions": contradictions,
        "unrecognized": unrecognized,
    }


def _overlaps(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < previous_end and end > previous_start for previous_start, previous_end in spans)


def _interests(text: str) -> tuple[list[str], list[str]]:
    interests: list[str] = []
    unrecognized: list[str] = []
    chunks = re.split(r"[,;&/]|\band\b", text, flags=re.IGNORECASE)
    for chunk in chunks:
        words = [
            word
            for word in re.findall(r"[a-z0-9]+", chunk.lower())
            if word not in _STOP and (len(word) > 2 or word in _SHORT_OK)
        ]
        if not words:
            continue
        phrase = " ".join(words)
        if all(word in _BARE_PROPERTIES for word in words):
            unrecognized.append(phrase)
            continue
        if phrase not in interests:
            interests.append(phrase)
    return interests, unrecognized
