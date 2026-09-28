"""Pull course codes out of pasted transcript text. Confirmation stays with the student."""

from __future__ import annotations

import re

from bitsrec.ingest.codes import find_codes

_INCOMPLETE = re.compile(
    r"\b(NC|RC|W|fail(?:ed)?|not completed|incomplete|withdrawn)\b",
    re.IGNORECASE,
)


def parse_transcript(text: str) -> dict:
    found = []
    seen: set[str] = set()
    for raw in (text or "").splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if not line:
            continue
        note = None
        if _INCOMPLETE.search(line):
            note = "This line contains a word that may mean the course was not completed."
        for code in find_codes(line):
            if code in seen:
                continue
            seen.add(code)
            found.append(
                {
                    "course_code": code,
                    "line": line[:180],
                    "note": note,
                    "preselected": note is None,
                }
            )
    return {
        "needs_confirmation": True,
        "applied": False,
        "courses": found,
        "warning": "These codes were read from the pasted text. They are not completed courses until you add them.",
    }
