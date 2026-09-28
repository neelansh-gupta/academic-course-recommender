"""Eligibility uses stored lists, printed prerequisites, and timetable cohort notes."""

from __future__ import annotations

import pytest

from bitsrec.engine import analyze_profile, eligible_courses
from bitsrec.engine.prerequisites import prerequisite_status
from bitsrec.paths import processed_dir

DB = processed_dir() / "bits.sqlite"
CS_CDC = [
    "CS F211",
    "CS F212",
    "CS F213",
    "CS F214",
    "CS F215",
    "CS F222",
    "CS F241",
    "CS F301",
    "CS F303",
    "CS F342",
    "CS F351",
    "CS F363",
    "CS F364",
    "CS F372",
]


def _same(left: str, right: str) -> bool:
    return left == right


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
    }
    base.update(overrides)
    return base


def requirement(result, category, programme):
    rows = [
        row
        for row in result["requirements"]
        if row["category"] == category and row["programme"] == programme
    ]
    assert len(rows) == 1
    return rows[0]


def by_code(result):
    return {row["course_code"]: row for row in result["eligible"]}


def test_slash_groups_and_or_branches_follow_the_printed_line():
    machines = "ECE/EEE/INSTR F211: Electrical Machines, and ECE/EEE/INSTR F242: Control Systems"
    assert (
        prerequisite_status(
            machines,
            ["INSTR F211", "INSTR F242"],
            description_present=True,
            completed={"ECE F211", "EEE F242"},
            equivalent=_same,
        )
        == "satisfied"
    )
    assert (
        prerequisite_status(
            machines,
            ["INSTR F211", "INSTR F242"],
            description_present=True,
            completed={"ECE F211"},
            equivalent=_same,
        )
        == "not_satisfied"
    )
    concrete = "AUE F212: Structural Mechanics or CE F211: Mechanics of Solids"
    assert (
        prerequisite_status(
            concrete,
            ["AUE F212", "CE F211"],
            description_present=True,
            completed={"CE F211"},
            equivalent=_same,
        )
        == "satisfied"
    )
    later_sentence = (
        "ECE/EEE/INSTR F212: Electromagnetic Theory OR PHY F212: Electromagnetic Theory I. "
        "This course will replace ECE F314."
    )
    assert (
        prerequisite_status(
            later_sentence,
            ["INSTR F212", "PHY F212", "ECE F314"],
            description_present=True,
            completed={"PHY F212"},
            equivalent=_same,
        )
        == "satisfied"
    )


def test_missing_description_is_unverified_and_a_printed_equivalent_counts():
    assert (
        prerequisite_status(None, [], description_present=True, completed=set(), equivalent=_same)
        == "none_stated"
    )
    assert (
        prerequisite_status(None, [], description_present=False, completed=set(), equivalent=_same)
        == "unverified"
    )

    def equivalent(required: str, completed: str) -> bool:
        return required == completed or {required, completed} == {"BIO F211", "BIO C211"}

    assert (
        prerequisite_status(
            "BIO F211",
            ["BIO F211"],
            description_present=True,
            completed={"BIO C211"},
            equivalent=equivalent,
        )
        == "satisfied"
    )


def test_profile_requires_the_identity_fields():
    with pytest.raises(ValueError):
        analyze_profile({"degree": "B.E. Computer Science"}, DB)


def test_computer_science_catalogue_and_remaining_cdc(db):
    result = analyze_profile(profile(), db)
    match = result["programmes"][0]
    assert match["catalogue"] == "Computer Science"
    assert match["footer"] == "B. E. Computer Science"
    cdc = requirement(result, "CDC", "Computer Science")
    assert cdc["required_courses_min"] == 14
    assert cdc["required_units_min"] == 48
    assert cdc["remaining_courses"] == 14
    assert cdc["completed_courses"] == 0
    assert "footer" in cdc["source_section"]
    eligible = by_code(result)
    assert [code for code in CS_CDC if code not in eligible] == []
    course = eligible["CS F211"]
    assert course["title"] == "Data Structures & Algorithms"
    assert course["units"] == 4
    assert "CDC" in course["satisfies"]
    assert course["prerequisite_status"] == "none_stated"
    assert course["offered_this_semester"] is False
    assert result["registration_cap"]["clause"] == "1.01"
    assert result["registration_cap"]["max_units"] == 25
    assert eligible_courses(profile(), db)[0]["course_code"] == result["eligible"][0]["course_code"]


def test_completed_cdc_is_removed_and_a_current_course_is_not_completed(db):
    done = analyze_profile(profile(completed_courses=["CS F211"]), db)
    cdc = requirement(done, "CDC", "Computer Science")
    assert "CS F211" not in by_code(done)
    assert cdc["completed_courses"] == 1
    assert cdc["remaining_courses"] == 13
    assert cdc["completed_units"] == 4
    assert cdc["remaining_units"] == 44

    current = analyze_profile(profile(current_courses=["CS F211"]), db)
    assert "CS F211" not in by_code(current)
    assert requirement(current, "CDC", "Computer Science")["completed_courses"] == 0
    assert requirement(current, "CDC", "Computer Science")["remaining_courses"] == 14
    assert current["in_progress"] == ["CS F211"]


def test_printed_or_prerequisite_blocks_until_one_alternative_is_complete(db):
    student = profile(degree="B.E. Architecture and Urban Engineering")
    blocked = analyze_profile(student, db)
    assert blocked["programmes"][0]["catalogue"] == "Architectural And Urban Engineering"
    assert "AUE F313" not in by_code(blocked)

    opened = analyze_profile(profile(degree="B.E. Architecture and Urban Engineering", completed_courses=["CE F211"]), db)
    course = by_code(opened)["AUE F313"]
    assert "CDC" in course["satisfies"]
    assert course["prerequisite_status"] == "satisfied"
    assert course["title"] == "Design of Reinforced Concrete structures"
    assert course["units"] == 3


def test_2026_curriculum_is_limited_to_that_admission_year(db):
    earlier = by_code(analyze_profile(profile(admission_year=2024), db))
    later = by_code(analyze_profile(profile(admission_year=2026), db))
    assert "CS U101" not in earlier
    assert "OPEL" in later["CS U101"]["satisfies"]
    assert later["CS U101"]["offered_this_semester"] is True


def test_dual_degree_keeps_both_cdc_lists(db):
    result = analyze_profile(
        profile(dual_degree="M.Sc. Economics", completed_courses=["CS F211"]),
        db,
    )
    catalogues = {item["input_field"]: item["catalogue"] for item in result["programmes"]}
    assert catalogues == {"degree": "Computer Science", "dual_degree": "Economics"}
    assert requirement(result, "CDC", "Computer Science")["remaining_courses"] == 13
    economics = requirement(result, "CDC", "Economics")
    assert economics["remaining_courses"] == 14
    assert economics["completed_courses"] == 0
    assert "CDC" in by_code(result)["ECON F211"]["satisfies"]
    assert "CS F211" not in by_code(result)


def test_three_humanities_courses_meet_the_pool_minimum(db):
    result = analyze_profile(
        profile(completed_courses=["HSS F222", "HSS F223", "HSS F224"]),
        db,
    )
    huel = requirement(result, "HUEL", "Integrated First Degree")
    assert huel["required_units_min"] == 8
    assert huel["required_courses_min"] == 3
    assert huel["completed_courses"] == 3
    assert huel["completed_units"] == 9
    assert huel["remaining_courses"] == 0
    assert huel["remaining_units"] == 0
    humanities = by_code(result)["HSS F221"]
    assert "HUEL" not in humanities["satisfies"]
    assert "OPEL" in humanities["satisfies"]


def test_listed_electives_stay_eligible_including_a_higher_degree_del(db):
    eligible = by_code(analyze_profile(profile(), db))
    assert eligible["BITS F464"]["prerequisite_status"] == "none_stated"
    assert "DEL" in eligible["BITS F464"]["satisfies"]
    assert "DEL" in eligible["CS G513"]["satisfies"]
    assert "OPEL" not in eligible["CS G513"]["satisfies"]


def test_course_without_a_description_is_not_in_the_strict_set(db):
    assert "BITS F421T" not in by_code(analyze_profile(profile(), db))


def test_unknown_or_truncated_programme_does_not_invent_a_cdc_list(db):
    unknown = analyze_profile(profile(degree="B.E. Underwater Basket Weaving"), db)
    assert unknown["programmes"][0]["catalogue"] is None
    assert all(row["category"] != "CDC" for row in unknown["requirements"])
    assert any("not inferred" in warning for warning in unknown["warnings"])

    electrical = analyze_profile(profile(degree="B.E. Electrical & Electronics"), db)
    assert electrical["programmes"][0]["catalogue"] is None
    assert electrical["programmes"][0]["footer"] == "B.E. Electrical & Electronics"
    assert all(row["category"] != "CDC" for row in electrical["requirements"])
