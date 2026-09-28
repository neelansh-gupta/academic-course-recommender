"""Minor size rules read from the bulletin, not from a hardcoded table.

The general first-degree table is used when a minor page does not print its
own course and unit total. A minor that prints "N courses (min)" and
"M units (min)" uses that total instead.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pymupdf

_TITLE = re.compile(r"^Minor in [A-Z][A-Za-z, ]{2,70}$")
_SPECIFIC = re.compile(
    r"(\d+)\s*courses?\s*\(min\)\s+(\d+)\s*units?\s*\(min\)",
    re.IGNORECASE,
)
_CORE = re.compile(
    r"Minor\s*[–\-]\s*Core\s+(\d+)\s*\(max\)\s+(\d+)\s*\(max\)",
    re.IGNORECASE,
)
_ELECTIVE = re.compile(
    r"Minor\s*[–\-]\s*Electives\s+(\d+)\s*\(min\)\s+(\d+)\s*\(min\)",
    re.IGNORECASE,
)
_TOTAL = re.compile(
    r"Minor\s*[–\-]\s*Total\s+(\d+)\s*\(min\)\s+(\d+)\s*\(min\)",
    re.IGNORECASE,
)
_PRINTED = re.compile(r"\bIV-\d+\b")


def requirement_for(name: str, bulletin_path: str | Path) -> dict | None:
    pdf = Path(bulletin_path)
    if not pdf.is_file() or not name:
        return None
    parsed = _cached(str(pdf.resolve()), pdf.stat().st_mtime_ns)
    if not parsed["general"] and not parsed["specific"]:
        return None
    heading = _match_heading(name, list(parsed["specific"]))
    if heading:
        return dict(parsed["specific"][heading])
    if not parsed["general"]:
        return None
    general = dict(parsed["general"])
    general["heading"] = name
    general["specific"] = False
    return general


@lru_cache(maxsize=2)
def _cached(path: str, _mtime: int) -> dict:
    lines: list[tuple[int, str]] = []
    with pymupdf.open(path) as document:
        for page in document:
            printed = None
            for raw in page.get_text().splitlines():
                text = re.sub(r"\s+", " ", raw).strip()
                if not text:
                    continue
                found = _PRINTED.search(text)
                if found and len(text) < 12:
                    printed = found.group(0)
                lines.append((page.number + 1, printed or "", text))
    return {"general": _general(lines), "specific": _specific(lines)}


def _general(lines: list[tuple[int, str, str]]) -> dict | None:
    for index, (page, printed, text) in enumerate(lines):
        if "requirements for a minor" not in text.lower():
            continue
        window = " ".join(item[2] for item in lines[index : index + 40])
        total = _TOTAL.search(window)
        if not total:
            continue
        core = _CORE.search(window)
        elective = _ELECTIVE.search(window)
        label = f"Requirements for a minor ({printed})" if printed else "Requirements for a minor"
        return {
            "heading": None,
            "min_courses": int(total.group(1)),
            "min_units": float(total.group(2)),
            "core_courses_max": None if core is None else int(core.group(1)),
            "core_units_max": None if core is None else float(core.group(2)),
            "elective_courses_min": None if elective is None else int(elective.group(1)),
            "elective_units_min": None if elective is None else float(elective.group(2)),
            "courses_raw": f"{total.group(1)} (min)",
            "units_raw": f"{total.group(2)} (min)",
            "source_page": page,
            "source_section": label,
            "specific": False,
            "note": "General first-degree minor table. This minor page did not print its own total.",
        }
    return None


def _specific(lines: list[tuple[int, str, str]]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for index, (_page, _printed, text) in enumerate(lines):
        if not _TITLE.match(text):
            continue
        title = text
        window_lines = []
        page = _page
        printed = _printed
        for later_page, later_printed, later in lines[index + 1 : index + 30]:
            if _TITLE.match(later):
                break
            window_lines.append(later)
            page = later_page
            printed = later_printed or printed
        match = _SPECIFIC.search(" ".join(window_lines))
        if not match:
            continue
        label = "Courses & Units Required"
        if printed:
            label = f"{label} ({printed})"
        found[title] = {
            "heading": title,
            "min_courses": int(match.group(1)),
            "min_units": float(match.group(2)),
            "core_courses_max": None,
            "core_units_max": None,
            "elective_courses_min": None,
            "elective_units_min": None,
            "courses_raw": f"{match.group(1)} (min)",
            "units_raw": f"{match.group(2)} (min)",
            "source_page": page,
            "source_section": label,
            "specific": True,
            "note": "This minor page prints its own course and unit total.",
        }
    return found


def _match_heading(query: str, headings: list[str]) -> str | None:
    wanted = _tokens(query)
    if not wanted:
        return None
    scored = []
    for heading in headings:
        have = _tokens(heading)
        if not have:
            continue
        if wanted == have:
            scored.append((len(have), 2, heading))
        elif len(have) <= len(wanted) and wanted[: len(have)] == have:
            scored.append((len(have), 1, heading))
        elif len(wanted) <= len(have) and have[: len(wanted)] == wanted:
            scored.append((len(wanted), 1, heading))
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
    best = [item for item in scored if item[0] == scored[0][0] and item[1] == scored[0][1]]
    if len(best) > 1:
        return None
    return scored[0][2]


def _tokens(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).split()
