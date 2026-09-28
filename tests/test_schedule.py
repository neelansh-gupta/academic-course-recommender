"""Clash checks use stored day-hour slots and printed exam sessions."""

from __future__ import annotations

import sqlite3

import pytest

from bitsrec.paths import processed_dir, raw_dir
from bitsrec.schedule import (
    assess_sections,
    choose_among,
    compare_sections,
    feasible_sections,
    find_clashes,
    hours_for_clock,
    load_hour_legend,
)

DB = processed_dir() / "bits.sqlite"


def section(**overrides):
    base = {
        "course_code": "CS F211",
        "section": "L1",
        "section_kind": "lecture",
        "slots": [{"day": "Mon", "hour": 2}, {"day": "Wed", "hour": 2}],
        "midsem_date": "09/10",
        "midsem_session": "AN2",
        "compre_date": "14/12",
        "compre_session": "AN",
    }
    base.update(overrides)
    return base


@pytest.fixture
def db():
    if not DB.is_file():
        pytest.skip("processed database is not built")
    return DB


def by_section(rows):
    return {row["section"]: row for row in rows}


def test_meeting_overlap_is_a_clash_and_a_different_hour_is_not():
    left = section()
    overlap = compare_sections(
        left,
        section(
            course_code="BIO F111",
            slots=[{"day": "Wed", "hour": 2}],
            midsem_session="AN1",
            compre_session="FN",
        ),
    )
    assert overlap["clashes"] == [
        {"type": "meeting", "day": "Wed", "hour": 2, "kinds": ["lecture", "lecture"]}
    ]
    clear = compare_sections(
        left,
        section(
            course_code="CHEM F111",
            slots=[{"day": "Mon", "hour": 11}],
            midsem_session="AN1",
            compre_session="FN",
        ),
    )
    assert clear["clashes"] == []
    assert clear["unverified"] == []


def test_exam_clash_needs_the_same_date_and_session():
    left = section(slots=[{"day": "Mon", "hour": 2}])
    same_exam = section(
        course_code="CHEM F111",
        slots=[{"day": "Mon", "hour": 11}],
        midsem_session="AN2",
    )
    compared = compare_sections(left, same_exam)
    assert {"type": "midsem", "date": "09/10", "session": "AN2"} in compared["clashes"]
    different_session = section(
        course_code="CHEM F111",
        slots=[{"day": "Mon", "hour": 11}],
        midsem_session="AN1",
        compre_session="FN",
    )
    assert compare_sections(left, different_session)["clashes"] == []


def test_missing_times_are_unverified_rather_than_clear():
    missing = compare_sections(
        section(),
        section(
            course_code="BIO F266",
            slots=[],
            midsem_date=None,
            midsem_session=None,
            compre_date=None,
            compre_session=None,
        ),
    )
    assert missing["clashes"] == []
    assert {item["type"] for item in missing["unverified"]} == {"meeting", "midsem", "compre"}
    parent_session = compare_sections(
        section(midsem_session="AN"),
        section(course_code="OTHER", slots=[{"day": "Fri", "hour": 1}], midsem_session="AN1"),
    )
    assert not any(item["type"] == "midsem" for item in parent_session["clashes"])


def test_two_sections_of_one_course_clash_on_the_printed_exam(db):
    report = find_clashes(
        [
            {"course_code": "BIO F101", "section": "L1"},
            {"course_code": "BIO F101", "section": "L2"},
        ],
        db,
    )
    pair = report["pairs"][0]
    kinds = {item["type"] for item in pair["clashes"]}
    assert "meeting" not in kinds
    assert {"type": "midsem", "date": "09/10", "session": "AN2"} in pair["clashes"]
    assert {"type": "compre", "date": "14/12", "session": "AN"} in pair["clashes"]
    held = assess_sections("BIO F101", [{"course_code": "BIO F101", "section": "L1"}], db)
    assert "L1" in by_section(held["already_registered"])
    assert "L2" in by_section(held["clashing"])
    assert "L2" not in by_section(held["feasible"])


def test_lecture_and_practical_share_the_hour_grid(db):
    report = find_clashes(
        [
            {"course_code": "AN F314", "section": "L1"},
            {"course_code": "BITS K101-1", "section": "P1"},
        ],
        db,
    )
    meetings = [item for item in report["pairs"][0]["clashes"] if item["type"] == "meeting"]
    assert {(item["day"], item["hour"]) for item in meetings} == {("Mon", 10), ("Wed", 10), ("Fri", 10)}
    assert meetings[0]["kinds"] == ["lecture", "practical"]
    same_practical = find_clashes(
        [
            {"course_code": "BITS K101-1", "section": "P1"},
            {"course_code": "BITS K101-1", "section": "P2"},
        ],
        db,
    )
    assert any(item["type"] == "meeting" for item in same_practical["pairs"][0]["clashes"])


def test_stored_rows_separate_class_clashes_from_exam_clashes(db):
    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    try:
        bio = connection.execute(
            "SELECT days_hours_raw, midsem_date, midsem_session FROM timetable_sections WHERE course_code='BIO F101' AND section='L1'"
        ).fetchone()
        other = connection.execute(
            "SELECT days_hours_raw, midsem_date, midsem_session FROM timetable_sections WHERE course_code='BIO F111' AND section='L1'"
        ).fetchone()
    finally:
        connection.close()
    assert bio[0] == other[0] == "M W 2"
    class_clash = find_clashes(
        [
            {"course_code": "BIO F101", "section": "L1"},
            {"course_code": "BIO F111", "section": "L1"},
        ],
        db,
    )["pairs"][0]
    assert {("Mon", 2), ("Wed", 2)} <= {
        (item["day"], item["hour"]) for item in class_clash["clashes"] if item["type"] == "meeting"
    }

    exam_only = assess_sections("AN F314", [{"course_code": "CHEM F111", "section": "L1"}], db)
    clashing = by_section(exam_only["clashing"])["L1"]
    assert not any(item["type"] == "meeting" for item in clashing["clashes"])
    assert {"type": "midsem", "date": "09/10", "session": "AN1"} in [
        {key: item[key] for key in ("type", "date", "session")} for item in clashing["clashes"]
    ]
    clear = feasible_sections("CHEM F111", [{"course_code": "BIO F101", "section": "L1"}], db)
    assert "L1" in by_section(clear)


def test_missing_exam_times_keep_a_section_out_of_the_strict_set(db):
    report = assess_sections("BIO F101", [{"course_code": "BIO F111", "section": "L1"}], db)
    feasible = by_section(report["feasible"])
    unverified = by_section(report["unverified"])
    assert "L1" in by_section(report["clashing"])
    assert "T1" not in feasible
    assert "T1" in unverified
    assert any(item["type"] == "midsem" for item in unverified["T1"]["unverified"])
    assert "P1" in by_section(report["cancelled"])
    empty = assess_sections("BIO F101", [], db)
    assert "T1" in by_section(empty["feasible"])
    assert "P1" not in by_section(empty["feasible"])


def test_hour_legend_maps_eight_am_to_the_printed_hour():
    legend = load_hour_legend(raw_dir() / "timetable.pdf")
    assert hours_for_clock(legend, 8, "AM") == [1]
    assert hours_for_clock(legend, 5, "PM") == [10]
    assert hours_for_clock(legend, 8, "PM") == []


def test_section_choice_keeps_a_feasible_section_and_applies_preferences():
    early = section(section="L1", slots=[{"day": "Mon", "hour": 1}])
    later = section(section="L2", slots=[{"day": "Mon", "hour": 4}, {"day": "Wed", "hour": 4}])
    wide = section(section="L3", slots=[{"day": "Mon", "hour": 1}, {"day": "Mon", "hour": 5}])
    chosen = choose_among(
        [early, later],
        [section(section="L9", clashes=[{"type": "meeting", "day": "Mon", "hour": 2}])],
        [],
        held_slots=[],
        avoid_hours=set(),
        free_days=set(),
        avoid_gaps=False,
        compact=False,
    )
    assert chosen["status"] == "chosen"
    assert chosen["section"]["section"] == "L1"
    assert "meeting" in chosen["section"]["reason"]

    avoided = choose_among(
        [early, later],
        [],
        [],
        held_slots=[],
        avoid_hours={1},
        free_days=set(),
        avoid_gaps=False,
        compact=False,
    )
    assert avoided["section"]["section"] == "L2"

    monday = choose_among(
        [early, section(section="T1", slots=[{"day": "Thu", "hour": 4}])],
        [],
        [],
        held_slots=[],
        avoid_hours=set(),
        free_days={"Mon"},
        avoid_gaps=False,
        compact=False,
    )
    assert monday["section"]["section"] == "T1"

    gaps = choose_among(
        [wide, later],
        [],
        [],
        held_slots=[],
        avoid_hours=set(),
        free_days=set(),
        avoid_gaps=True,
        compact=False,
    )
    assert gaps["section"]["section"] == "L2"

    compact = choose_among(
        [wide, later],
        [],
        [],
        held_slots=[],
        avoid_hours=set(),
        free_days=set(),
        avoid_gaps=False,
        compact=True,
    )
    assert compact["section"]["section"] == "L2"

    blocked = choose_among(
        [],
        [section(section="L1", clashes=[{"type": "midsem", "date": "09/10", "session": "AN1"}])],
        [],
        held_slots=[],
        avoid_hours=set(),
        free_days=set(),
        avoid_gaps=False,
        compact=False,
    )
    assert blocked["status"] == "not_matched"


def test_unknown_held_section_does_not_count_as_a_free_timetable(db):
    report = assess_sections("BIO F101", [{"course_code": "CS F000", "section": "L9"}], db)
    assert report["feasible"] == []
    assert report["unverified"]
    assert report["warnings"]
