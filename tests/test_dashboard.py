"""The dashboard serves the database through the eligibility, preference, and timetable stages."""

from __future__ import annotations

import json
import threading
from urllib.request import Request, urlopen

import pytest

from bitsrec.dashboard.server import make_server
from bitsrec.paths import processed_dir

DB = processed_dir() / "bits.sqlite"


@pytest.fixture
def db():
    if not DB.is_file():
        pytest.skip("processed database is not built")
    return DB


@pytest.fixture
def base_url(db):
    server = make_server("127.0.0.1", 0, db)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()


def profile():
    return {
        "campus": "pilani",
        "admission_year": 2024,
        "degree": "B.E. Computer Science",
        "current_semester": 5,
        "completed_courses": [],
        "current_courses": [],
        "interests": [],
    }


def post(base_url, path, payload):
    request = Request(
        base_url + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def test_home_page_has_the_profile_form_and_query_box(base_url):
    with urlopen(base_url + "/") as response:
        page = response.read().decode("utf-8")
    assert response.status == 200
    assert 'id="query"' in page
    assert "Update recommendations" in page
    assert "CS F211" not in page or "placeholder" in page
    with urlopen(base_url + "/api/example") as example:
        body = json.loads(example.read().decode("utf-8"))
    assert body["degree"] == "B.E. Computer Science"
    assert body["campus"] == "pilani"
    assert 'id="campus-list"' in page
    assert 'id="degree-list"' in page
    assert 'id="dual-degree-list"' in page
    assert 'id="minor-list"' in page
    assert "overflow-y: scroll" in page
    assert 'id="suggest-completed"' in page
    assert 'id="read-transcript"' in page
    assert 'id="explain"' in page
    assert 'id="plan"' in page
    assert 'id="compare"' in page
    assert 'id="combine"' in page
    assert 'id="freshness"' in page
    with urlopen(base_url + "/api/options") as options:
        choices = json.loads(options.read().decode("utf-8"))
    assert choices["degrees"][2] == "B.E. Computer Science"
    assert "M.Sc. Economics" in choices["degrees"]
    assert "B.E. Computer Science" in choices["dual_degrees"]
    assert "M.Sc. Economics" not in choices["dual_degrees"]
    assert "Programme Yea r" not in " ".join(choices["degrees"])
    assert "Minor in Biomedical Engineering" in choices["minors"]
    assert "Minor in Data" not in choices["minors"]
    assert "Minor in Data Science" in choices["minors"]


def test_feed_cites_handouts_and_keeps_ineligible_courses_out(base_url):
    status, body = post(
        base_url,
        "/api/feed",
        {"profile": profile(), "query": "no midsem", "limit": 100},
    )
    assert status == 200
    matched = {row["course_code"]: row for row in body["recommendations"]}
    unverified = {row["course_code"] for row in body["unverified"]}
    assert "CS F429" in matched
    assert "CS F301" not in matched
    assert "CS F301" not in unverified
    assert "CS U101" not in matched
    assert "CS F215" in unverified
    citation = matched["CS F429"]["matched_properties"][0]["citations"][0]
    assert citation["source_file"] == "173_CS_F429.pdf"
    assert citation["value"] == 0
    cdc = next(
        row
        for row in body["requirements"]
        if row["category"] == "CDC" and row["programme"] == "Computer Science"
    )
    assert cdc["remaining_courses"] == 14
    assert body["registration_cap"]["max_units"] == 25
    assert len(body["recommendations"]) == min(100, body["recommendation_count"])


def test_feed_rejects_an_incomplete_profile(base_url):
    request = Request(
        base_url + "/api/feed",
        data=json.dumps({"profile": {"campus": "pilani"}, "query": ""}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(Exception) as caught:
        urlopen(request)
    assert getattr(caught.value, "code", None) == 400


def test_feed_answers_a_category_query_with_an_explanation(base_url):
    status, body = post(
        base_url,
        "/api/feed",
        {"profile": profile(), "query": "Suggest DELs related to AI.", "limit": 100},
    )
    assert status == 200
    matched = {row["course_code"]: row for row in body["recommendations"]}
    assert "CS F441" in matched
    assert "DEL" in matched["CS F441"]["satisfies"]
    assert "artificial intelligence" in matched["CS F441"]["explanation"].lower()
    assert body["categories"] == ["DEL"]
    assert "CE F417" not in matched
    assert "CE F417" not in {row["course_code"] for row in body["unverified"]}


def test_section_check_reports_a_stored_midsem_clash(base_url):
    status, body = post(
        base_url,
        "/api/sections",
        {
            "course_code": "AN F314",
            "held_sections": [{"course_code": "CHEM F111", "section": "L1"}],
        },
    )
    assert status == 200
    feasible = {row["section"] for row in body["feasible"]}
    clashing = {row["section"]: row for row in body["clashing"]}
    assert "L1" not in feasible
    assert "L1" in clashing
    assert {"type": "midsem", "date": "09/10", "session": "AN1"} in [
        {key: item[key] for key in ("type", "date", "session")}
        for item in clashing["L1"]["clashes"]
        if item["type"] == "midsem"
    ]


def test_suggest_transcript_explain_plan_compare_and_timetable(base_url):
    status, suggested = post(base_url, "/api/suggest-completed", {"profile": profile()})
    assert status == 200
    assert suggested["applied"] is False
    assert suggested["needs_confirmation"] is True
    assert suggested["suggested"]
    assert all(item["chart_semester"] < 5 for item in suggested["suggested"])
    assert suggested["skipped_without_position"] > 0

    status, transcript = post(
        base_url,
        "/api/transcript",
        {"text": "CS F211 A\nCS F212 NC\nnot a course"},
    )
    assert status == 200
    by_code = {row["course_code"]: row for row in transcript["courses"]}
    assert by_code["CS F211"]["preselected"] is True
    assert by_code["CS F212"]["preselected"] is False
    assert transcript["applied"] is False

    status, explained = post(
        base_url,
        "/api/explain",
        {"profile": {**profile(), "completed_courses": ["CS F211"]}, "course_code": "CS F211"},
    )
    assert status == 200
    assert explained["status"] == "not_eligible"
    assert "completed" in explained["reason"]

    status, plan = post(base_url, "/api/plan", {"profile": profile()})
    assert status == 200
    assert plan["terms"]
    assert "not the official semester chart" in plan["note"]
    placed = {course["course_code"] for term in plan["terms"] for course in term["courses"]}
    assert "CS F211" in placed

    status, compared = post(
        base_url,
        "/api/compare",
        {"profile": profile(), "course_codes": ["CS F429"]},
    )
    assert status == 200
    handout = compared["courses"][0]["handout"]
    assert handout["source_file"] == "173_CS_F429.pdf"
    assert handout["has_midsem"] == 0

    status, combined = post(
        base_url,
        "/api/combine",
        {"profile": profile(), "course_codes": ["BIO F101", "CHEM F111"], "query": ""},
    )
    assert status == 200
    assert combined["complete"] is True
    assert {row["course_code"] for row in combined["chosen"]} == {"BIO F101", "CHEM F111"}

    with urlopen(base_url + "/api/freshness") as response:
        freshness = json.loads(response.read().decode("utf-8"))
    assert response.status == 200
    assert any(item["document"] == "Bulletin" and item["present"] for item in freshness["documents"])
