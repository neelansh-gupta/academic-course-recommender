"""Deterministic academic eligibility.

Named CDC, DEL, humanities, and minor lists come from the bulletin tables.
A course is eligible only when one of those lists allows it, its printed
prerequisites hold, and the timetable cohort note does not block the student.
"""

from __future__ import annotations

import re
from pathlib import Path

from bitsrec.engine.catalog import AcademicStore, ListedCourse, RuleRecord, load_store
from bitsrec.engine.prerequisites import prerequisite_status
from bitsrec.ingest.codes import find_codes, normalize_code
from bitsrec.paths import raw_dir

_TOKEN_STOP = {
    "b",
    "e",
    "be",
    "m",
    "sc",
    "msc",
    "of",
    "the",
    "with",
    "programme",
    "program",
    "year",
    "yea",
    "stream",
    "honours",
    "hons",
    "and",
    "in",
    "for",
}
_NUMBER_WORDS = {"twenty-five": 25, "twenty-one": 21, "twenty": 20}
_FIRST_DEGREE = re.compile(r"\b(b\.?\s*e\.?|b\.?\s*pharm|m\.?\s*sc|bba)\b", re.IGNORECASE)
_MATCH_MINIMUM = 0.6


def analyze_profile(profile: dict, db_path: str | Path, explain: str | None = None) -> dict:
    student = _validate_profile(profile)
    store = load_store(db_path)
    warnings: list[str] = []
    programmes = [_resolve_degree(label, raw, store, warnings) for label, raw in _degree_inputs(student)]

    completed = set(student["completed_courses"])
    current = set(student["current_courses"])
    unknown = sorted((completed | current) - set(store.courses))
    if unknown:
        warnings.append(
            "These course codes are not in the extracted catalogue and were not used: " + ", ".join(unknown)
        )
        completed -= set(unknown)
        current -= set(unknown)

    cdc_lists = {item["catalogue"]: store.listings_for(item["catalogue"], "CDC") for item in programmes if item["catalogue"]}
    del_lists = {item["catalogue"]: store.listings_for(item["catalogue"], "DEL") for item in programmes if item["catalogue"]}
    huel = store.listings_for("Humanities Elective Pool", "HUEL")
    named_lists, ps_lists = _chart_courses(store, programmes, cdc_lists)
    minor_name, minor_core, minor_electives = _resolve_minor(student.get("minor"), store, warnings)
    minor_rule = _minor_rule(student.get("minor"), minor_name, warnings)

    buckets = _assign_completed(completed, cdc_lists, del_lists, huel, named_lists, ps_lists)
    requirements = _requirements(
        store, programmes, cdc_lists, del_lists, huel, named_lists, ps_lists, buckets, student["first_degree"], warnings
    )
    if minor_name and minor_rule is not None:
        requirements.append(_minor_row(minor_name, minor_core, minor_electives, completed, minor_rule, store))
    huel_open = _category_open(requirements, "HUEL")
    opel_open = _category_open(requirements, "OPEL")
    del_open = {row["programme"] for row in requirements if row["category"] == "DEL" and _row_open(row)}

    all_cdc = {row.course_code for row in store.listings if row.role == "CDC"}
    audit = {row.course_code for row in store.listings if row.role == "AUDIT"}
    minor_codes = set(minor_core) | set(minor_electives)
    named_codes = set().union(*named_lists.values()) if named_lists else set()
    ps_codes = set().union(*ps_lists.values()) if ps_lists else set()
    student_cdc = set().union(*cdc_lists.values()) if cdc_lists else set()
    student_del = set().union(*del_lists.values()) if del_lists else set()

    candidates: dict[str, dict] = {}

    def add(code: str, category: str, programme: str, listing: ListedCourse | None) -> None:
        if code in completed or code in current or code.startswith("SLOT ") or code.startswith("XXX "):
            return
        slot = candidates.setdefault(code, {"categories": [], "programmes": [], "listing": None})
        if category not in slot["categories"]:
            slot["categories"].append(category)
        if programme not in slot["programmes"]:
            slot["programmes"].append(programme)
        if listing is not None and slot["listing"] is None:
            slot["listing"] = listing

    for programme, rows in cdc_lists.items():
        for code, row in rows.items():
            add(code, "CDC", programme, row)
    for programme, rows in del_lists.items():
        if programme not in del_open:
            continue
        for code, row in rows.items():
            add(code, "DEL", programme, row)
    for programme, codes in named_lists.items():
        for code in codes:
            add(code, "NAMED", programme, None)
    for programme, codes in ps_lists.items():
        for code in codes:
            add(code, "PS_OR_THESIS", programme, None)
    if huel_open:
        for code, row in huel.items():
            add(code, "HUEL", "Humanities Elective Pool", row)
    if opel_open:
        for code in store.courses:
            if _may_be_opel(code, store, all_cdc, audit, student_cdc, student_del, huel, named_codes, ps_codes, minor_codes):
                add(code, "OPEL", "Integrated First Degree", huel.get(code))
    minor_open = minor_name is not None and (minor_rule is None or _category_open(requirements, "MINOR"))
    if minor_open:
        for code, row in minor_core.items():
            add(code, "MINOR_CORE", minor_name, row)
        for code, row in minor_electives.items():
            add(code, "MINOR_ELECTIVE", minor_name, row)

    equivalent = _equivalence(store.equivalents)
    eligible = []
    excluded = {"prerequisite_not_satisfied": 0, "prerequisite_unverified": 0, "cohort": 0}
    focus = _one_code(explain) if explain else None
    focus_drop = None
    for code, slot in sorted(candidates.items()):
        course = store.courses.get(code)
        if course is None:
            status = "unverified"
        else:
            status = prerequisite_status(
                course.prerequisites_text,
                course.prerequisite_codes,
                description_present=course.description_present,
                completed=completed,
                equivalent=equivalent,
            )
        if status == "not_satisfied":
            excluded["prerequisite_not_satisfied"] += 1
            if code == focus:
                text = None if course is None else course.prerequisites_text
                focus_drop = {
                    "course_code": code,
                    "status": "not_eligible",
                    "reason": "A printed prerequisite is not satisfied by the completed courses.",
                    "prerequisite_text": text,
                }
            continue
        if status == "unverified":
            excluded["prerequisite_unverified"] += 1
            if code == focus:
                focus_drop = {
                    "course_code": code,
                    "status": "not_eligible",
                    "reason": "The prerequisite could not be verified from the extracted description, so the course stays out of the strict eligible set.",
                }
            continue
        allowed, cohort_note = _cohort_allows(student["admission_year"], store.sections_by_code.get(code, ()))
        if not allowed:
            excluded["cohort"] += 1
            if code == focus:
                focus_drop = {
                    "course_code": code,
                    "status": "not_eligible",
                    "reason": "Every non-cancelled section is marked for 2026 admissions, and this admission year is earlier.",
                }
            continue
        eligible.append(
            {
                "course_code": code,
                "title": _title(store, code, slot["listing"]),
                "units": _units(store, code, slot["listing"]),
                "satisfies": slot["categories"],
                "programmes": slot["programmes"],
                "prerequisite_status": status,
                "offered_this_semester": _offered(store.sections_by_code.get(code, ())),
                "cohort_note": cohort_note,
                "source_section": None if slot["listing"] is None else slot["listing"].source_section,
                "reasons": _reasons(slot["categories"], slot["programmes"], status),
            }
        )

    result = {
        "programmes": programmes,
        "requirements": requirements,
        "eligible": eligible,
        "excluded_counts": excluded,
        "warnings": warnings,
        "registration_cap": _registration_cap(store.clauses.get("1.01", ""), student["first_degree"]),
        "in_progress": sorted(current),
    }
    if focus:
        hit = next((item for item in eligible if item["course_code"] == focus), None)
        if hit:
            result["explanation"] = {
                "course_code": focus,
                "status": "eligible",
                "reasons": hit["reasons"],
                "course": hit,
            }
        elif focus in completed:
            result["explanation"] = {
                "course_code": focus,
                "status": "not_eligible",
                "reason": "It is already listed as completed, so it is not offered again.",
            }
        elif focus in current:
            result["explanation"] = {
                "course_code": focus,
                "status": "not_eligible",
                "reason": "It is a current course. Current registration is not treated as completion, and the course is not offered again.",
            }
        elif focus_drop:
            result["explanation"] = focus_drop
        else:
            result["explanation"] = {
                "course_code": focus,
                "status": "not_eligible",
                "reason": _why_not_candidate(
                    focus,
                    store,
                    cdc_lists,
                    del_lists,
                    huel,
                    named_codes,
                    ps_codes,
                    minor_codes,
                    all_cdc,
                    audit,
                    student_cdc,
                    student_del,
                    del_open,
                    huel_open,
                    opel_open,
                    minor_open,
                ),
            }
    return result


def eligible_courses(profile: dict, db_path: str | Path) -> list[dict]:
    return analyze_profile(profile, db_path)["eligible"]


def explain_course(profile: dict, course_code: str, db_path: str | Path) -> dict:
    code = _one_code(course_code)
    if not code:
        raise ValueError("Enter one course code, for example CS F211.")
    return analyze_profile(profile, db_path, explain=code)["explanation"]


def _one_code(value: str | None) -> str | None:
    if not value:
        return None
    found = find_codes(str(value))
    if found:
        return found[0]
    parts = re.sub(r"\s+", " ", str(value).upper()).strip().split(" ")
    if len(parts) == 2:
        return normalize_code(parts[0], parts[1])
    return None


def _minor_rule(asked: str | None, stored: str | None, warnings: list[str]) -> dict | None:
    if not stored:
        return None
    from bitsrec.engine.minors import requirement_for

    rule = requirement_for(asked or stored, raw_dir() / "bulletin.pdf")
    if rule is None:
        warnings.append(
            "The bulletin minor page states a minimum size, but that minimum was not read from the bulletin. "
            "Unfinished listed minor courses are included; the unit minimum is unverified."
        )
    return rule


def _minor_row(name: str, core: dict, electives: dict, completed: set[str], rule: dict, store: AcademicStore) -> dict:
    listings = {**electives, **core}
    done = [code for code in sorted(listings) if code in completed]

    def units_of(code: str) -> float | None:
        listed = _listed_units(listings, code)
        return listed if listed is not None else _course_units(store, code)

    missing = [code for code in done if units_of(code) is None]
    record = RuleRecord(
        programme=rule.get("heading") or name,
        category="MINOR",
        min_units=rule.get("min_units"),
        max_units=None,
        min_courses=rule.get("min_courses"),
        max_courses=None,
        units_raw=rule.get("units_raw"),
        courses_raw=rule.get("courses_raw"),
        source_section=rule.get("source_section"),
        source_page=rule.get("source_page"),
    )
    row = _pool_row("MINOR", name, done, record, missing, len(listings), units_of)
    row["note"] = rule.get("note")
    row["specific"] = bool(rule.get("specific"))
    return row


def _why_not_candidate(
    code,
    store,
    cdc_lists,
    del_lists,
    huel,
    named,
    ps,
    minor,
    all_cdc,
    audit,
    student_cdc,
    student_del,
    del_open,
    huel_open,
    opel_open,
    minor_open,
) -> str:
    if code not in store.courses:
        return "This code is not in the extracted catalogue."
    if any(code in rows for rows in cdc_lists.values()):
        return "It is on a matched CDC list, but it was not offered again."
    for programme, rows in del_lists.items():
        if code in rows and programme not in del_open:
            return f"It is a discipline elective for {programme}, and that requirement is not still open."
    if code in huel and not huel_open:
        return "It is in the humanities pool, and that requirement is not still open."
    if code in minor and not minor_open:
        return "It is on the minor list, and the minor minimum read from the bulletin is already met."
    if code in named or code in ps:
        return "It is named on the semester chart, but it was not offered again."
    if code in audit:
        return "It is listed as audit, so it is not treated as an open elective."
    course = store.courses.get(code)
    if course is not None and course.level == "G":
        return "G-level courses are included only when they are printed on this student's own course list."
    if code in all_cdc and code not in student_cdc and code not in student_del and code not in huel:
        return "It belongs to another programme's CDC list, so it is not treated as an open elective."
    if not opel_open:
        return "The open-elective requirement is not still open, and this course is not on another open list."
    return "It is not on an open CDC, elective, humanities, minor, or open-elective list for this profile."


def _validate_profile(profile: dict) -> dict:
    if not isinstance(profile, dict):
        raise ValueError("Student profile must be an object.")
    missing = [
        key
        for key in ("campus", "admission_year", "degree", "current_semester")
        if profile.get(key) in (None, "")
    ]
    if missing:
        raise ValueError("Student profile is missing: " + ", ".join(missing))
    year = profile["admission_year"]
    semester = profile["current_semester"]
    if isinstance(year, bool) or not isinstance(year, int):
        raise ValueError("admission_year must be an integer.")
    if isinstance(semester, bool) or not isinstance(semester, int) or semester < 1:
        raise ValueError("current_semester must be an integer of at least 1.")
    degree = str(profile["degree"]).strip()
    return {
        "admission_year": year,
        "degree": degree,
        "dual_degree": str(profile["dual_degree"]).strip() if profile.get("dual_degree") else None,
        "current_semester": semester,
        "completed_courses": _course_list(profile.get("completed_courses") or []),
        "current_courses": _course_list(profile.get("current_courses") or []),
        "minor": str(profile["minor"]).strip() if profile.get("minor") else None,
        "first_degree": bool(_FIRST_DEGREE.search(degree)),
    }


def _course_list(values: list) -> list[str]:
    codes = []
    for value in values:
        found = find_codes(str(value))
        if found:
            codes.append(found[0])
            continue
        parts = re.sub(r"\s+", " ", str(value).upper()).strip().split(" ")
        if len(parts) == 2:
            codes.append(normalize_code(parts[0], parts[1]))
    return codes


def _degree_inputs(student: dict) -> list[tuple[str, str]]:
    rows = [("degree", student["degree"])]
    if student.get("dual_degree"):
        rows.append(("dual_degree", student["dual_degree"]))
    return rows


def _resolve_degree(label: str, raw: str, store: AcademicStore, warnings: list[str]) -> dict:
    catalogues = sorted({row.programme for row in store.listings if row.role == "CDC"})
    footers = sorted(
        {
            row.programme
            for row in store.rules
            if row.category in {"CDC", "DEL"} and row.source_section and "footer" in row.source_section
        }
    )
    catalogue, catalogue_score, catalogue_tie = _best_match(raw, catalogues)
    footer, footer_score, footer_tie = _best_match(raw, footers)
    if catalogue is None:
        if catalogue_tie:
            warnings.append(
                f"More than one bulletin CDC list matched {raw!r} equally. Named CDC courses were not inferred."
            )
        else:
            warnings.append(
                f"No bulletin CDC list matched {raw!r}. Named CDC courses for that degree were not inferred."
            )
    if footer is None and footer_tie:
        warnings.append(f"More than one semester-chart footer matched {raw!r} equally.")
    elif catalogue is None and footer is not None:
        warnings.append(
            f"Semester-chart footer {footer!r} matched {raw!r}, but no CDC course list matched. Those courses were not inferred."
        )
    return {
        "input": raw,
        "input_field": label,
        "catalogue": catalogue,
        "catalogue_score": round(catalogue_score, 3),
        "footer": footer,
        "footer_score": round(footer_score, 3),
    }


def _tokens(text: str) -> list[str]:
    cleaned = re.sub(r"[^a-z0-9]+", " ", text.lower().replace("&", " and "))
    return [token for token in cleaned.split() if token not in _TOKEN_STOP and len(token) > 1]


def _similar(left: str, right: str) -> bool:
    if left == right:
        return True
    if len(left) < 6 or len(right) < 6:
        return False
    if left.startswith(right) or right.startswith(left):
        return True
    # "architecture" and "architectural" share a stem, and neither word contains the other.
    shared = 0
    for left_char, right_char in zip(left, right):
        if left_char != right_char:
            break
        shared += 1
    shorter = min(len(left), len(right))
    return shared >= 8 and shared >= shorter - 3


def _coverage(query: str, candidate: str) -> tuple[float, bool]:
    left = _tokens(query)
    right = _tokens(candidate)
    if not left or not right:
        return 0.0, False
    used: set[int] = set()
    matched = 0
    for token in left:
        for index, other in enumerate(right):
            if index in used or not _similar(token, other):
                continue
            used.add(index)
            matched += 1
            break
    return matched / max(len(left), len(right)), matched == len(left)


def _best_match(query: str, candidates: list[str]) -> tuple[str | None, float, bool]:
    scored = []
    for candidate in candidates:
        score, covered = _coverage(query, candidate)
        if covered and score >= _MATCH_MINIMUM:
            scored.append((score, len(_tokens(candidate)), candidate))
    if not scored:
        return None, 0.0, False
    scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
    best_score, best_length, best_name = scored[0]
    tied = [item for item in scored if item[0] == best_score and item[1] == best_length]
    if len(tied) > 1:
        return None, best_score, True
    return best_name, best_score, False


def _prefix_match(query: str, candidates: list[str]) -> tuple[str | None, bool]:
    """Match a stored name that is a leading truncation of the printed name."""
    left = _tokens(query)
    scored = []
    for candidate in candidates:
        right = _tokens(candidate)
        if not right or len(right) > len(left):
            continue
        if all(_similar(stored, asked) for stored, asked in zip(right, left)):
            scored.append((len(right), candidate))
    if not scored:
        return None, False
    scored.sort(key=lambda item: (-item[0], item[1]))
    best_length = scored[0][0]
    tied = [item for item in scored if item[0] == best_length]
    if len(tied) > 1:
        return None, True
    return scored[0][1], False


def _resolve_minor(name: str | None, store: AcademicStore, warnings: list[str]):
    if not name:
        return None, {}, {}
    programmes = sorted({row.programme for row in store.listings if row.role == "MINOR_CORE"})
    query = name if name.lower().startswith("minor") else f"Minor in {name}"
    match, _score, tie = _best_match(query, programmes)
    if match is None and not tie:
        match, tie = _prefix_match(query, programmes)
    if match is None:
        message = (
            f"More than one minor list matched {name!r} equally. Minor courses were not inferred."
            if tie
            else f"No minor course list matched {name!r}. Minor courses were not inferred."
        )
        warnings.append(message)
        return None, {}, {}
    return (
        match,
        store.listings_for(match, "MINOR_CORE"),
        store.listings_for(match, "MINOR_ELECTIVE"),
    )


def _chart_courses(store: AcademicStore, programmes: list[dict], cdc_lists: dict[str, dict]):
    student_cdc = set().union(*cdc_lists.values()) if cdc_lists else set()
    named: dict[str, set[str]] = {}
    ps: dict[str, set[str]] = {}
    for item in programmes:
        footer = item["footer"]
        if not footer:
            continue
        for code, row in store.listings_for(footer, "semester_required").items():
            if code not in store.courses or code in student_cdc:
                continue
            target = ps if _is_ps_or_thesis(row) else named
            target.setdefault(footer, set()).add(code)
    return named, ps


def _is_ps_or_thesis(row: ListedCourse) -> bool:
    title = (row.title or "").lower()
    return "practice school" in title or "thesis" in title


def _assign_completed(completed, cdc_lists, del_lists, huel, named_lists, ps_lists) -> dict[str, list[str]]:
    named = set().union(*named_lists.values()) if named_lists else set()
    ps = set().union(*ps_lists.values()) if ps_lists else set()
    buckets = {"CDC": [], "DEL": {}, "HUEL": [], "NAMED": [], "PS_OR_THESIS": [], "OPEL": []}
    for programme in del_lists:
        buckets["DEL"][programme] = []
    for code in sorted(completed):
        if any(code in rows for rows in cdc_lists.values()):
            buckets["CDC"].append(code)
            continue
        placed = False
        for programme, rows in del_lists.items():
            if code in rows:
                buckets["DEL"][programme].append(code)
                placed = True
                break
        if placed:
            continue
        if code in huel:
            buckets["HUEL"].append(code)
        elif code in ps:
            buckets["PS_OR_THESIS"].append(code)
        elif code in named:
            buckets["NAMED"].append(code)
        else:
            buckets["OPEL"].append(code)
    return buckets


def _requirements(store, programmes, cdc_lists, del_lists, huel, named_lists, ps_lists, buckets, first_degree, warnings):
    rows = []
    for item in programmes:
        catalogue = item["catalogue"]
        if not catalogue:
            continue
        footer = item["footer"]
        cdc_rule = _rule(store, footer, "CDC") or _rule(store, "Integrated First Degree", "CDC")
        del_rule = _rule(store, footer, "DEL") or _rule(store, "Integrated First Degree", "DEL")
        cdc_rows = cdc_lists[catalogue]
        done = [code for code in buckets["CDC"] if code in cdc_rows]
        if cdc_rule and cdc_rule.min_courses is not None and cdc_rule.min_courses != len(cdc_rows):
            warnings.append(
                f"{catalogue} CDC footer asks for {cdc_rule.min_courses} courses, while the catalogue list has {len(cdc_rows)}."
            )
        rows.append(_named_row("CDC", catalogue, cdc_rows, done, cdc_rule))
        del_rows = del_lists[catalogue]
        kept, overflow, missing = _fill_pool(
            buckets["DEL"].get(catalogue, []),
            del_rule,
            lambda code, rows=del_rows: _listed_units(rows, code),
        )
        buckets["OPEL"].extend(overflow)
        rows.append(
            _pool_row(
                "DEL",
                catalogue,
                kept,
                del_rule,
                missing,
                len(del_rows),
                lambda code, rows=del_rows: _listed_units(rows, code),
            )
        )
    if first_degree:
        huel_rule = _rule(store, "Integrated First Degree", "HUEL")
        kept, overflow, missing = _fill_pool(buckets["HUEL"], huel_rule, lambda code: _listed_units(huel, code))
        buckets["OPEL"].extend(overflow)
        rows.append(
            _pool_row(
                "HUEL",
                "Integrated First Degree",
                kept,
                huel_rule,
                missing,
                len(huel),
                lambda code: _listed_units(huel, code),
            )
        )
        opel_rule = _rule(store, "Integrated First Degree", "OPEL")
        opel_missing = [code for code in buckets["OPEL"] if _course_units(store, code) is None]
        rows.append(
            _pool_row(
                "OPEL",
                "Integrated First Degree",
                buckets["OPEL"],
                opel_rule,
                opel_missing,
                None,
                lambda code: _course_units(store, code),
            )
        )
        ps_rule = _rule(store, "Integrated First Degree", "PS_OR_THESIS")
        if ps_rule is not None:
            rows.append(_reference_row("PS_OR_THESIS", "Integrated First Degree", ps_rule, buckets["PS_OR_THESIS"]))
        degree_rule = _rule(store, "Integrated First Degree", "DEGREE_TOTAL") or _rule(
            store, "Integrated First Degree", "MINIMUM_DEGREE_UNITS"
        )
        if degree_rule is not None:
            rows.append(_reference_row("DEGREE_TOTAL", "Integrated First Degree", degree_rule, []))
    for programme, codes in named_lists.items():
        done = [code for code in buckets["NAMED"] if code in codes]
        rows.append(
            {
                "category": "NAMED_ON_SEMESTER_CHART",
                "programme": programme,
                "required_units_min": None,
                "required_units_max": None,
                "required_courses_min": None,
                "required_courses_max": None,
                "completed_units": None,
                "completed_courses": len(done),
                "remaining_units": None,
                "remaining_courses": len(codes - set(done)),
                "completed_codes": sorted(done),
                "units_unverified_codes": [],
                "source_section": "Semester-wise pattern",
                "source_page": None,
                "units_raw": None,
                "courses_raw": None,
                "note": "Courses printed on this semester chart outside the CDC list, excluding Practice School and thesis.",
            }
        )
    return rows


def _named_row(category, programme, listings, done, rule: RuleRecord | None) -> dict:
    missing = [code for code in done if _listed_units(listings, code) is None]
    completed_units = sum(_listed_units(listings, code) or 0 for code in done)
    required_units = None if rule is None else rule.min_units
    return _row(
        category,
        programme,
        required_units=required_units,
        required_units_max=None if rule is None else rule.max_units,
        required_courses=None if rule is None or rule.min_courses is None else rule.min_courses,
        required_courses_max=None if rule is None else rule.max_courses,
        completed_units=completed_units,
        completed=done,
        remaining_units=None if required_units is None or missing else max(0.0, required_units - completed_units),
        remaining_courses=max(0, len(listings) - len(done)),
        missing=missing,
        named_courses=len(listings),
        rule=rule,
    )


def _pool_row(category, programme, done, rule, missing, named_courses, units_of) -> dict:
    completed_units = sum(units_of(code) or 0 for code in done)
    required_units = None if rule is None else rule.min_units
    if required_units is None or missing:
        remaining_units = None
    else:
        remaining_units = max(0.0, required_units - completed_units)
    remaining_courses = None
    if rule is not None and rule.min_courses is not None:
        remaining_courses = max(0, rule.min_courses - len(done))
    return _row(
        category,
        programme,
        required_units=required_units,
        required_units_max=None if rule is None else rule.max_units,
        required_courses=None if rule is None else rule.min_courses,
        required_courses_max=None if rule is None else rule.max_courses,
        completed_units=completed_units,
        completed=done,
        remaining_units=remaining_units,
        remaining_courses=remaining_courses,
        missing=missing,
        named_courses=named_courses,
        rule=rule,
    )


def _row(
    category,
    programme,
    *,
    required_units,
    required_units_max,
    required_courses,
    required_courses_max,
    completed_units,
    completed,
    remaining_units,
    remaining_courses,
    missing,
    named_courses,
    rule,
) -> dict:
    return {
        "category": category,
        "programme": programme,
        "required_units_min": required_units,
        "required_units_max": required_units_max,
        "required_courses_min": required_courses,
        "required_courses_max": required_courses_max,
        "named_courses": named_courses,
        "completed_units": completed_units,
        "completed_courses": len(completed),
        "remaining_units": remaining_units,
        "remaining_courses": remaining_courses,
        "completed_codes": sorted(completed),
        "units_unverified_codes": sorted(missing),
        "source_section": None if rule is None else rule.source_section,
        "source_page": None if rule is None else rule.source_page,
        "units_raw": None if rule is None else rule.units_raw,
        "courses_raw": None if rule is None else rule.courses_raw,
    }


def _reference_row(category, programme, rule: RuleRecord, completed: list[str]) -> dict:
    note = None
    if category == "PS_OR_THESIS":
        note = "Printed as alternatives. Remaining Practice School or thesis units are not a single number."
    elif category == "DEGREE_TOTAL":
        note = "Stored degree minimum. Completed units are not summed without a stored map for every category."
    return {
        "category": category,
        "programme": programme,
        "required_units_min": rule.min_units,
        "required_units_max": rule.max_units,
        "required_courses_min": rule.min_courses,
        "required_courses_max": rule.max_courses,
        "named_courses": None,
        "completed_units": None,
        "completed_courses": len(completed),
        "remaining_units": None,
        "remaining_courses": None,
        "completed_codes": sorted(completed),
        "units_unverified_codes": [],
        "source_section": rule.source_section,
        "source_page": rule.source_page,
        "units_raw": rule.units_raw,
        "courses_raw": rule.courses_raw,
        "note": note,
    }


def _fill_pool(codes: list[str], rule: RuleRecord | None, units_of) -> tuple[list[str], list[str], list[str]]:
    if rule is None:
        return list(codes), [], [code for code in codes if units_of(code) is None]
    kept: list[str] = []
    units = 0.0
    missing: list[str] = []
    for code in codes:
        value = units_of(code)
        if value is None:
            missing.append(code)
        else:
            units += value
        kept.append(code)
        courses_met = rule.min_courses is None or len(kept) >= rule.min_courses
        units_met = rule.min_units is None or units >= rule.min_units
        if courses_met and units_met and not missing:
            return kept, codes[len(kept) :], missing
        if missing:
            return list(codes), [], missing
    return kept, [], missing


def _rule(store: AcademicStore, programme: str | None, category: str) -> RuleRecord | None:
    if not programme:
        return None
    found = [row for row in store.rules if row.programme == programme and row.category == category]
    if not found:
        return None
    return sorted(found, key=lambda row: (row.min_units is None, -(row.min_units or 0)))[0]


def _listed_units(listings: dict[str, ListedCourse], code: str) -> float | None:
    row = listings.get(code)
    return None if row is None else row.units


def _course_units(store: AcademicStore, code: str) -> float | None:
    course = store.courses.get(code)
    return None if course is None else course.units


def _category_open(rows: list[dict], category: str) -> bool:
    return any(row["category"] == category and _row_open(row) for row in rows)


def _row_open(row: dict) -> bool:
    if row["category"] == "CDC":
        return (row["remaining_courses"] or 0) > 0
    if row["category"] in {"PS_OR_THESIS", "DEGREE_TOTAL", "NAMED_ON_SEMESTER_CHART"}:
        return False
    if row["units_unverified_codes"]:
        return True
    courses_left = row["remaining_courses"] is not None and row["remaining_courses"] > 0
    units_left = row["remaining_units"] is not None and row["remaining_units"] > 0
    if row["remaining_courses"] is None and row["remaining_units"] is None:
        return False
    return courses_left or units_left


def _may_be_opel(code, store, all_cdc, audit, student_cdc, student_del, huel, named, ps, minor) -> bool:
    if code in student_cdc or code in named or code in ps or code in minor or code in audit:
        return False
    if code in all_cdc and code not in student_del and code not in huel:
        return False
    course = store.courses.get(code)
    if course is None or course.level in {"G", "N"}:
        return False
    return course.level in {"F", "U", None}


def _equivalence(pairs: tuple[tuple[str, str], ...]):
    parent: dict[str, str] = {}

    def find(code: str) -> str:
        parent.setdefault(code, code)
        while parent[code] != code:
            parent[code] = parent[parent[code]]
            code = parent[code]
        return code

    for left, right in pairs:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    def equivalent(required: str, completed: str) -> bool:
        return required == completed or find(required) == find(completed)

    return equivalent


def _cohort_allows(admission_year: int, sections: tuple) -> tuple[bool, str | None]:
    active = [section for section in sections if not section.cancelled]
    if not active:
        return True, None
    restricted = [section for section in active if section.cohort_rule == "2026_admissions_only"]
    open_sections = [section for section in active if section.cohort_rule != "2026_admissions_only"]
    if restricted and not open_sections and admission_year < 2026:
        return False, "2026_admissions_only"
    if restricted and open_sections:
        return True, "another_section_is_open_to_earlier_batches"
    return True, None


def _offered(sections: tuple) -> bool:
    return any(not section.cancelled for section in sections)


def _units(store: AcademicStore, code: str, listing: ListedCourse | None) -> float | None:
    if listing is not None and listing.units is not None:
        return listing.units
    return _course_units(store, code)


def _title(store: AcademicStore, code: str, listing: ListedCourse | None) -> str | None:
    titles = []
    if listing is not None and listing.title:
        titles.append(listing.title)
    titles.extend(store.titles_by_code.get(code, ()))
    course = store.courses.get(code)
    if course is not None and course.title:
        titles.append(course.title)
    clean = [title for title in titles if len(title) <= 80 and " or or" not in title.lower()]
    if not clean:
        return None
    return min(clean, key=len)


def _reasons(categories: list[str], programmes: list[str], status: str) -> list[str]:
    programme = programmes[0] if programmes else "the matched programme"
    labels = {
        "CDC": f"Named compulsory discipline course for {programme}.",
        "DEL": f"Listed discipline elective for {programme}.",
        "HUEL": "Listed in the bulletin humanities elective pool.",
        "OPEL": "Can count toward the open-elective requirement.",
        "NAMED": f"Named on the semester chart for {programme} outside the CDC list.",
        "PS_OR_THESIS": "Practice School or thesis option printed on the semester chart.",
        "MINOR_CORE": f"Listed minor core for {programme}.",
        "MINOR_ELECTIVE": f"Listed minor elective for {programme}.",
    }
    reasons = [labels[category] for category in categories if category in labels]
    if status == "none_stated":
        reasons.append("No prerequisite line was printed in the extracted course description.")
    elif status == "satisfied":
        reasons.append("Printed prerequisites are satisfied by completed courses or documented equivalents.")
    reasons.append("The course is not already completed or currently registered.")
    return reasons


def _registration_cap(clause_body: str, first_degree: bool) -> dict | None:
    first = re.search(
        r"first degree student\s+can register is\s+([a-z]+(?:-[a-z]+)?|\d+)",
        clause_body,
        re.IGNORECASE,
    )
    higher = re.search(
        r"higher degree student\s+can register is\s+([a-z]+(?:-[a-z]+)?|\d+)",
        clause_body,
        re.IGNORECASE,
    )
    if not first and not higher:
        return None
    first_units = _number(first.group(1)) if first else None
    higher_units = _number(higher.group(1)) if higher else None
    return {
        "clause": "1.01",
        "applies_to": "first_degree" if first_degree else None,
        "max_units": first_units if first_degree else None,
        "first_degree_max_units": first_units,
        "higher_degree_max_units": higher_units,
    }


def _number(raw: str) -> int | None:
    token = raw.lower()
    if token.isdigit():
        return int(token)
    return _NUMBER_WORDS.get(token)
