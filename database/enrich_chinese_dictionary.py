#!/usr/bin/env python3
"""Import traceable Simplified-Chinese glosses and generate compositional memory hints.

The Tomoshi open-data database is a JMdict-derived CC BY-SA 4.0 layer.  Its
translations are machine-assisted, so they are imported with
``is_machine_generated=1`` and never replace the upstream English glosses.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import zstandard


ROOT = Path(__file__).resolve().parent
DEFAULT_DATABASE = ROOT / "japanese_learning.sqlite3"
DEFAULT_ARCHIVE = ROOT / ".cache" / "tomoshi-dict-open.db.zst"
EXPECTED_SHA256 = "793d26a8963bff8b4dc39e83190d6f1608449f25aa5a8b5f9163c44e8c39b684"
SOURCE_LABEL = "Tomoshi zh-CN open data 2026-11-16 · CC BY-SA 4.0"
SOURCE_URL = "https://github.com/tomoshi-app/tomoshi-dict-data"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def unpack(archive: Path) -> Path:
    output = archive.with_suffix("")
    if output.exists() and output.stat().st_mtime >= archive.stat().st_mtime:
        return output
    decompressor = zstandard.ZstdDecompressor()
    with archive.open("rb") as source, output.open("wb") as target:
        decompressor.copy_stream(source, target)
    return output


def translated_glosses(raw: str) -> list[str]:
    data = json.loads(raw)
    values: list[str] = []
    seen: set[str] = set()
    for sense in data.get("senses", {}).values():
        for gloss in sense.get("glosses", []):
            text = gloss.get("text", "").strip() if isinstance(gloss, dict) else str(gloss).strip()
            if text and text not in seen:
                seen.add(text)
                values.append(text)
    return values


def memory_hint(written: str, reading: str, gloss: str, components: list[str]) -> str:
    parts = "、".join(components[:4])
    if parts:
        return f"构词联想：把「{written}」看成{parts}，整体表示“{gloss}”；读作「{reading}」。"
    return f"读音联结：看到「{written}」先读成「{reading}」，再联想到它在句中表示“{gloss}”。"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--memory-limit", type=int, default=12000)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    database = args.database.resolve()
    archive = args.archive.resolve()
    if not archive.exists():
        raise SystemExit(f"Missing Tomoshi archive: {archive}")
    actual_hash = sha256(archive)
    if archive == DEFAULT_ARCHIVE.resolve() and actual_hash != EXPECTED_SHA256:
        raise RuntimeError(f"Unexpected source checksum: {actual_hash}")
    source_path = unpack(archive)

    with sqlite3.connect(source_path) as source:
        licence = source.execute(
            "SELECT license,attribution FROM table_licenses WHERE table_name='zh_defs'"
        ).fetchone()
        if licence is None or licence[0] != "CC-BY-SA-4.0":
            raise RuntimeError("The Chinese definition table has no expected CC BY-SA 4.0 licence")

    if not args.no_backup:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = database.with_name(f"{database.stem}.before-zh-{stamp}{database.suffix}")
        shutil.copy2(database, backup)
        print(f"Backup: {backup}")

    inserted = 0
    skipped = 0
    with sqlite3.connect(database) as target, sqlite3.connect(source_path) as source:
        target.execute("PRAGMA foreign_keys=ON")
        target.execute("PRAGMA journal_mode=WAL")
        source.row_factory = sqlite3.Row
        target.execute("DELETE FROM lexeme_glosses WHERE source=?", (SOURCE_LABEL,))
        valid_ids = {row[0] for row in target.execute("SELECT jmdict_seq FROM lexemes")}
        batch: list[tuple[int, str, str, str, int]] = []
        for row in source.execute("SELECT entry_id,data FROM zh_defs WHERE locale='zh-CN'"):
            lexeme_id = int(row["entry_id"])
            if lexeme_id not in valid_ids:
                skipped += 1
                continue
            for gloss in translated_glosses(row["data"]):
                batch.append((lexeme_id, "zho", gloss, SOURCE_LABEL, 1))
            if len(batch) >= 10000:
                target.executemany(
                    "INSERT INTO lexeme_glosses(lexeme_id,language_code,gloss,source,is_machine_generated) VALUES (?,?,?,?,?)",
                    batch,
                )
                inserted += len(batch)
                batch.clear()
        if batch:
            target.executemany(
                "INSERT INTO lexeme_glosses(lexeme_id,language_code,gloss,source,is_machine_generated) VALUES (?,?,?,?,?)",
                batch,
            )
            inserted += len(batch)

        kanji_updates = []
        for literal, raw in source.execute("SELECT literal,zho FROM kanji_gloss WHERE locale='zh-CN'"):
            meanings = "；".join(json.loads(raw))
            kanji_updates.append((meanings, literal))
        target.executemany(
            "UPDATE kanji_entries SET meanings_zh=? WHERE literal=?",
            kanji_updates,
        )

        target.execute(
            """INSERT INTO dictionary_sources(id,name,homepage,purpose_zh,license_note_zh,imported_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET imported_at=excluded.imported_at,
                   license_note_zh=excluded.license_note_zh""",
            (
                "tomoshi_zh_cn",
                "Tomoshi Dictionary Open Data zh-CN",
                SOURCE_URL,
                "JMdict词条的简体中文机器辅助释义",
                "CC BY-SA 4.0；© Y1Z；机器辅助内容，需人工复核",
                datetime.now(timezone.utc).isoformat(),
            ),
        )

        candidates = target.execute(
            """SELECT l.id, f.written_form, f.reading,
                      (SELECT gloss FROM lexeme_glosses g
                       WHERE g.lexeme_id=l.id AND g.language_code='zho'
                       ORDER BY g.is_machine_generated,g.id LIMIT 1) AS gloss
               FROM lexemes l
               JOIN lexeme_forms f ON f.id=(
                   SELECT f2.id FROM lexeme_forms f2 WHERE f2.lexeme_id=l.id
                   ORDER BY f2.is_common DESC,
                            CASE WHEN f2.written_form GLOB '*[一-龯]*' THEN 0 ELSE 1 END,
                            f2.id LIMIT 1
               )
               WHERE EXISTS (SELECT 1 FROM lexeme_glosses g WHERE g.lexeme_id=l.id AND g.language_code='zho')
                 AND (l.common_rank IS NOT NULL
                      OR EXISTS (SELECT 1 FROM sentence_lexemes sl WHERE sl.lexeme_id=l.id)
                      OR EXISTS (SELECT 1 FROM reading_sentence_lexemes rl WHERE rl.lexeme_id=l.id))
               ORDER BY CASE WHEN l.common_rank IS NULL THEN 1 ELSE 0 END,l.common_rank,l.id
               LIMIT ?""",
            (args.memory_limit,),
        ).fetchall()
        generated = 0
        for lexeme_id, written, word_reading, gloss in candidates:
            components = []
            for char in dict.fromkeys(written):
                if "一" <= char <= "龯":
                    meaning = target.execute(
                        "SELECT meanings_zh FROM kanji_entries WHERE literal=?", (char,)
                    ).fetchone()
                    if meaning and meaning[0]:
                        components.append(f"「{char}」（{meaning[0].split('；')[0]}）")
            note = memory_hint(written, word_reading, gloss, components)
            target.execute(
                """INSERT INTO lexeme_details(lexeme_id,memory_note_zh,memory_note_status)
                   VALUES (?,?,'generated')
                   ON CONFLICT(lexeme_id) DO UPDATE SET
                     memory_note_zh=CASE
                       WHEN lexeme_details.memory_note_status='reviewed' THEN lexeme_details.memory_note_zh
                       ELSE excluded.memory_note_zh END,
                     memory_note_status=CASE
                       WHEN lexeme_details.memory_note_status='reviewed' THEN 'reviewed'
                       ELSE 'generated' END""",
                (lexeme_id, note),
            )
            generated += 1

        target.execute(
            "INSERT INTO metadata(key,value) VALUES ('tomoshi_zh_sha256',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (actual_hash,),
        )
        target.execute(
            "INSERT INTO metadata(key,value) VALUES ('memory_notes_generated',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(generated),),
        )
        integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok" or target.execute("SELECT COUNT(*) FROM sentences").fetchone()[0] < 6000:
            raise RuntimeError(f"Database safety check failed: {integrity}")
        target.commit()
        chinese_total = target.execute(
            "SELECT COUNT(*) FROM lexeme_glosses WHERE language_code='zho'"
        ).fetchone()[0]
        translated_lexemes = target.execute(
            "SELECT COUNT(DISTINCT lexeme_id) FROM lexeme_glosses WHERE language_code='zho'"
        ).fetchone()[0]
    print(json.dumps({
        "inserted_chinese_glosses": inserted,
        "translated_lexemes": translated_lexemes,
        "chinese_glosses_total": chinese_total,
        "kanji_updated": len(kanji_updates),
        "memory_notes_generated": generated,
        "unmatched_source_entries": skipped,
        "integrity": integrity,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
