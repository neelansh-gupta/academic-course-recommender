# BITS Academic Course Recommender

An agentic course recommender for BITS students. Academic eligibility is computed from the supplied regulations, bulletin, timetable, and handouts. Preference matching ranks only the courses that this deterministic layer has already marked eligible. Intent, abbreviation expansion, and explanations stay tied to stored fields.

This repository implements Stages 1–5: data pre-processing, deterministic eligibility, preference matching, timetable clash checks, and the student dashboard.

## Architecture

1. **Deterministic academic engine** (`src/bitsrec/engine/`) — remaining CDC, DEL, HUEL, OPEL, and minor requirements, prerequisites, programme restrictions, and a per-course explanation of why a course is in or out of that set.
2. **Preference matcher** (`src/bitsrec/llm/`) — natural-language preferences against structured handout fields, including category, makeup, and interest phrases, with an explicit unverified state when a property is absent from the source.
3. **Timetable intelligence** (`src/bitsrec/schedule/`) — class, lab, tutorial, midsem, and comprehensive clashes, section choice for one course, and a clash-free section set across a shortlist.
4. **Dashboard** (`src/bitsrec/dashboard/`) — profile form, query box, recommendation feed, confirmed course suggestions, a prerequisite order, comparison, and source file dates.

Stage 1 does not hardcode course lists. It reads the PDFs in `dataset.zip`.

## Layout

```
dataset.zip                          supplied archive (not committed)
schema/schema.sql                    SQLite schema
schema/student_profile.schema.json   profile contract for later stages
src/bitsrec/ingest/                  Stage 1 parsers and pipeline
src/bitsrec/engine/                  Stage 2 eligibility, explanations, minor totals, chart suggestions, prerequisite order
src/bitsrec/llm/                     Stage 3 preference matching
src/bitsrec/schedule/                Stage 4 clash checks and multi-course section search
src/bitsrec/dashboard/               Stage 5 profile, feed, transcript review, comparison, and source dates
data/raw/                            extracted PDFs
data/processed/bits.sqlite           structured dataset
data/examples/                       illustrative profile, not official student data
tests/                               parsers, eligibility, preferences, timetable, and dashboard
```

## Data model

| Entity | What is stored | Source |
| --- | --- | --- |
| Regulation clauses | Numbered clause text and page | Academic Regulations 2023 |
| Programme rules | Minimum degree units, first-degree category ranges, programme CDC/DEL footers | Regulations clause 1.04 and Bulletin Part IV |
| Courses | Code, title, units, description, prerequisite text | Bulletin catalogue, descriptions, timetable, handouts |
| Programme courses | CDC, DEL, HUEL pool, minor courses, semester-required courses, elective slots | Bulletin |
| Handouts | Evaluation components, midsem/compre flags, attendance excerpt, makeup policy | `handouts/*.pdf` |
| Timetable sections | Section, room, day-hour slots, midsem and compre sessions | `timetable.pdf` |
| Equivalents | Course-code equivalences printed in the timetable | Timetable section IX |

`NULL` means the document did not yield that fact. Handouts also list those fields in `unverified_fields_json`. A midsem flag is `1` or `0` only when an evaluation scheme was read; otherwise it stays `NULL`. Minor course and unit totals are read from the bulletin when a profile names a minor. They are not copied into `programme_rules`.

Courses with computer code 5000 or above are tagged `2026_admissions_only`, which is the restriction printed on the timetable.

## Setup

Python 3.11 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Place `dataset.zip` in the project root. It must contain `Academic-Regulations-2023.pdf`, `bulletin.pdf`, `timetable.pdf`, and `handouts/`.

## Build the dataset

```bash
python -m bitsrec.ingest.run
```

The command unzips the archive into `data/raw/` if needed, then writes:

- `data/processed/bits.sqlite`
- `data/processed/summary.json`
- one JSON export per table (`courses.json`, `handouts.json`, and so on)

Re-running replaces the SQLite file. Parsed PDFs are left in `data/raw/`.

## Tests

```bash
pytest
```

## Stage 2 eligibility

`analyze_profile(profile, db_path)` reads `data/processed/bits.sqlite`. It matches each degree to a bulletin CDC list and a semester-chart footer, subtracts completed courses, and returns the remaining requirements plus the courses that are still academically eligible. `eligible_courses` returns only that list.

A course is included only when a stored list allows it, its printed prerequisites are satisfied or no prerequisite line was printed, and a 2026-only timetable note does not block the student's admission year. A course with no extracted description stays out of that list because its prerequisites are unverified. Humanities and open-elective minima are applied once, including for a dual degree. CDC lists stay separate. The engine does not rank courses and does not check timetable clashes.

`current_semester` is stored and checked, but it does not mark earlier courses complete. Only codes in `completed_courses` count as passed. `explain_course(profile, course_code, db_path)` says why one code is eligible or excluded: already completed, currently registered, an unmet or unverified prerequisite, a 2026-only section, or a list that is not open for this profile.

A minor total is read from the bulletin when the profile names a minor. A minor page that prints its own "courses (min)" and "units (min)" uses that total. Otherwise the general first-degree table is used. The numbers are not hardcoded in the engine.

`suggest_completed(profile, db_path)` lists semester-chart courses whose stored year and half fall before `current_semester`. A chart row with no year is counted as skipped and is not suggested. The result sets `applied` to false. `plan_terms(profile, db_path)` orders unfinished named courses by stored prerequisites and packs them under the registration cap from clause 1.01. Elective pools stay as remaining counts. The plan says it is not the official semester chart.

```python
from bitsrec.engine import analyze_profile

report = analyze_profile(profile, "data/processed/bits.sqlite")
```

## Stage 3 preferences

`recommend(profile, query, db_path)` ranks only the courses `analyze_profile` marked eligible. Pass `academic=` when that report was already computed, and the matcher will reuse it. It reads evaluation flags, attendance status, and makeup text from handouts for the student's campus, and it matches interest phrases against stored titles, descriptions, and handout text.

DEL, HUEL, OPEL, and CDC in the query limit that list to courses that satisfy the named category. `AI` and `ML` also match the spelled-out phrases `artificial intelligence` and `machine learning` when those phrases are stored. A flag is a match only when the handout states it. A null flag stays in `unverified`. A qualitative request such as a lenient makeup policy stays unverified unless the stored makeup text uses that word. A handout from another campus is not used. A course that fails a stated preference is left out of both lists. Each result includes an explanation built from the cited fields.

```python
from bitsrec.llm import recommend

report = recommend(profile, "no midsem and machine learning", "data/processed/bits.sqlite")
```

## Stage 4 timetable

`feasible_sections(course_code, current_sections, db_path)` returns sections that do not clash with the sections already held. `assess_sections` also lists clashing, unverified, cancelled, and already registered sections. `find_clashes` compares a set of sections with one another, including two sections of the same course.

A meeting clash is a shared day and hour, whether the rows are lectures, tutorials, or practicals. A midsem or comprehensive clash is the same printed date and session. A missing time stays unverified and keeps the section out of the strict feasible list. Cancelled rows do not occupy a slot.

`recommend(..., held_sections=[...])` keeps a course when one section is feasible and another clashes, and it records the feasible section. Queries such as "no 8 AM classes", "keep Monday free", "avoid long gaps", and "compact timetable" use the hour legend printed in the timetable. Hour 1 is 8–8:50 AM only because that legend says so.

`combine_sections(course_codes, held_sections, db_path, schedule)` searches for one section per requested course that does not clash with the held sections or with the other chosen sections. A course whose times cannot be compared is left unplaced. The search is limited to the first six courses.

```python
from bitsrec.schedule import feasible_sections

sections = feasible_sections("CHEM F111", [{"course_code": "BIO F101", "section": "L1"}], "data/processed/bits.sqlite")
```

## Stage 5 dashboard

```bash
python -m bitsrec.dashboard
```

Open `http://127.0.0.1:8765`. The form sends a student profile, a preference sentence, and any held sections to the database. The feed lists matched courses with handout citations and a short explanation, and it keeps a separate list when a requested field was not extracted. Held sections are used both by the section check and by the feed when it chooses a section. The example profile button loads `data/examples/student_profile.example.json`; that file is illustrative, not an official academic history.

Degree, dual degree, minor, and campus use the Pilani names printed in the bulletin. Each menu scrolls. The semester field does not fill completed courses.

- **Suggest earlier chart courses** calls `/api/suggest-completed`. The boxes start unchecked. **Add selected to completed** is what writes codes into the profile.
- **Review pasted codes** calls `/api/transcript`. A line that contains a non-completion word such as NC is left unchecked. Nothing is saved until the same confirm button is used.
- **Explain** calls `/api/explain` for one course code.
- **Draft a prerequisite order** calls `/api/plan`.
- **Shortlist**, **Compare**, and **Build a clash-free timetable** call `/api/compare` and `/api/combine`. Comparison uses the handout for the profile campus. A null field stays "not stored".
- **Save on this browser** and **Load saved** keep the profile, query, held sections, and shortlist in `localStorage`. They are not written to the database.
- **Sources** calls `/api/freshness` and shows the file dates of the regulations, bulletin, timetable, and processed database.

`/api/feed` and `/api/sections` remain the recommendation page and the single-course section check. The feed computes eligibility once and passes that report into the preference matcher.
