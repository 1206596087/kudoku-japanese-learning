#!/usr/bin/env python3
"""Add dictionary and reading-corpus tables without rebuilding course data."""

from __future__ import annotations

import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATABASE_PATH = ROOT / "japanese_learning.sqlite3"


EXTENSION_SQL = """
CREATE TABLE IF NOT EXISTS lexeme_details (
    lexeme_id INTEGER PRIMARY KEY REFERENCES lexemes(id) ON DELETE CASCADE,
    part_of_speech TEXT,
    usage_notes TEXT,
    memory_note_zh TEXT,
    memory_note_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (memory_note_status IN ('pending', 'generated', 'reviewed'))
);
CREATE TABLE IF NOT EXISTS sentence_lexemes (
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
CREATE TABLE IF NOT EXISTS learner_lexeme_state (
    learner_id INTEGER NOT NULL REFERENCES learner_profiles(id) ON DELETE CASCADE,
    lexeme_id INTEGER NOT NULL REFERENCES lexemes(id) ON DELETE CASCADE,
    recognition_score INTEGER NOT NULL DEFAULT 0 CHECK (recognition_score BETWEEN 0 AND 5),
    production_score INTEGER NOT NULL DEFAULT 0 CHECK (production_score BETWEEN 0 AND 5),
    review_count INTEGER NOT NULL DEFAULT 0,
    due_at TEXT,
    PRIMARY KEY (learner_id, lexeme_id)
);
CREATE TABLE IF NOT EXISTS reading_works (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_file_url TEXT NOT NULL,
    copyright_status TEXT NOT NULL,
    orthography TEXT,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS reading_sentences (
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
CREATE TABLE IF NOT EXISTS reading_sentence_ruby_segments (
    reading_sentence_id TEXT NOT NULL REFERENCES reading_sentences(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    surface TEXT NOT NULL,
    reading TEXT,
    PRIMARY KEY (reading_sentence_id, position)
);
CREATE TABLE IF NOT EXISTS reading_sentence_lexemes (
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
CREATE TABLE IF NOT EXISTS reading_sections (
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
CREATE TABLE IF NOT EXISTS learner_reading_state (
    learner_id INTEGER NOT NULL REFERENCES learner_profiles(id) ON DELETE CASCADE,
    work_id TEXT NOT NULL REFERENCES reading_works(id) ON DELETE CASCADE,
    last_sentence_id TEXT REFERENCES reading_sentences(id) ON DELETE SET NULL,
    last_position INTEGER NOT NULL DEFAULT 0,
    progress_percent REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (learner_id, work_id)
);
CREATE TABLE IF NOT EXISTS reading_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id INTEGER NOT NULL REFERENCES learner_profiles(id) ON DELETE CASCADE,
    reading_sentence_id TEXT NOT NULL REFERENCES reading_sentences(id) ON DELETE CASCADE,
    read_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS reading_bookmarks (
    learner_id INTEGER NOT NULL REFERENCES learner_profiles(id) ON DELETE CASCADE,
    reading_sentence_id TEXT NOT NULL REFERENCES reading_sentences(id) ON DELETE CASCADE,
    note_zh TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (learner_id, reading_sentence_id)
);
CREATE TABLE IF NOT EXISTS learner_saved_lexemes (
    learner_id INTEGER NOT NULL REFERENCES learner_profiles(id) ON DELETE CASCADE,
    lexeme_id INTEGER NOT NULL REFERENCES lexemes(id) ON DELETE CASCADE,
    source_sentence_id TEXT,
    source_type TEXT NOT NULL DEFAULT 'dictionary',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (learner_id, lexeme_id)
);
CREATE TABLE IF NOT EXISTS literary_expression_rules (
    id TEXT PRIMARY KEY,
    pattern TEXT NOT NULL,
    title_zh TEXT NOT NULL,
    explanation_zh TEXT NOT NULL,
    nuance_zh TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'generated'
        CHECK (status IN ('generated','reviewed'))
);
CREATE TABLE IF NOT EXISTS learner_preferences (
    learner_id INTEGER PRIMARY KEY REFERENCES learner_profiles(id) ON DELETE CASCADE,
    desired_retention REAL NOT NULL DEFAULT 0.9
        CHECK (desired_retention BETWEEN 0.7 AND 0.99)
);
CREATE TABLE IF NOT EXISTS app_review_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id INTEGER NOT NULL REFERENCES learner_profiles(id) ON DELETE CASCADE,
    sentence_id TEXT NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 4),
    review_mode TEXT NOT NULL,
    error_type TEXT,
    card_json TEXT NOT NULL,
    reviewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS app_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id INTEGER NOT NULL UNIQUE REFERENCES learner_profiles(id) ON DELETE CASCADE,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    password_iterations INTEGER NOT NULL DEFAULT 310000,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS app_sessions (
    token_hash TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES app_accounts(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS learner_sentence_exposure (
    learner_id INTEGER NOT NULL REFERENCES learner_profiles(id) ON DELETE CASCADE,
    sentence_id TEXT NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
    shown_count INTEGER NOT NULL DEFAULT 0,
    completed_count INTEGER NOT NULL DEFAULT 0,
    last_shown_at TEXT,
    last_completed_at TEXT,
    PRIMARY KEY (learner_id,sentence_id)
);
CREATE TABLE IF NOT EXISTS learner_unit_state (
    learner_id INTEGER NOT NULL REFERENCES learner_profiles(id) ON DELETE CASCADE,
    root_sentence_id TEXT NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
    shown_count INTEGER NOT NULL DEFAULT 0,
    completed_sentences INTEGER NOT NULL DEFAULT 0,
    mastery_percent REAL NOT NULL DEFAULT 0,
    last_activity_at TEXT,
    PRIMARY KEY (learner_id,root_sentence_id)
);
CREATE INDEX IF NOT EXISTS idx_glosses_lookup ON lexeme_glosses(language_code, gloss);
CREATE INDEX IF NOT EXISTS idx_glosses_lexeme_language ON lexeme_glosses(lexeme_id, language_code, id);
CREATE INDEX IF NOT EXISTS idx_lexemes_common_rank ON lexemes(common_rank, id);
CREATE INDEX IF NOT EXISTS idx_sentence_lexemes_lexeme ON sentence_lexemes(lexeme_id);
CREATE INDEX IF NOT EXISTS idx_reading_sentences_work ON reading_sentences(work_id, position);
CREATE INDEX IF NOT EXISTS idx_reading_lexemes_lexeme ON reading_sentence_lexemes(lexeme_id);
CREATE INDEX IF NOT EXISTS idx_reading_sections_work ON reading_sections(work_id, section_number);
CREATE INDEX IF NOT EXISTS idx_reading_history_learner ON reading_history(learner_id, read_at DESC);
CREATE INDEX IF NOT EXISTS idx_reading_bookmarks_learner ON reading_bookmarks(learner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_saved_lexemes_learner ON learner_saved_lexemes(learner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_review_logs_sentence ON app_review_logs(learner_id, sentence_id, reviewed_at);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON app_sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_exposure_selection ON learner_sentence_exposure(learner_id,shown_count,last_shown_at);
CREATE INDEX IF NOT EXISTS idx_unit_state_learner ON learner_unit_state(learner_id,last_activity_at DESC);
"""


def extend_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(EXTENSION_SQL)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(learner_sentence_state)")}
    migrations = {
        "fsrs_card_json": "ALTER TABLE learner_sentence_state ADD COLUMN fsrs_card_json TEXT",
        "last_rating": "ALTER TABLE learner_sentence_state ADD COLUMN last_rating INTEGER",
        "last_reviewed_at": "ALTER TABLE learner_sentence_state ADD COLUMN last_reviewed_at TEXT",
        "lapse_count": "ALTER TABLE learner_sentence_state ADD COLUMN lapse_count INTEGER NOT NULL DEFAULT 0",
        "last_error_type": "ALTER TABLE learner_sentence_state ADD COLUMN last_error_type TEXT",
    }
    for name, statement in migrations.items():
        if name not in columns:
            connection.execute(statement)

    work_columns = {row[1] for row in connection.execute("PRAGMA table_info(reading_works)")}
    work_migrations = {
        "guide_zh": "ALTER TABLE reading_works ADD COLUMN guide_zh TEXT",
        "guide_status": "ALTER TABLE reading_works ADD COLUMN guide_status TEXT NOT NULL DEFAULT 'generated'",
        "era_note_zh": "ALTER TABLE reading_works ADD COLUMN era_note_zh TEXT",
    }
    for name, statement in work_migrations.items():
        if name not in work_columns:
            connection.execute(statement)


def main() -> None:
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        extend_schema(connection)
    print(f"Extended schema safely: {DATABASE_PATH}")


if __name__ == "__main__":
    main()
