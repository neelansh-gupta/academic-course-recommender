-- Structured store for the BITS Academic Course Recommender.
-- Every academic fact keeps a source document reference.
-- NULL means the field could not be verified from the supplied documents.

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    document TEXT NOT NULL,
    path TEXT,
    page INTEGER,
    section TEXT,
    note TEXT
);

CREATE TABLE IF NOT EXISTS regulation_clauses (
    clause_id TEXT PRIMARY KEY,
    body TEXT NOT NULL,
    page INTEGER,
    source_document TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS programme_rules (
    id INTEGER PRIMARY KEY,
    programme TEXT NOT NULL,
    degree_family TEXT,
    category TEXT NOT NULL,
    min_units REAL,
    max_units REAL,
    min_courses INTEGER,
    max_courses INTEGER,
    units_raw TEXT,
    courses_raw TEXT,
    notes TEXT,
    source_document TEXT NOT NULL,
    source_page INTEGER,
    source_section TEXT,
    confidence REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS courses (
    course_code TEXT PRIMARY KEY,
    title TEXT,
    department TEXT,
    level TEXT,
    lecture_hours REAL,
    practical_hours REAL,
    tutorial_hours REAL,
    units REAL,
    description TEXT,
    topics_json TEXT,
    prerequisites_text TEXT,
    prerequisite_codes_json TEXT,
    restrictions_text TEXT,
    needs_verification INTEGER NOT NULL DEFAULT 0,
    sources_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS programme_courses (
    id INTEGER PRIMARY KEY,
    programme TEXT NOT NULL,
    course_code TEXT NOT NULL,
    role TEXT NOT NULL,
    year_label TEXT,
    semester_label TEXT,
    units REAL,
    title TEXT,
    source_document TEXT NOT NULL,
    source_page INTEGER,
    source_section TEXT,
    confidence REAL NOT NULL,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_programme_courses_role
    ON programme_courses (programme, role);

CREATE TABLE IF NOT EXISTS course_equivalents (
    course_code TEXT NOT NULL,
    equivalent_code TEXT NOT NULL,
    source_document TEXT NOT NULL,
    source_page INTEGER,
    PRIMARY KEY (course_code, equivalent_code)
);

CREATE TABLE IF NOT EXISTS handouts (
    id INTEGER PRIMARY KEY,
    course_code TEXT NOT NULL,
    source_file TEXT NOT NULL,
    campus TEXT,
    semester_label TEXT,
    instructor_in_charge TEXT,
    instructors TEXT,
    description TEXT,
    syllabus TEXT,
    attendance_excerpt TEXT,
    attendance_status TEXT,
    makeup_policy TEXT,
    has_midsem INTEGER,
    has_compre INTEGER,
    has_project INTEGER,
    has_quiz INTEGER,
    has_assignment INTEGER,
    evaluation_json TEXT,
    evaluation_excerpt TEXT,
    unverified_fields_json TEXT NOT NULL,
    confidence REAL NOT NULL,
    needs_verification INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_handouts_course_file
    ON handouts (course_code, source_file);

CREATE TABLE IF NOT EXISTS timetable_sections (
    id INTEGER PRIMARY KEY,
    com_code INTEGER,
    course_code TEXT NOT NULL,
    title TEXT,
    section TEXT,
    section_kind TEXT,
    instructor TEXT,
    instructors_json TEXT,
    room TEXT,
    days_hours_raw TEXT,
    slots_json TEXT,
    midsem_date TEXT,
    midsem_session TEXT,
    compre_date TEXT,
    compre_session TEXT,
    lecture_hours REAL,
    practical_hours REAL,
    tutorial_hours REAL,
    self_study_hours REAL,
    units REAL,
    cohort_rule TEXT,
    cancelled INTEGER NOT NULL DEFAULT 0,
    source_page INTEGER,
    confidence REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS extraction_log (
    id INTEGER PRIMARY KEY,
    severity TEXT NOT NULL,
    stage TEXT NOT NULL,
    message TEXT NOT NULL,
    source TEXT
);

CREATE INDEX IF NOT EXISTS idx_programme_courses_code ON programme_courses (course_code);
CREATE INDEX IF NOT EXISTS idx_timetable_code ON timetable_sections (course_code);
CREATE INDEX IF NOT EXISTS idx_handouts_code ON handouts (course_code);
