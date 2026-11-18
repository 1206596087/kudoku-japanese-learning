from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "database" / "japanese_learning.sqlite3"
OUTPUT = ROOT / "cloudflare" / "migrations" / "generated"

CONTENT_TABLES = (
    "levels",
    "grammar_points",
    "sentences",
    "sentence_ruby_segments",
    "sentence_chunks",
    "sentence_grammar",
    "dictionary_sources",
    "lexemes",
    "lexeme_forms",
    "furigana_segments",
    "lexeme_glosses",
    "lexeme_details",
    "kanji_entries",
    "kanji_readings",
    "sentence_lexemes",
    "reading_works",
    "reading_sections",
    "reading_sentences",
    "reading_sentence_ruby_segments",
    "reading_sentence_lexemes",
    "literary_expression_rules",
    "metadata",
)

USER_TABLES = (
    "learner_profiles",
    "learner_preferences",
    "app_accounts",
    "learner_sentence_state",
    "learner_grammar_state",
    "learner_lexeme_state",
    "learner_sentence_exposure",
    "learner_unit_state",
    "learner_reading_state",
    "learner_saved_lexemes",
    "reading_bookmarks",
    "reading_history",
    "app_learning_events",
    "app_review_logs",
    "app_sessions",
)

# These tables live in CONTENT_DB, so USER_DB stores their identifiers without
# cross-database foreign keys. Relationships are validated in the application.
EXTERNAL_REFERENCE = re.compile(
    r"\s+REFERENCES\s+"
    r"(?:levels|grammar_points|sentences|lexemes|reading_works|reading_sentences)"
    r"\s*\([^)]*\)(?:\s+ON\s+DELETE\s+(?:CASCADE|SET\s+NULL|RESTRICT|NO\s+ACTION))?",
    re.IGNORECASE,
)


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def sql_literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bytes):
        return "X'" + value.hex() + "'"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return "NULL" if not math.isfinite(value) else repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def table_sql(connection: sqlite3.Connection, table: str, user_database: bool) -> str:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not row or not row[0]:
        raise RuntimeError(f"Missing schema for {table}")
    sql = row[0]
    if user_database:
        sql = EXTERNAL_REFERENCE.sub("", sql)
    return sql.rstrip(";") + ";"


def index_sql(connection: sqlite3.Connection, tables: Iterable[str]) -> list[str]:
    names = tuple(tables)
    placeholders = ",".join("?" for _ in names)
    rows = connection.execute(
        f"""SELECT sql FROM sqlite_master
            WHERE type='index' AND sql IS NOT NULL AND tbl_name IN ({placeholders})
            ORDER BY name""",
        names,
    )
    return [row[0].rstrip(";") + ";" for row in rows]


def write_database(
    connection: sqlite3.Connection,
    destination: Path,
    tables: tuple[str, ...],
    *,
    user_database: bool,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    with destination.open("w", encoding="utf-8", newline="\n") as output:
        output.write("-- Generated from japanese_learning.sqlite3; do not edit.\n")
        output.write("PRAGMA defer_foreign_keys = true;\n")
        for table in tables:
            output.write(table_sql(connection, table, user_database) + "\n")

        for table in tables:
            # Browser sessions are deliberately not migrated. A new secure
            # cookie is issued after the first cloud login.
            if table == "app_sessions":
                counts[table] = 0
                continue
            cursor = connection.execute(f"SELECT * FROM {quote_identifier(table)}")
            columns = [item[0] for item in cursor.description]
            prefix = (
                f"INSERT INTO {quote_identifier(table)} "
                f"({','.join(quote_identifier(column) for column in columns)}) VALUES "
            )
            batch: list[str] = []
            batch_length = len(prefix)
            count = 0
            for row in cursor:
                values = "(" + ",".join(sql_literal(value) for value in row) + ")"
                if batch and (len(batch) >= 100 or batch_length + len(values) > 80_000):
                    output.write(prefix + ",".join(batch) + ";\n")
                    batch = []
                    batch_length = len(prefix)
                batch.append(values)
                batch_length += len(values) + 1
                count += 1
            if batch:
                output.write(prefix + ",".join(batch) + ";\n")
            counts[table] = count

        for statement in index_sql(connection, tables):
            output.write(statement + "\n")
        output.write("PRAGMA optimize;\n")
    return counts


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    if not SOURCE.exists():
        raise SystemExit(f"Database not found: {SOURCE}")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    content_path = OUTPUT / "content.sql"
    user_path = OUTPUT / "user.sql"

    connection = sqlite3.connect(f"file:{SOURCE.as_posix()}?mode=ro", uri=True)
    try:
        integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"Source database integrity check failed: {integrity}")
        content_counts = write_database(
            connection, content_path, CONTENT_TABLES, user_database=False
        )
        user_counts = write_database(
            connection, user_path, USER_TABLES, user_database=True
        )
    finally:
        connection.close()

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(SOURCE),
        "source_size": SOURCE.stat().st_size,
        "source_sha256": sha256(SOURCE),
        "content": {
            "file": content_path.name,
            "size": content_path.stat().st_size,
            "sha256": sha256(content_path),
            "counts": content_counts,
        },
        "user": {
            "file": user_path.name,
            "size": user_path.stat().st_size,
            "sha256": sha256(user_path),
            "counts": user_counts,
            "sessions_migrated": False,
        },
    }
    (OUTPUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
