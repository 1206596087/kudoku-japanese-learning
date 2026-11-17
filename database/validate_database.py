#!/usr/bin/env python3
"""Run structural checks on course, dictionary and reading-corpus layers."""

from __future__ import annotations

import sqlite3
from pathlib import Path


DATABASE_PATH = Path(__file__).resolve().parent / "japanese_learning.sqlite3"


def scalar(connection: sqlite3.Connection, query: str) -> int:
    row = connection.execute(query).fetchone()
    assert row is not None
    return int(row[0])


def main() -> None:
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        assert integrity == "ok", integrity

        expected = {"core": 510, "variant": 1530, "real": 3060, "personal": 1020}
        expected_base = {"core": 500, "variant": 1500, "real": 3000, "personal": 1000}
        counts = {
            kind: scalar(connection, f"SELECT COUNT(*) FROM sentences WHERE kind = '{kind}'")
            for kind in expected
        }
        total_count = scalar(connection, "SELECT COUNT(*) FROM sentences")
        base_counts = {
            kind: scalar(connection, f"SELECT COUNT(*) FROM sentences WHERE kind = '{kind}' AND id NOT LIKE 'n1\\_%' ESCAPE '\\'")
            for kind in expected_base
        }
        n1_count = scalar(connection, "SELECT COUNT(*) FROM sentences WHERE level_code='N1'")
        grammar_count = scalar(connection, "SELECT COUNT(*) FROM grammar_points")
        chunk_count = scalar(connection, "SELECT COUNT(*) FROM sentence_chunks")
        ruby_count = scalar(connection, "SELECT COUNT(*) FROM sentence_ruby_segments")

        assert counts == expected, (counts, expected)
        assert base_counts == expected_base, (base_counts, expected_base)
        assert total_count == 6120, total_count
        assert n1_count == 120, n1_count
        assert grammar_count == 60, grammar_count

        missing_chunks = scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM sentences AS s
            WHERE NOT EXISTS (
                SELECT 1 FROM sentence_chunks AS c WHERE c.sentence_id = s.id
            )
            """,
        )
        assert missing_chunks == 0

        missing_ruby = scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM sentences AS s
            WHERE NOT EXISTS (
                SELECT 1 FROM sentence_ruby_segments AS r WHERE r.sentence_id = s.id
            )
            """,
        )
        assert missing_ruby == 0

        missing_kanji_readings = scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM sentences AS s
            WHERE s.text GLOB '*[一-龯]*'
              AND NOT EXISTS (
                  SELECT 1
                  FROM sentence_ruby_segments AS r
                  WHERE r.sentence_id = s.id AND r.reading IS NOT NULL
              )
            """,
        )
        assert missing_kanji_readings == 0

        html_ruby_rows = scalar(
            connection,
            "SELECT COUNT(*) FROM sentences WHERE text LIKE '%<ruby>%' OR text LIKE '%<rt>%'")
        assert html_ruby_rows == 0

        duplicate_texts = scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM (
                SELECT text FROM sentences GROUP BY text HAVING COUNT(*) > 1
            )
            """,
        )
        assert duplicate_texts == 0, duplicate_texts

        wrong_child_counts = scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM sentences AS c
            WHERE c.kind = 'core'
              AND (
                (SELECT COUNT(*) FROM sentences v WHERE v.parent_sentence_id = c.id AND v.kind = 'variant') <> 3
                OR (SELECT COUNT(*) FROM sentences r WHERE r.parent_sentence_id = c.id AND r.kind = 'real') <> 6
                OR (SELECT COUNT(*) FROM sentences p WHERE p.parent_sentence_id = c.id AND p.kind = 'personal') <> 2
              )
            """,
        )
        assert wrong_child_counts == 0

        wrong_grammar_roles = scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM sentences AS s
            JOIN sentence_grammar AS sg ON sg.sentence_id = s.id
            WHERE (s.kind <> 'core' AND sg.role <> 'review')
            """,
        )
        assert wrong_grammar_roles == 0

        empty_required_fields = scalar(
            connection,
            """
            SELECT COUNT(*) FROM sentences
            WHERE trim(text) = '' OR trim(reading) = '' OR trim(translation_zh) = ''
               OR trim(grammar_note_zh) = '' OR trim(scene_zh) = ''
            """,
        )
        assert empty_required_fields == 0

        lexeme_count = scalar(connection, "SELECT COUNT(*) FROM lexemes")
        form_count = scalar(connection, "SELECT COUNT(*) FROM lexeme_forms")
        gloss_count = scalar(connection, "SELECT COUNT(*) FROM lexeme_glosses")
        dictionary_furigana_count = scalar(connection, "SELECT COUNT(*) FROM furigana_segments")
        reading_count = scalar(connection, "SELECT COUNT(*) FROM reading_sentences")
        reading_work_count = scalar(connection, "SELECT COUNT(*) FROM reading_works")
        course_token_count = scalar(connection, "SELECT COUNT(*) FROM sentence_lexemes")
        reading_token_count = scalar(connection, "SELECT COUNT(*) FROM reading_sentence_lexemes")
        assert lexeme_count >= 200_000, lexeme_count
        assert form_count >= lexeme_count, (form_count, lexeme_count)
        assert gloss_count >= 400_000, gloss_count
        assert dictionary_furigana_count >= 600_000, dictionary_furigana_count
        assert reading_count == 14_000, reading_count
        assert reading_work_count == 4, reading_work_count
        assert total_count + reading_count == 20_120
        assert course_token_count > 60_000, course_token_count
        assert reading_token_count > 250_000, reading_token_count

        missing_course_token_index = scalar(
            connection,
            """SELECT COUNT(*) FROM sentences s
               WHERE NOT EXISTS (SELECT 1 FROM sentence_lexemes sl WHERE sl.sentence_id=s.id)""",
        )
        missing_reading_token_index = scalar(
            connection,
            """SELECT COUNT(*) FROM reading_sentences rs
               WHERE NOT EXISTS (SELECT 1 FROM reading_sentence_lexemes rsl WHERE rsl.reading_sentence_id=rs.id)""",
        )
        assert missing_course_token_index == 0
        assert missing_reading_token_index == 0

        print(
            "OK: 6000 base course preserved; 120 N1 supplements; 6120 structured total = "
            f"{counts['core']} core + {counts['variant']} variants + "
            f"{counts['real']} real + {counts['personal']} personal; "
            f"{grammar_count} grammar families, {chunk_count} chunks, "
            f"{ruby_count} course ruby segments, 0 duplicate texts; "
            f"{reading_count} attributed reading sentences = 20120 combined; "
            f"{lexeme_count} JMdict entries, {form_count} forms, {gloss_count} glosses"
        )
    finally:
        connection.close()


if __name__ == "__main__":
    main()

