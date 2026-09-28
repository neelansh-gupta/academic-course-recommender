"""Pilani campus programme names printed in the bulletin.

The degree list is the integrated first-degree section under
"Programmes offered at BITS Pilani – Pilani Campus". Dual degree options are
the B.E. programmes from that list, which is the second degree assigned to
M.Sc. students. Minor names are the "Minor in ..." headings in Part IV.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pymupdf

_DEGREE = re.compile(r"^(?:B\.E\.|M\.Sc\.)\s+\S.*|B\. Pharm\.$")
_MINOR = re.compile(r"^Minor in [A-Z][A-Za-z, ]{2,70}$")


def pilani_programmes(bulletin_path: str | Path) -> dict:
    pdf = Path(bulletin_path)
    if not pdf.is_file():
        return {"degrees": [], "dual_degrees": [], "minors": []}
    stamp = pdf.stat().st_mtime_ns
    return dict(_cached(str(pdf.resolve()), stamp))


@lru_cache(maxsize=2)
def _cached(path: str, _mtime: int) -> tuple:
    document = pymupdf.open(path)
    try:
        pages = [(page.get_text("text") or "") for page in document]
    finally:
        document.close()
    degrees = _pilani_degrees(pages)
    minors = _minor_titles(pages)
    dual = [name for name in degrees if name.startswith("B.E.")]
    return (
        ("degrees", tuple(degrees)),
        ("dual_degrees", tuple(dual)),
        ("minors", tuple(minors)),
    )


def _pilani_degrees(pages: list[str]) -> list[str]:
    for text in pages:
        if "PROGRAMMES OFFERED AT BITS PILANI" not in text or "PILANI CAMPUS" not in text:
            continue
        start = text.find("Integrated First Degree Programmes")
        end = text.find("Higher Degree Programmes")
        if start < 0 or end < start:
            continue
        names = []
        for raw in text[start:end].splitlines():
            line = " ".join(raw.split())
            if _DEGREE.match(line) and line not in names:
                names.append(line)
        if names:
            return names
    return []


def _minor_titles(pages: list[str]) -> list[str]:
    found = []
    pending = None
    for text in pages:
        lines = []
        for raw in text.splitlines():
            line = " ".join(raw.split())
            if not line or re.fullmatch(r"[IVX]+-\d+", line):
                continue
            lines.append(line)
        if pending and lines and lines[0] == "Description":
            _add(found, pending)
        pending = None
        for index, line in enumerate(lines):
            if not _MINOR.match(line):
                continue
            nxt = lines[index + 1] if index + 1 < len(lines) else None
            if nxt == "Description":
                _add(found, line)
            elif nxt is None:
                pending = line
    return found


def _add(found: list[str], name: str) -> None:
    if name not in found:
        found.append(name)
