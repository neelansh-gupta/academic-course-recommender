"""Unit checks for code normalization and handout field extraction."""

from bitsrec.ingest.codes import find_codes, normalize_code
from bitsrec.ingest.handouts import parse_handout_text
from bitsrec.ingest.timetable import _slots


def test_normalize_course_code():
    assert normalize_code("cs", "f301") == "CS F301"
    assert find_codes("EEE F211/ECE F211 and INSTR F211") == [
        "EEE F211",
        "ECE F211",
        "INSTR F211",
    ]


def test_day_hour_runs_do_not_bleed_across_day_groups():
    slots = _slots(["M", "W", "5", "Th", "10"])
    assert slots == [
        {"day": "Mon", "hour": 5},
        {"day": "Wed", "hour": 5},
        {"day": "Thu", "hour": 10},
    ]
    assert _slots(["T", "7", "8"]) == [
        {"day": "Tue", "hour": 7},
        {"day": "Tue", "hour": 8},
    ]


def test_vertical_evaluation_scheme_marks_midsem_and_makeup():
    text = """
    Course No : CS F301
    Course Title : Principles of Programming Languages
    Instructor-in-Charge : Amit Dua
    Pilani Campus
    First Semester 2026-2027
    1. Course Description:
    The course covers features of programming languages.
    5. Evaluation Scheme:
    Component
    Wt (%)
    Quiz 1
    12.5
    35-40 mins.
    2nd September 2026
    Closed Book
    Mid-Semester Test
    30
    90 mins.
    8th October 2026 (FN)
    Open Book
    Comprehensive
    Examination
    45
    180 mins.
    1st December 2026 (FN)
    Closed Book
    9. Make-up Policy:
    Make-up will be given in case of extreme medical emergency or hospitalization only.
    """
    records = parse_handout_text(text, "164_CS_F301.pdf")
    assert len(records) == 1
    record = records[0]
    assert record["course_code"] == "CS F301"
    assert record["has_midsem"] == 1
    assert record["has_compre"] == 1
    assert record["has_quiz"] == 1
    assert record["has_project"] == 0
    assert "medical emergency" in record["makeup_policy"]
    names = [item["name"] for item in __import__("json").loads(record["evaluation_json"])]
    assert names.count("Quiz 1") == 1
    assert "Mid-Semester Test" in names
    assert all(item["weight"] != 1 for item in __import__("json").loads(record["evaluation_json"]))


def test_missing_evaluation_is_unverified_rather_than_false():
    text = """
    Course No: HSS F343
    Course Title: Professional Ethics
    Instructor-in-charge: A Faculty Member
    """
    record = parse_handout_text(text, "329_HSS_F343.pdf")[0]
    assert record["has_midsem"] is None
    assert "evaluation" in record["unverified_fields"]
    assert record["needs_verification"] == 1


def test_attendance_expectation_is_not_a_numeric_threshold():
    text = """
    Course No.: HSS F222
    Course Title: Linguistics
    Instructor-in-charge: Someone
    5. Evaluation Scheme:
    Mid Semester Test
    30%
    Comprehensive Exam
    40%
    Each student is expected to attend all classes and participate in all evaluation components.
    10. Make-up Policy:
    The make-up for an evaluation component will be given only in genuine cases.
    """
    record = parse_handout_text(text, "318_HSS_F222.pdf")[0]
    assert record["attendance_status"] == "expected_no_numeric_minimum_stated"
    assert record["has_midsem"] == 1
    assert record["has_compre"] == 1


def test_processed_dataset_matches_known_bulletin_facts():
    """Checks facts that are explicit in the supplied PDFs, once ingestion has run."""
    import sqlite3
    from pathlib import Path

    db_path = Path(__file__).resolve().parents[1] / "data" / "processed" / "bits.sqlite"
    if not db_path.exists():
        return
    connection = sqlite3.connect(db_path)
    try:
        units = connection.execute(
            "SELECT min_units FROM programme_rules WHERE category='MINIMUM_DEGREE_UNITS' AND programme='Integrated First Degree'"
        ).fetchone()
        assert units == (144.0,)
        cdc = connection.execute(
            """
            SELECT course_code FROM programme_courses
            WHERE role='CDC' AND programme='Computer Science'
            ORDER BY course_code
            """
        ).fetchall()
        assert [row[0] for row in cdc] == [
            "CS F211", "CS F212", "CS F213", "CS F214", "CS F215", "CS F222",
            "CS F241", "CS F301", "CS F303", "CS F342", "CS F351", "CS F363",
            "CS F364", "CS F372",
        ]
        handout = connection.execute(
            "SELECT has_midsem, has_compre FROM handouts WHERE course_code='CS F301'"
        ).fetchone()
        assert handout == (1, 1)
        slot = connection.execute(
            "SELECT days_hours_raw, midsem_session FROM timetable_sections WHERE course_code='BITS F314' AND section='L1'"
        ).fetchone()
        assert slot[0] == "M W F 8"
        assert slot[1] == "AN1"
    finally:
        connection.close()

