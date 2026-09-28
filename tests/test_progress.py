"""Semester suggestions, explanations, plans, and minor totals stay tied to stored text."""

from __future__ import annotations

import pytest

from bitsrec.engine.eligibility import analyze_profile, explain_course
from bitsrec.engine.minors import requirement_for
from bitsrec.engine.progress import plan_terms, suggest_completed
from bitsrec.paths import processed_dir, raw_dir

DB = processed_dir() / "bits.sqlite"


@pytest.fixture
def db():
    if not DB.is_file():
        pytest.skip("processed database is not built")
    return DB


def profile(**extra):
    base = {
        "campus": "pilani",
        "admission_year": 2024,
        "degree": "B.E. Computer Science",
        "current_semester": 5,
        "completed_courses": [],
        "current_courses": [],
    }
    base.update(extra)
    return base


def test_chart_suggestions_are_not_applied(db):
    result = suggest_completed(profile(), db)
    assert result["applied"] is False
    assert result["suggested"]
    assert all(item["assumed"] and item["chart_semester"] < 5 for item in result["suggested"])
    assert result["skipped_without_position"] > 0


def test_explain_names_the_reason_a_course_is_out(db):
    done = explain_course(profile(completed_courses=["CS F211"]), "CS F211", db)
    assert done["status"] == "not_eligible"
    assert "completed" in done["reason"]
    current = explain_course(profile(current_courses=["CS F211"]), "cs f211", db)
    assert "current" in current["reason"]
    cohort = explain_course(profile(), "CS U101", db)
    assert cohort["status"] == "not_eligible"
    assert "2026" in cohort["reason"]


def test_plan_orders_unfinished_cdc_without_claiming_the_chart(db):
    result = plan_terms(profile(completed_courses=["CS F211"]), db)
    placed = [course["course_code"] for term in result["terms"] for course in term["courses"]]
    assert "CS F211" not in placed
    assert "CS F212" in placed
    assert "official semester chart" in result["note"]
    assert any(pool["category"] == "DEL" for pool in result["open_pools"])


def test_minor_total_is_read_from_the_bulletin(db):
    bulletin = raw_dir() / "bulletin.pdf"
    if not bulletin.is_file():
        pytest.skip("bulletin pdf is not available")
    general = requirement_for("Minor in Underwater Basket", bulletin)
    assert general is not None
    assert general["specific"] is False
    assert general["min_courses"] == 5
    assert general["min_units"] == 15
    quantum = requirement_for("Minor in Quantum Information and Technologies", bulletin)
    assert quantum["specific"] is True
    assert quantum["min_courses"] == 5
    assert quantum["min_units"] == 15
    academic = analyze_profile(profile(minor="Minor in Computing and Intelligence"), db)
    row = next(item for item in academic["requirements"] if item["category"] == "MINOR")
    assert row["required_courses_min"] == 6
    assert row["required_units_min"] == 18
    assert row["specific"] is True
    assert not any("not stored as a programme rule" in warning for warning in academic["warnings"])
