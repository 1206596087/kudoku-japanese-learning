PRAGMA foreign_keys = ON;

CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE levels (
    code TEXT PRIMARY KEY,
    sort_order INTEGER NOT NULL UNIQUE,
    label_zh TEXT NOT NULL,
    goal_zh TEXT NOT NULL
);

CREATE TABLE dictionary_sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    homepage TEXT NOT NULL,
    purpose_zh TEXT NOT NULL,
    license_note_zh TEXT NOT NULL,
    imported_at TEXT
);

CREATE TABLE grammar_points (
    id TEXT PRIMARY KEY,
    level_code TEXT NOT NULL REFERENCES levels(code),
    title_zh TEXT NOT NULL,
    pattern TEXT NOT NULL,
    meaning_zh TEXT NOT NULL,
    formation_zh TEXT NOT NULL,
    rationale_zh TEXT NOT NULL,
    nuance_zh TEXT NOT NULL,
    common_mistakes_zh TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'reviewed', 'published'))
);

CREATE TABLE sentences (
    id TEXT PRIMARY KEY,
    level_code TEXT NOT NULL REFERENCES levels(code),
    kind TEXT NOT NULL CHECK (kind IN ('core', 'variant', 'real', 'personal')),
    parent_sentence_id TEXT REFERENCES sentences(id),
    variant_type TEXT CHECK (
        variant_type IS NULL OR
        variant_type IN ('lexical', 'form', 'context', 'listening', 'personal')
    ),
    text TEXT NOT NULL,
    reading TEXT NOT NULL,
    translation_zh TEXT NOT NULL,
    scene_zh TEXT NOT NULL,
    register TEXT NOT NULL CHECK (
        register IN ('polite', 'plain', 'casual', 'formal', 'mixed')
    ),
    grammar_note_zh TEXT NOT NULL,
    audio_text TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'original'
        CHECK (source_type IN ('original', 'generated', 'licensed', 'user_import')),
    source_ref TEXT,
    copyright_status TEXT NOT NULL DEFAULT 'original'
        CHECK (copyright_status IN ('original', 'licensed', 'public_domain', 'user_provided')),
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'reviewed', 'published')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (
        (kind = 'core' AND parent_sentence_id IS NULL AND variant_type IS NULL) OR
        (kind <> 'core' AND parent_sentence_id IS NOT NULL)
    )
);

CREATE TABLE sentence_grammar (
    sentence_id TEXT NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
    grammar_id TEXT NOT NULL REFERENCES grammar_points(id),
    role TEXT NOT NULL CHECK (role IN ('new', 'review')),
    PRIMARY KEY (sentence_id, grammar_id)
);

CREATE TABLE sentence_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sentence_id TEXT NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    surface TEXT NOT NULL,
    reading TEXT NOT NULL,
    meaning_zh TEXT NOT NULL,
    function_zh TEXT NOT NULL,
    UNIQUE (sentence_id, position)
);

-- Plain text and ruby alignment are stored separately. The database never
-- stores HTML <ruby> tags; the app will render these segments later.
CREATE TABLE sentence_ruby_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sentence_id TEXT NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    surface TEXT NOT NULL,
    reading TEXT,
    UNIQUE (sentence_id, position)
);

-- These tables are intentionally ready for the later JMdict/KANJIDIC2/
-- JmdictFurigana imports. They are empty in the first sentence sample.
CREATE TABLE lexemes (
    id INTEGER PRIMARY KEY,
    jmdict_seq INTEGER UNIQUE,
    level_code TEXT REFERENCES levels(code),
    common_rank INTEGER
);

CREATE TABLE lexeme_forms (
    id INTEGER PRIMARY KEY,
    lexeme_id INTEGER NOT NULL REFERENCES lexemes(id) ON DELETE CASCADE,
    written_form TEXT NOT NULL,
    reading TEXT NOT NULL,
    is_common INTEGER NOT NULL DEFAULT 0 CHECK (is_common IN (0, 1)),
    UNIQUE (lexeme_id, written_form, reading)
);

CREATE TABLE lexeme_glosses (
    id INTEGER PRIMARY KEY,
    lexeme_id INTEGER NOT NULL REFERENCES lexemes(id) ON DELETE CASCADE,
    language_code TEXT NOT NULL,
    gloss TEXT NOT NULL,
    source TEXT NOT NULL,
    is_machine_generated INTEGER NOT NULL DEFAULT 0
        CHECK (is_machine_generated IN (0, 1))
);

CREATE TABLE kanji_entries (
    literal TEXT PRIMARY KEY,
    meanings_zh TEXT,
    grade INTEGER,
    stroke_count INTEGER,
    jlpt_legacy_level INTEGER
);

CREATE TABLE kanji_readings (
    id INTEGER PRIMARY KEY,
    literal TEXT NOT NULL REFERENCES kanji_entries(literal) ON DELETE CASCADE,
    reading_type TEXT NOT NULL CHECK (reading_type IN ('on', 'kun', 'nanori')),
    reading TEXT NOT NULL,
    UNIQUE (literal, reading_type, reading)
);

CREATE TABLE furigana_segments (
    id INTEGER PRIMARY KEY,
    lexeme_form_id INTEGER NOT NULL REFERENCES lexeme_forms(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    surface TEXT NOT NULL,
    reading TEXT,
    UNIQUE (lexeme_form_id, position)
);

-- Dictionary enrichment stays separate from the upstream JMdict-shaped
-- tables so dictionary updates do not overwrite local teaching notes.
CREATE TABLE lexeme_details (
    lexeme_id INTEGER PRIMARY KEY REFERENCES lexemes(id) ON DELETE CASCADE,
    part_of_speech TEXT,
    usage_notes TEXT,
    memory_note_zh TEXT,
    memory_note_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (memory_note_status IN ('pending', 'generated', 'reviewed'))
);

CREATE TABLE sentence_lexemes (
    sentence_id TEXT NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    lexeme_id INTEGER REFERENCES lexemes(id) ON DELETE SET NULL,
    lexeme_form_id INTEGER REFERENCES lexeme_forms(id) ON DELETE SET NULL,
    surface TEXT NOT NULL,
    dictionary_form TEXT NOT NULL,
    reading TEXT NOT NULL,
    part_of_speech TEXT NOT NULL,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    PRIMARY KEY (sentence_id, position)
);

CREATE TABLE learner_lexeme_state (
    learner_id INTEGER NOT NULL REFERENCES learner_profiles(id) ON DELETE CASCADE,
    lexeme_id INTEGER NOT NULL REFERENCES lexemes(id) ON DELETE CASCADE,
    recognition_score INTEGER NOT NULL DEFAULT 0 CHECK (recognition_score BETWEEN 0 AND 5),
    production_score INTEGER NOT NULL DEFAULT 0 CHECK (production_score BETWEEN 0 AND 5),
    review_count INTEGER NOT NULL DEFAULT 0,
    due_at TEXT,
    PRIMARY KEY (learner_id, lexeme_id)
);

CREATE TABLE reading_works (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_file_url TEXT NOT NULL,
    copyright_status TEXT NOT NULL,
    orthography TEXT,
    guide_zh TEXT,
    guide_status TEXT NOT NULL DEFAULT 'generated',
    era_note_zh TEXT,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE reading_sentences (
    id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL REFERENCES reading_works(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    text TEXT NOT NULL,
    reading TEXT NOT NULL,
    translation_zh TEXT,
    difficulty_score REAL,
    character_count INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'source'
        CHECK (status IN ('source', 'segmented', 'translated', 'reviewed')),
    UNIQUE (work_id, position)
);

CREATE TABLE reading_sentence_ruby_segments (
    reading_sentence_id TEXT NOT NULL REFERENCES reading_sentences(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    surface TEXT NOT NULL,
    reading TEXT,
    PRIMARY KEY (reading_sentence_id, position)
);

CREATE TABLE reading_sentence_lexemes (
    reading_sentence_id TEXT NOT NULL REFERENCES reading_sentences(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    lexeme_id INTEGER REFERENCES lexemes(id) ON DELETE SET NULL,
    surface TEXT NOT NULL,
    dictionary_form TEXT NOT NULL,
    reading TEXT NOT NULL,
    part_of_speech TEXT NOT NULL,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    PRIMARY KEY (reading_sentence_id, position)
);

CREATE TABLE learner_profiles (
    id INTEGER PRIMARY KEY,
    display_name TEXT NOT NULL,
    current_level_code TEXT NOT NULL REFERENCES levels(code),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE learner_sentence_state (
    learner_id INTEGER NOT NULL REFERENCES learner_profiles(id) ON DELETE CASCADE,
    sentence_id TEXT NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
    listening_score INTEGER NOT NULL DEFAULT 0 CHECK (listening_score BETWEEN 0 AND 5),
    comprehension_score INTEGER NOT NULL DEFAULT 0 CHECK (comprehension_score BETWEEN 0 AND 5),
    production_score INTEGER NOT NULL DEFAULT 0 CHECK (production_score BETWEEN 0 AND 5),
    review_count INTEGER NOT NULL DEFAULT 0,
    due_at TEXT,
    fsrs_card_json TEXT,
    last_rating INTEGER,
    last_reviewed_at TEXT,
    lapse_count INTEGER NOT NULL DEFAULT 0,
    last_error_type TEXT,
    PRIMARY KEY (learner_id, sentence_id)
);

CREATE TABLE learner_grammar_state (
    learner_id INTEGER NOT NULL REFERENCES learner_profiles(id) ON DELETE CASCADE,
    grammar_id TEXT NOT NULL REFERENCES grammar_points(id) ON DELETE CASCADE,
    recognition_score INTEGER NOT NULL DEFAULT 0 CHECK (recognition_score BETWEEN 0 AND 5),
    production_score INTEGER NOT NULL DEFAULT 0 CHECK (production_score BETWEEN 0 AND 5),
    review_count INTEGER NOT NULL DEFAULT 0,
    due_at TEXT,
    PRIMARY KEY (learner_id, grammar_id)
);

CREATE TABLE reading_sections (
    id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL REFERENCES reading_works(id) ON DELETE CASCADE,
    section_number INTEGER NOT NULL,
    title_zh TEXT NOT NULL,
    start_position INTEGER NOT NULL,
    end_position INTEGER NOT NULL,
    summary_zh TEXT,
    is_generated INTEGER NOT NULL DEFAULT 1 CHECK (is_generated IN (0,1)),
    UNIQUE (work_id, section_number)
);

CREATE TABLE learner_reading_state (
    learner_id INTEGER NOT NULL REFERENCES learner_profiles(id) ON DELETE CASCADE,
    work_id TEXT NOT NULL REFERENCES reading_works(id) ON DELETE CASCADE,
    last_sentence_id TEXT REFERENCES reading_sentences(id) ON DELETE SET NULL,
    last_position INTEGER NOT NULL DEFAULT 0,
    progress_percent REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (learner_id, work_id)
);

CREATE TABLE reading_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id INTEGER NOT NULL REFERENCES learner_profiles(id) ON DELETE CASCADE,
    reading_sentence_id TEXT NOT NULL REFERENCES reading_sentences(id) ON DELETE CASCADE,
    read_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE reading_bookmarks (
    learner_id INTEGER NOT NULL REFERENCES learner_profiles(id) ON DELETE CASCADE,
    reading_sentence_id TEXT NOT NULL REFERENCES reading_sentences(id) ON DELETE CASCADE,
    note_zh TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (learner_id, reading_sentence_id)
);

CREATE TABLE learner_saved_lexemes (
    learner_id INTEGER NOT NULL REFERENCES learner_profiles(id) ON DELETE CASCADE,
    lexeme_id INTEGER NOT NULL REFERENCES lexemes(id) ON DELETE CASCADE,
    source_sentence_id TEXT,
    source_type TEXT NOT NULL DEFAULT 'dictionary',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (learner_id, lexeme_id)
);

CREATE TABLE literary_expression_rules (
    id TEXT PRIMARY KEY,
    pattern TEXT NOT NULL,
    title_zh TEXT NOT NULL,
    explanation_zh TEXT NOT NULL,
    nuance_zh TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'generated'
        CHECK (status IN ('generated','reviewed'))
);

CREATE TABLE learner_preferences (
    learner_id INTEGER PRIMARY KEY REFERENCES learner_profiles(id) ON DELETE CASCADE,
    desired_retention REAL NOT NULL DEFAULT 0.9
        CHECK (desired_retention BETWEEN 0.7 AND 0.99)
);

CREATE TABLE app_review_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id INTEGER NOT NULL REFERENCES learner_profiles(id) ON DELETE CASCADE,
    sentence_id TEXT NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 4),
    review_mode TEXT NOT NULL,
    error_type TEXT,
    card_json TEXT NOT NULL,
    reviewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE app_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id INTEGER NOT NULL UNIQUE REFERENCES learner_profiles(id) ON DELETE CASCADE,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    password_iterations INTEGER NOT NULL DEFAULT 310000,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_login_at TEXT
);

CREATE TABLE app_sessions (
    token_hash TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES app_accounts(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE learner_sentence_exposure (
    learner_id INTEGER NOT NULL REFERENCES learner_profiles(id) ON DELETE CASCADE,
    sentence_id TEXT NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
    shown_count INTEGER NOT NULL DEFAULT 0,
    completed_count INTEGER NOT NULL DEFAULT 0,
    last_shown_at TEXT,
    last_completed_at TEXT,
    PRIMARY KEY (learner_id,sentence_id)
);

CREATE TABLE learner_unit_state (
    learner_id INTEGER NOT NULL REFERENCES learner_profiles(id) ON DELETE CASCADE,
    root_sentence_id TEXT NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
    shown_count INTEGER NOT NULL DEFAULT 0,
    completed_sentences INTEGER NOT NULL DEFAULT 0,
    mastery_percent REAL NOT NULL DEFAULT 0,
    last_activity_at TEXT,
    PRIMARY KEY (learner_id,root_sentence_id)
);

CREATE INDEX idx_sentences_level_kind ON sentences(level_code, kind);
CREATE INDEX idx_sentences_parent ON sentences(parent_sentence_id);
CREATE INDEX idx_sentence_grammar_grammar ON sentence_grammar(grammar_id, role);
CREATE INDEX idx_chunks_sentence_position ON sentence_chunks(sentence_id, position);
CREATE INDEX idx_ruby_sentence_position ON sentence_ruby_segments(sentence_id, position);
CREATE INDEX idx_forms_written ON lexeme_forms(written_form);
CREATE INDEX idx_forms_reading ON lexeme_forms(reading);
CREATE INDEX idx_glosses_lookup ON lexeme_glosses(language_code, gloss);
CREATE INDEX idx_glosses_lexeme_language ON lexeme_glosses(lexeme_id, language_code, id);
CREATE INDEX idx_lexemes_common_rank ON lexemes(common_rank, id);
CREATE INDEX idx_sentence_lexemes_lexeme ON sentence_lexemes(lexeme_id);
CREATE INDEX idx_reading_sentences_work ON reading_sentences(work_id, position);
CREATE INDEX idx_reading_lexemes_lexeme ON reading_sentence_lexemes(lexeme_id);
CREATE INDEX idx_reading_sections_work ON reading_sections(work_id, section_number);
CREATE INDEX idx_reading_history_learner ON reading_history(learner_id, read_at DESC);
CREATE INDEX idx_reading_bookmarks_learner ON reading_bookmarks(learner_id, created_at DESC);
CREATE INDEX idx_saved_lexemes_learner ON learner_saved_lexemes(learner_id, created_at DESC);
CREATE INDEX idx_review_logs_sentence ON app_review_logs(learner_id, sentence_id, reviewed_at);
CREATE INDEX idx_sessions_expiry ON app_sessions(expires_at);
CREATE INDEX idx_exposure_selection ON learner_sentence_exposure(learner_id,shown_count,last_shown_at);
CREATE INDEX idx_unit_state_learner ON learner_unit_state(learner_id,last_activity_at DESC);

