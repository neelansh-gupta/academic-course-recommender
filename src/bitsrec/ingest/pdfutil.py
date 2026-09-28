"""Shared PDF helpers built on PyMuPDF word coordinates."""

from __future__ import annotations

from collections.abc import Iterable

import pymupdf

Word = tuple


def open_pdf(path: str) -> pymupdf.Document:
    return pymupdf.open(path)


def page_text(page: pymupdf.Page) -> str:
    return page.get_text("text") or ""


def words_of(page: pymupdf.Page) -> list[tuple]:
    return list(page.get_text("words") or [])


def lines_from_words(words: Iterable[tuple], y_tol: float = 2.4) -> list[dict]:
    """Cluster words into visual lines. ``y`` stays the first word's top."""
    ordered = sorted(words, key=lambda w: (w[1], w[0]))
    lines: list[dict] = []
    for word in ordered:
        if not lines or word[1] - lines[-1]["y"] > y_tol:
            lines.append({"y": word[1], "words": [word]})
        else:
            lines[-1]["words"].append(word)
    for line in lines:
        line["words"].sort(key=lambda w: w[0])
        line["text"] = " ".join(w[4] for w in line["words"]).strip()
    return [line for line in lines if line["text"]]


def column_lines(page: pymupdf.Page, split_x: float, y_tol: float = 2.4) -> list[list[dict]]:
    left: list[tuple] = []
    right: list[tuple] = []
    for word in words_of(page):
        if word[0] < split_x:
            left.append(word)
        else:
            right.append(word)
    return [lines_from_words(left, y_tol), lines_from_words(right, y_tol)]


def split_x_for(page: pymupdf.Page) -> float:
    """Column boundary used by the bulletin templates in this dataset."""
    width = float(page.rect.width)
    if width >= 560:
        return 296.0
    return 256.0


def dehyphenate(lines: list[str]) -> str:
    parts: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if parts and parts[-1].endswith("-") and line[:1].islower():
            parts[-1] = parts[-1][:-1] + line
        else:
            parts.append(line)
    return " ".join(parts)
