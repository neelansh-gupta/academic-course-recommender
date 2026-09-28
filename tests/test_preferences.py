"""Preference ranking stays inside the eligible set and cites stored handouts."""

from __future__ import annotations

import re
import sqlite3

import pytest

from bitsrec.engine import eligible_courses
from bitsrec.llm import parse_query, recommend
from bitsrec.llm.recommend import consensus
from bitsrec.paths import processed_dir

DB = processed_dir() / "bits.sqlite"


@pytest.fixture
def db():
    if not DB.is_file():
        pytest.skip("processed database is not built")
    return DB


def profile(**overrides):
    base = {
        "campus": "pilani",
        "admission_year": 2024,
        "degree": "B.E. Computer Science",
        "current_semester": 5,
        "completed_courses": [],
        "current_courses": [],
        "interests": [],
    }
    base.update(overrides)
    return base


def codes(rows):
    return {row["course_code"] for row in rows}


def test_query_parser_keeps_polarity_and_interest_phrases():
    parsed = parse_query("no midsem and machine learning")
    assert parsed["preferences"] == [{"property": "has_midsem", "wants": False}]
    assert parsed["interests"] == ["machine learning"]
    assert parsed["contradictions"] == []
    assert parsed["unrecognized"] == []

    bare = parse_query("midsem")
    assert bare["preferences"] == []
    assert bare["unrecognized"] == ["midsem"]

    both = parse_query("no midsem with a midsem")
    assert "has_midsem" in both["contradictions"]
    assert both["preferences"] == []


def test_disagreeing_handouts_do_not_produce_a_value():
    rows = [
        {"has_midsem": 0, "source_file": "a.pdf"},
        {"has_midsem": 1, "source_file": "b.pdf"},
    ]
    status, value, evidence = consensus(rows, "has_midsem")
    assert status == "conflict"
    assert value is None
    assert len(evidence) == 2
    assert consensus([{"has_midsem": None}], "has_midsem")[0] == "unverified"
    assert consensus([{"has_midsem": 0}], "has_midsem") == (
        "stated",
        0,
        [{"has_midsem": 0}],
    )


def test_empty_query_returns_only_eligible_courses_without_preference_claims(db):
    student = profile()
    result = recommend(student, "", db)
    eligible = codes(eligible_courses(student, db))
    recommended = codes(result["recommendations"])
    assert recommended == eligible
    assert "CS F211" in recommended
    assert "CS U101" not in recommended
    assert result["unverified"] == []
    assert result["not_matched_count"] == 0
    assert result["recommendations"][0]["course_code"] == "CS F211"
    assert result["recommendations"][0]["matched_properties"] == []


def test_no_midsem_cites_a_real_handout_and_keeps_nulls_unverified(db):
    student = profile()
    result = recommend(student, "no midsem", db)
    recommended = codes(result["recommendations"])
    unverified = codes(result["unverified"])
    eligible = codes(eligible_courses(student, db))
    assert recommended.isdisjoint(unverified)
    assert recommended | unverified <= eligible
    assert (
        len(result["recommendations"]) + len(result["unverified"]) + result["not_matched_count"]
        == result["eligible_count"]
    )
    assert "CS F429" in recommended
    assert "CS F301" not in recommended
    assert "CS F301" not in unverified
    assert "CS F215" in unverified
    assert "CS U101" not in recommended | unverified

    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    try:
        for row in result["recommendations"]:
            assert row["matched_properties"]
            for prop in row["matched_properties"]:
                assert prop["property"] == "has_midsem"
                assert prop["value"] == 0
                for citation in prop["citations"]:
                    stored = connection.execute(
                        """
                        SELECT has_midsem, campus FROM handouts
                        WHERE course_code = ? AND source_file = ?
                        """,
                        (row["course_code"], citation["source_file"]),
                    ).fetchone()
                    assert stored is not None
                    assert stored[0] == 0
                    assert (stored[1] or "").lower() == "pilani"
            stated = connection.execute(
                """
                SELECT has_midsem FROM handouts
                WHERE course_code = ? AND lower(campus) = 'pilani' AND has_midsem IS NOT NULL
                """,
                (row["course_code"],),
            ).fetchall()
            assert stated
            assert {value for (value,) in stated} == {0}
        pending = next(row for row in result["unverified"] if row["course_code"] == "CS F215")
        assert pending["unverified_properties"][0]["property"] == "has_midsem"
        assert "did not yield" in pending["unverified_properties"][0]["reason"]
    finally:
        connection.close()


def test_project_and_midsem_must_both_be_stated(db):
    result = recommend(profile(), "no midsem with a project", db)
    recommended = codes(result["recommendations"])
    assert "BITS F428" in recommended
    assert "CS F429" not in recommended
    course = next(row for row in result["recommendations"] if row["course_code"] == "BITS F428")
    cited = {item["property"]: item["value"] for item in course["matched_properties"]}
    assert cited == {"has_midsem": 0, "has_project": 1}
    assert any(
        citation["source_file"] == "050_BITS_F428.pdf"
        for item in course["matched_properties"]
        for citation in item["citations"]
    )


def test_quiz_flag_is_not_treated_as_an_assignment(db):
    quiz = codes(recommend(profile(), "with a quiz", db)["recommendations"])
    assignment = codes(recommend(profile(), "with an assignment", db)["recommendations"])
    assert "CS F429" in quiz
    assert "CS F429" not in assignment


def test_another_campus_handout_is_not_used(db):
    result = recommend(profile(campus="goa"), "no midsem", db)
    assert result["recommendations"] == []
    assert len(result["unverified"]) == result["eligible_count"]
    assert result["not_matched_count"] == 0
    pending = next(row for row in result["unverified"] if row["course_code"] == "CS F429")
    assert "No handout for this campus" in pending["unverified_properties"][0]["reason"]


def test_interest_citations_are_substrings_of_stored_text(db):
    student = profile()
    result = recommend(student, "machine learning", db)
    recommended = codes(result["recommendations"])
    assert "BITS F464" in recommended
    assert "CS U311" not in recommended | codes(result["unverified"])
    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    try:
        for row in result["recommendations"]:
            assert row["interest_matches"]
            for hit in row["interest_matches"]:
                assert hit["term"] in _compact(hit["excerpt"])
                if hit["source"] == "courses":
                    stored = connection.execute(
                        f"SELECT {hit['field']} FROM courses WHERE course_code = ?",
                        (row["course_code"],),
                    ).fetchone()[0]
                else:
                    stored = connection.execute(
                        f"SELECT {hit['field']} FROM handouts WHERE course_code = ? AND source_file = ?",
                        (row["course_code"], hit["source_file"]),
                    ).fetchone()[0]
                assert _compact(hit["excerpt"]) in _compact(stored)
    finally:
        connection.close()


def test_interest_does_not_override_a_stated_midsem(db):
    result = recommend(profile(), "no midsem and machine learning", db)
    recommended = codes(result["recommendations"])
    unverified = codes(result["unverified"])
    assert "CE F417" in recommended
    assert "BITS F464" not in recommended | unverified
    assert "CS F415" in unverified
    assert "CS U311" not in recommended | unverified
    course = next(row for row in result["recommendations"] if row["course_code"] == "CE F417")
    assert course["matched_properties"][0]["value"] == 0
    assert any("machine learning" in hit["excerpt"].lower() for hit in course["interest_matches"])
    pending = next(row for row in result["unverified"] if row["course_code"] == "CS F415")
    assert pending["interest_matches"]
    assert pending["unverified_properties"][0]["property"] == "has_midsem"


def test_attendance_not_required_uses_only_that_status(db):
    result = recommend(profile(), "attendance not required", db)
    recommended = codes(result["recommendations"])
    unverified = codes(result["unverified"])
    assert "BITS F327" in recommended
    assert "BITS F314" not in recommended | unverified
    assert "CS F429" in unverified
    course = next(row for row in result["recommendations"] if row["course_code"] == "BITS F327")
    citation = course["matched_properties"][0]["citations"][0]
    assert citation["source_file"] == "044_BITS_F327.pdf"
    assert citation["value"] == "not_required"


def test_completed_and_blocked_courses_stay_out(db):
    done = recommend(profile(completed_courses=["CS F211"]), "no midsem", db)
    assert "CS F211" not in codes(done["recommendations"]) | codes(done["unverified"])
    blocked = recommend(
        profile(degree="B.E. Architecture and Urban Engineering"),
        "machine learning",
        db,
    )
    assert "AUE F313" not in codes(blocked["recommendations"]) | codes(blocked["unverified"])


def test_assignment_queries_become_category_and_policy_checks():
    dels = parse_query("Suggest DELs related to AI.")
    assert dels["categories"] == ["DEL"]
    assert dels["interests"] == ["ai"]
    assert dels["preferences"] == []

    opel = parse_query("I want an OPEL with no attendance requirement.")
    assert opel["categories"] == ["OPEL"]
    assert opel["preferences"] == [{"property": "attendance_status", "wants": "not_required"}]
    assert opel["interests"] == []

    makeup = parse_query("Suggest courses with no midsem and a lenient makeup policy.")
    assert makeup["preferences"] == [
        {"property": "has_midsem", "wants": False},
        {"property": "makeup_policy", "wants": "lenient"},
    ]
    assert makeup["interests"] == []

    huel = parse_query("I need a HUEL and prefer project-based evaluation.")
    assert huel["categories"] == ["HUEL"]
    assert huel["preferences"] == [{"property": "has_project", "wants": True}]
    assert huel["interests"] == []

    combined = parse_query("Suggest an AI-related DEL with no midsem")
    assert combined["categories"] == ["DEL"]
    assert combined["interests"] == ["ai"]
    assert combined["preferences"] == [{"property": "has_midsem", "wants": False}]


def test_del_query_matches_stored_ai_text_and_keeps_other_categories_out(db):
    result = recommend(profile(), "Suggest DELs related to AI.", db)
    recommended = codes(result["recommendations"])
    unverified = codes(result["unverified"])
    assert "CS F441" in recommended
    assert "CE F417" not in recommended | unverified
    assert "CS F211" not in recommended | unverified
    assert all("DEL" in row["satisfies"] for row in result["recommendations"])
    course = next(row for row in result["recommendations"] if row["course_code"] == "CS F441")
    assert course["explanation"]
    assert any("artificial intelligence" in hit["excerpt"].lower() for hit in course["interest_matches"])
    assert all(hit["term"] in _compact(hit["excerpt"]) for hit in course["interest_matches"])


def test_opel_attendance_query_stays_inside_open_electives(db):
    result = recommend(profile(), "Find an OPEL with no attendance requirement.", db)
    recommended = codes(result["recommendations"])
    assert "BITS F327" in recommended
    assert "CS F211" not in recommended | codes(result["unverified"])
    assert all("OPEL" in row["satisfies"] for row in result["recommendations"] + result["unverified"])
    course = next(row for row in result["recommendations"] if row["course_code"] == "BITS F327")
    assert course["matched_properties"][0]["citations"][0]["value"] == "not_required"


def test_lenient_makeup_stays_unverified_when_the_handout_does_not_say_so(db):
    result = recommend(profile(), "Suggest courses with no midsem and a lenient makeup policy.", db)
    recommended = codes(result["recommendations"])
    unverified = {row["course_code"]: row for row in result["unverified"]}
    assert "CS F429" not in recommended
    assert "CS F301" not in recommended
    assert "CS F301" not in unverified
    pending = unverified["CS F429"]
    assert pending["matched_properties"][0]["value"] == 0
    assert any(
        item["property"] == "makeup_policy" and "does not say that it is lenient" in item["reason"]
        for item in pending["unverified_properties"]
    )
    assert "lenient" in pending["explanation"]


def test_huel_project_query_uses_the_category_and_the_handout_flag(db):
    result = recommend(profile(), "I need a HUEL and prefer project-based evaluation.", db)
    recommended = codes(result["recommendations"])
    unverified = codes(result["unverified"])
    assert "BITS F428" in recommended
    assert "HSS F222" not in recommended | unverified
    course = next(row for row in result["recommendations"] if row["course_code"] == "BITS F428")
    assert "HUEL" in course["satisfies"]
    assert course["matched_properties"][0]["value"] == 1


def test_a_clashing_section_does_not_reject_a_course_with_a_feasible_section(db):
    result = recommend(
        profile(),
        "",
        db,
        held_sections=[{"course_code": "MATH F213", "section": "L1"}],
    )
    course = next(row for row in result["recommendations"] if row["course_code"] == "CS F111")
    assert course["section"]["section"] == "L2"
    assert "L1" in course["section"]["reason"]
    blocked = recommend(
        profile(),
        "",
        db,
        held_sections=[{"course_code": "CHEM F111", "section": "L1"}],
    )
    assert "AN F314" not in codes(blocked["recommendations"]) | codes(blocked["unverified"])


def test_no_eight_am_uses_the_timetable_legend(db):
    result = recommend(profile(), "no 8 AM classes", db)
    course = next(row for row in result["recommendations"] if row["course_code"] == "CS F429")
    assert course["section"]["section"] == "L1"
    pending = next(row for row in result["unverified"] if row["course_code"] == "CS F303")
    assert any(item["property"] == "timetable" for item in pending["unverified_properties"])


def test_contradictory_query_ranks_nobody(db):
    result = recommend(profile(), "no midsem with a midsem", db)
    assert result["recommendations"] == []
    assert result["unverified"] == []
    assert "has_midsem" in result["contradictions"]
    assert result["warnings"]


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()
