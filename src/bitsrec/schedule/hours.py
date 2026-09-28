"""Clock times printed in the timetable legend.

Hour numbers in section rows are the legend's hour numbers. A request such as
"8 AM" is applied only after that legend has been read.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pymupdf

_CLOCK = re.compile(r"(\d{1,2})-(\d{1,2}:\d{2})(AM|PM)", re.IGNORECASE)
_INDEX = re.compile(r"(?m)^\s*(\d{1,2})\s*$")


def load_hour_legend(path: str | Path) -> dict[int, str]:
    pdf = Path(path)
    if not pdf.is_file():
        return {}
    stamp = pdf.stat().st_mtime_ns
    return dict(_cached_legend(str(pdf.resolve()), stamp))


def hours_for_clock(legend: dict[int, str], hour: int, meridiem: str) -> list[int]:
    found = []
    marker = meridiem.upper()
    for number, label in legend.items():
        match = _CLOCK.search(label.replace(" ", ""))
        if match is None:
            continue
        if int(match.group(1)) == hour and match.group(3).upper() == marker:
            found.append(number)
    return sorted(found)


@lru_cache(maxsize=4)
def _cached_legend(path: str, _mtime: int) -> tuple[tuple[int, str], ...]:
    document = pymupdf.open(path)
    try:
        for page in document:
            text = page.get_text("text") or ""
            if "the number indicates timings" not in text.lower():
                continue
            parsed = _parse_legend(text)
            if parsed:
                return tuple(sorted(parsed.items()))
    finally:
        document.close()
    return ()


def _parse_legend(text: str) -> dict[int, str]:
    start = text.lower().find("the number indicates timings")
    if start < 0:
        return {}
    block = text[start:]
    end = re.search(r"\n\s*10\.\s", block)
    if end:
        block = block[: end.start()]
    numbers = [int(value) for value in _INDEX.findall(block)]
    clocks = [match.group(0).upper().replace(" ", "") for match in _CLOCK.finditer(block)]
    if len(numbers) != len(clocks) or set(numbers) != set(range(1, len(numbers) + 1)):
        return {}
    return dict(zip(numbers, clocks))
