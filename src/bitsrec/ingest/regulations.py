"""Academic Regulations parser.

Clause numbers in this document sit at the end of each clause. The body
stored for a clause is the text that precedes its number.
"""

from __future__ import annotations

import re
from pathlib import Path

from bitsrec.ingest.pdfutil import open_pdf, page_text

CLAUSE_LINE_RE = re.compile(r"^[ \t]*(\d{1,2}\.\d{2}[a-z]?)[ \t]*$")
PAGE_MARK_RE = re.compile(r"\[\[PAGE \d+\]\]")

# Degree labels printed as their own lines in clause 1.04, in document order.
_UNIT_LABELS = [
    "B.Sc. (Off Campus programmes)",
    "Integrated First Degree",
    "M.E./M.Pharm.",
    "M.S./M.P.H.",
    "MBA in Business Analytics (On Campus Programme)",
    "MBA",
    "M.Phil.",
    "Ph.D.(Thesis)",
    "M.Tech./MBA (Off-Campus Programmes)",
    "M.Sc. (Off-Campus Programmes) (for normal input of 3 year",
]


def parse_regulations(path: Path) -> dict:
    document = open_pdf(str(path))
    try:
        pages = [page_text(page) for page in document]
    finally:
        document.close()

    clauses = _split_clauses(pages)
    minimum_units = _minimum_units(clauses)
    return {
        "source_document": "Academic-Regulations-2023.pdf",
        "clauses": clauses,
        "minimum_units": minimum_units,
    }


def _split_clauses(pages: list[str]) -> list[dict]:
    buffer: list[str] = []
    clauses: list[dict] = []
    for page_number, text in enumerate(pages, start=1):
        seen_content = False
        for raw_line in text.splitlines():
            stripped = raw_line.strip()
            if not seen_content:
                if not stripped:
                    continue
                seen_content = True
                # Printed page number at the top of the page, not a unit count.
                if re.fullmatch(r"\d{1,3}", stripped):
                    continue
            match = CLAUSE_LINE_RE.match(raw_line)
            if match and buffer:
                body = _clean_body("\n".join(buffer))
                if body:
                    clauses.append(
                        {
                            "clause_id": match.group(1),
                            "body": body,
                            "page": page_number,
                            "source_document": "Academic-Regulations-2023.pdf",
                        }
                    )
                buffer = []
            else:
                buffer.append(raw_line)
    return _dedupe_clauses(clauses)


def _dedupe_clauses(clauses: list[dict]) -> list[dict]:
    """Keep the longest body when a clause number is printed more than once."""
    best: dict[str, dict] = {}
    for clause in clauses:
        current = best.get(clause["clause_id"])
        if current is None or len(clause["body"]) > len(current["body"]):
            best[clause["clause_id"]] = clause
    return [best[key] for key in sorted(best, key=_clause_sort_key)]


def _clause_sort_key(clause_id: str) -> tuple:
    match = re.match(r"(\d+)\.(\d+)([a-z]?)", clause_id)
    if not match:
        return (999, 999, clause_id)
    return (int(match.group(1)), int(match.group(2)), match.group(3))


def _clean_body(body: str) -> str:
    body = PAGE_MARK_RE.sub("", body)
    lines = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue
        # Drop isolated page numbers that the running header leaves behind.
        lines.append(stripped)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return text


def _minimum_units(clauses: list[dict]) -> list[dict]:
    clause = next(
        (
            item
            for item in clauses
            if "Integrated First Degree" in item["body"] and re.search(r"(?m)^144$", item["body"])
        ),
        None,
    )
    if clause is None:
        return []
    lines = [line.strip() for line in clause["body"].splitlines() if line.strip()]
    rows: list[dict] = []
    index = 0
    while index < len(lines):
        label = _match_unit_label(lines, index)
        if label is None:
            index += 1
            continue
        index += label["span"]
        if index < len(lines) and re.fullmatch(r"\d+", lines[index]):
            rows.append(
                {
                    "programme": label["name"],
                    "degree_family": label["name"],
                    "category": "MINIMUM_DEGREE_UNITS",
                    "min_units": float(lines[index]),
                    "max_units": None,
                    "min_courses": None,
                    "max_courses": None,
                    "units_raw": lines[index],
                    "courses_raw": None,
                    "notes": f"Stated minimum units in Academic Regulations clause {clause['clause_id']}.",
                    "source_document": "Academic-Regulations-2023.pdf",
                    "source_page": clause["page"],
                    "source_section": clause["clause_id"],
                    "confidence": 0.95,
                }
            )
            index += 1
        else:
            index += 1
    diploma = re.search(
        r"Post Graduate Diploma \(Off Campus\)\s+is\s+(\d+)\s+units",
        clause["body"],
        re.IGNORECASE,
    )
    if diploma:
        rows.append(
            {
                "programme": "Post Graduate Diploma (Off Campus)",
                "degree_family": "Post Graduate Diploma (Off Campus)",
                "category": "MINIMUM_DEGREE_UNITS",
                "min_units": float(diploma.group(1)),
                "max_units": None,
                "min_courses": None,
                "max_courses": None,
                "units_raw": diploma.group(1),
                "courses_raw": None,
                "notes": "Stated in Academic Regulations clause 1.04a.",
                "source_document": "Academic-Regulations-2023.pdf",
                "source_page": clause["page"],
                "source_section": "1.04a",
                "confidence": 0.9,
            }
        )
    return rows


def _match_unit_label(lines: list[str], index: int) -> dict | None:
    window = " ".join(lines[index : index + 3])
    for label in _UNIT_LABELS:
        if window.startswith(label):
            span = 1
            consumed = lines[index]
            while span < 3 and label not in consumed and index + span < len(lines):
                consumed = f"{consumed} {lines[index + span]}"
                span += 1
                if label in consumed:
                    break
            if label in consumed or lines[index] == label:
                span = span if label in consumed else 1
                if label.startswith("M.Sc. (Off-Campus") and index + span < len(lines) and lines[index + span].startswith("Undergraduate"):
                    span += 1
                return {
                    "name": label.replace(" (for normal input of 3 year", "").strip(),
                    "span": span,
                }
    if lines[index] in _UNIT_LABELS:
        return {"name": lines[index], "span": 1}
    return None
