#!/usr/bin/env python3
"""Import JMdict, KANJIDIC2 and JmdictFurigana into the existing database.

The importer is deliberately additive with respect to course content: it
backs up the database, refreshes only dictionary/index tables, and checks that
the original 6,000 course sentences are unchanged before committing.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import shutil
import sqlite3
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from sudachipy import dictionary, tokenizer

from extend_schema import extend_schema


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "japanese_learning.sqlite3"
DEFAULT_CACHE = ROOT / ".cache"
KANJI_RE = re.compile(r"[一-龯々〆ヵヶ]")
EXPECTED_BASE_SENTENCES = 6000
TOKENIZER = dictionary.Dictionary().create()
SPLIT_MODE = tokenizer.Tokenizer.SplitMode.C


def hira(text: str) -> str:
    return "".join(chr(ord(ch) - 0x60) if "ァ" <= ch <= "ヶ" else ch for ch in text)


def reading_for(morpheme) -> str:
    reading = morpheme.reading_form()
    return hira(reading) if reading and reading != "*" else morpheme.surface()


def priority_rank(values: Iterable[str]) -> int | None:
    values = list(values)
    nf = [int(v[2:]) * 500 for v in values if v.startswith("nf") and v[2:].isdigit()]
    if nf:
        return min(nf)
    if any(v in {"news1", "ichi1", "spec1", "gai1"} for v in values):
        return 30000
    if any(v in {"news2", "ichi2", "spec2", "gai2"} for v in values):
        return 60000
    return None


def flush(connection: sqlite3.Connection, sql: str, rows: list[tuple]) -> None:
    if rows:
        connection.executemany(sql, rows)
        rows.clear()


def import_jmdict(connection: sqlite3.Connection, path: Path) -> dict[str, int]:
    lexemes: list[tuple] = []
    forms: list[tuple] = []
    glosses: list[tuple] = []
    details: list[tuple] = []
    stats = {"lexemes": 0, "forms": 0, "glosses": 0}

    with gzip.open(path, "rb") as stream:
        for _, entry in ET.iterparse(stream, events=("end",)):
            if entry.tag != "entry":
                continue
            seq = int(entry.findtext("ent_seq", "0"))
            kanji_elements = entry.findall("k_ele")
            readings = entry.findall("r_ele")
            all_priorities: list[str] = []
            k_priorities: dict[str, list[str]] = {}
            for item in kanji_elements:
                written = item.findtext("keb", "")
                values = [node.text or "" for node in item.findall("ke_pri")]
                k_priorities[written] = values
                all_priorities.extend(values)
            for item in readings:
                all_priorities.extend(node.text or "" for node in item.findall("re_pri"))

            lexemes.append((seq, seq, priority_rank(all_priorities)))
            pos_values: list[str] = []
            usage_values: list[str] = []
            for sense in entry.findall("sense"):
                pos_values.extend(node.text or "" for node in sense.findall("pos"))
                usage_values.extend(node.text or "" for node in sense.findall("s_inf"))
                for gloss in sense.findall("gloss"):
                    lang = gloss.attrib.get("{http://www.w3.org/XML/1998/namespace}lang", "eng")
                    if lang == "eng" and gloss.text:
                        glosses.append((seq, "eng", gloss.text.strip(), "JMdict", 0))

            for r_ele in readings:
                reading = r_ele.findtext("reb", "")
                r_pri = [node.text or "" for node in r_ele.findall("re_pri")]
                restrictions = {node.text or "" for node in r_ele.findall("re_restr")}
                if kanji_elements:
                    applicable = [
                        item.findtext("keb", "") for item in kanji_elements
                        if not restrictions or item.findtext("keb", "") in restrictions
                    ]
                else:
                    applicable = [reading]
                for written in applicable:
                    is_common = int(bool(r_pri or k_priorities.get(written)))
                    forms.append((seq, written, reading, is_common))

            details.append((seq, " / ".join(dict.fromkeys(pos_values)), " / ".join(dict.fromkeys(usage_values))))
            stats["lexemes"] += 1
            stats["forms"] += len(forms)
            stats["glosses"] += len(glosses)
            if len(lexemes) >= 1000:
                flush(connection, "INSERT INTO lexemes(id,jmdict_seq,common_rank) VALUES (?,?,?)", lexemes)
                flush(connection, "INSERT OR IGNORE INTO lexeme_forms(lexeme_id,written_form,reading,is_common) VALUES (?,?,?,?)", forms)
                flush(connection, "INSERT INTO lexeme_glosses(lexeme_id,language_code,gloss,source,is_machine_generated) VALUES (?,?,?,?,?)", glosses)
                flush(connection, "INSERT INTO lexeme_details(lexeme_id,part_of_speech,usage_notes) VALUES (?,?,?)", details)
            entry.clear()

    flush(connection, "INSERT INTO lexemes(id,jmdict_seq,common_rank) VALUES (?,?,?)", lexemes)
    flush(connection, "INSERT OR IGNORE INTO lexeme_forms(lexeme_id,written_form,reading,is_common) VALUES (?,?,?,?)", forms)
    flush(connection, "INSERT INTO lexeme_glosses(lexeme_id,language_code,gloss,source,is_machine_generated) VALUES (?,?,?,?,?)", glosses)
    flush(connection, "INSERT INTO lexeme_details(lexeme_id,part_of_speech,usage_notes) VALUES (?,?,?)", details)
    stats["forms"] = connection.execute("SELECT COUNT(*) FROM lexeme_forms").fetchone()[0]
    stats["glosses"] = connection.execute("SELECT COUNT(*) FROM lexeme_glosses").fetchone()[0]
    return stats


def import_kanjidic(connection: sqlite3.Connection, path: Path) -> dict[str, int]:
    entries: list[tuple] = []
    readings: list[tuple] = []
    with gzip.open(path, "rb") as stream:
        for _, char in ET.iterparse(stream, events=("end",)):
            if char.tag != "character":
                continue
            literal = char.findtext("literal", "")
            grade = char.findtext("misc/grade")
            strokes = char.findtext("misc/stroke_count")
            jlpt = char.findtext("misc/jlpt")
            entries.append((literal, None, int(grade) if grade else None, int(strokes) if strokes else None, int(jlpt) if jlpt else None))
            for node in char.findall("reading_meaning/rmgroup/reading"):
                kind = node.attrib.get("r_type")
                mapped = {"ja_on": "on", "ja_kun": "kun"}.get(kind)
                if mapped and node.text:
                    readings.append((literal, mapped, node.text))
            for node in char.findall("reading_meaning/nanori"):
                if node.text:
                    readings.append((literal, "nanori", node.text))
            char.clear()
    connection.executemany(
        "INSERT INTO kanji_entries(literal,meanings_zh,grade,stroke_count,jlpt_legacy_level) VALUES (?,?,?,?,?)",
        entries,
    )
    connection.executemany(
        "INSERT OR IGNORE INTO kanji_readings(literal,reading_type,reading) VALUES (?,?,?)",
        readings,
    )
    return {"kanji": len(entries), "kanji_readings": len(readings)}


def import_furigana(connection: sqlite3.Connection, path: Path) -> int:
    form_lookup = {
        (row[1], row[2]): row[0]
        for row in connection.execute("SELECT id,written_form,reading FROM lexeme_forms")
    }
    inserted = 0
    batch: list[tuple] = []
    with zipfile.ZipFile(path) as archive:
        name = next(name for name in archive.namelist() if name.endswith(".json"))
        with archive.open(name) as stream:
            data = json.load(stream)
    for item in data:
        form_id = form_lookup.get((item.get("text", ""), item.get("reading", "")))
        if form_id is None:
            continue
        for position, segment in enumerate(item.get("furigana", [])):
            batch.append((form_id, position, segment.get("ruby", ""), segment.get("rt")))
            inserted += 1
        if len(batch) >= 20000:
            flush(connection, "INSERT OR IGNORE INTO furigana_segments(lexeme_form_id,position,surface,reading) VALUES (?,?,?,?)", batch)
    flush(connection, "INSERT OR IGNORE INTO furigana_segments(lexeme_form_id,position,surface,reading) VALUES (?,?,?,?)", batch)
    return inserted


def form_maps(connection: sqlite3.Connection) -> tuple[dict[tuple[str, str], tuple[int, int]], dict[str, tuple[int, int]]]:
    exact: dict[tuple[str, str], tuple[int, int]] = {}
    by_written: dict[str, tuple[int, int]] = {}
    rows = connection.execute(
        """SELECT lf.id,lf.lexeme_id,lf.written_form,lf.reading,l.common_rank,lf.is_common
           FROM lexeme_forms lf JOIN lexemes l ON l.id=lf.lexeme_id
           ORDER BY lf.is_common DESC, COALESCE(l.common_rank,999999), lf.id"""
    )
    for row in rows:
        value = (row[1], row[0])
        exact.setdefault((row[2], hira(row[3])), value)
        by_written.setdefault(row[2], value)
    return exact, by_written


def tokenize_text(text: str, exact: dict, by_written: dict) -> list[tuple]:
    rows = []
    for position, token in enumerate(TOKENIZER.tokenize(text, SPLIT_MODE)):
        surface = token.surface()
        if not surface.strip():
            continue
        pos = token.part_of_speech()[0]
        dictionary_form = token.dictionary_form() or surface
        reading = reading_for(token)
        match = exact.get((dictionary_form, reading)) or exact.get((surface, reading)) or by_written.get(dictionary_form) or by_written.get(surface)
        lexeme_id, form_id = match if match else (None, None)
        rows.append((position, lexeme_id, form_id, surface, dictionary_form, reading, pos, token.begin(), token.end()))
    return rows


def index_course_sentences(connection: sqlite3.Connection) -> tuple[int, int]:
    exact, by_written = form_maps(connection)
    connection.execute("DELETE FROM sentence_lexemes")
    indexed = linked = 0
    batch: list[tuple] = []
    for sentence_id, text in connection.execute("SELECT id,text FROM sentences ORDER BY id"):
        for row in tokenize_text(text, exact, by_written):
            position, lexeme_id, form_id, surface, base, reading, pos, start, end = row
            batch.append((sentence_id, position, lexeme_id, form_id, surface, base, reading, pos, start, end))
            indexed += 1
            linked += int(lexeme_id is not None)
        if len(batch) >= 10000:
            flush(connection, "INSERT INTO sentence_lexemes VALUES (?,?,?,?,?,?,?,?,?,?)", batch)
    flush(connection, "INSERT INTO sentence_lexemes VALUES (?,?,?,?,?,?,?,?,?,?)", batch)
    return indexed, linked


def infer_teaching_glosses(connection: sqlite3.Connection) -> int:
    exact, by_written = form_maps(connection)
    existing = {(row[0], row[1]) for row in connection.execute("SELECT lexeme_id,gloss FROM lexeme_glosses WHERE language_code='zho'")}
    additions: list[tuple] = []
    for surface, meaning in connection.execute("SELECT surface,meaning_zh FROM sentence_chunks"):
        content = [row for row in tokenize_text(surface, exact, by_written) if row[6] not in {"補助記号", "助詞", "助動詞"} and row[1] is not None]
        if len(content) != 1 or not meaning or len(meaning) > 36:
            continue
        lexeme_id = content[0][1]
        key = (lexeme_id, meaning.strip())
        if key not in existing:
            additions.append((lexeme_id, "zho", meaning.strip(), "课程教学切分推断", 1))
            existing.add(key)
    connection.executemany(
        "INSERT INTO lexeme_glosses(lexeme_id,language_code,gloss,source,is_machine_generated) VALUES (?,?,?,?,?)",
        additions,
    )
    return len(additions)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()
    db_path = args.database.resolve()
    source = args.source_dir.resolve()
    required = [source / "JMdict.gz", source / "kanjidic2.xml.gz", source / "JmdictFurigana.json.zip"]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit("Missing source files: " + ", ".join(missing))

    if not args.no_backup:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = db_path.with_name(f"{db_path.stem}.before-dictionary-{stamp}{db_path.suffix}")
        shutil.copy2(db_path, backup)
        print(f"Backup: {backup}")

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        extend_schema(connection)
        before = connection.execute("SELECT COUNT(*) FROM sentences").fetchone()[0]
        base_before = connection.execute(
            "SELECT COUNT(*) FROM sentences WHERE id NOT LIKE 'n1\\_%' ESCAPE '\\'"
        ).fetchone()[0]
        if base_before != EXPECTED_BASE_SENTENCES:
            raise RuntimeError(
                f"Expected {EXPECTED_BASE_SENTENCES} base course sentences, got {base_before}"
            )
        connection.executescript(
            """
            DELETE FROM sentence_lexemes;
            DELETE FROM reading_sentence_lexemes;
            DELETE FROM furigana_segments;
            DELETE FROM lexeme_glosses;
            DELETE FROM lexeme_details;
            DELETE FROM lexeme_forms;
            DELETE FROM lexemes;
            DELETE FROM kanji_readings;
            DELETE FROM kanji_entries;
            """
        )
        jmdict = import_jmdict(connection, required[0])
        kanji = import_kanjidic(connection, required[1])
        furigana_count = import_furigana(connection, required[2])
        tokens, linked = index_course_sentences(connection)
        zh_glosses = infer_teaching_glosses(connection)
        now = datetime.now(timezone.utc).isoformat()
        sources = [
            ("jmdict", "JMdict", "https://www.edrdg.org/jmdict/j_jmdict.html", "日语多语词典与英文释义", "EDRDG General Dictionary Licence / CC BY-SA", now),
            ("kanjidic2", "KANJIDIC2", "https://www.edrdg.org/wiki/index.php/KANJIDIC_Project", "汉字读音、笔画与等级", "EDRDG General Dictionary Licence / CC BY-SA", now),
            ("jmdict_furigana", "JmdictFurigana", "https://github.com/Doublevil/JmdictFurigana", "词条内部假名对齐", "与JMdict相同的CC BY-SA许可", now),
        ]
        connection.executemany(
            """INSERT INTO dictionary_sources(id,name,homepage,purpose_zh,license_note_zh,imported_at)
               VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET imported_at=excluded.imported_at""",
            sources,
        )
        after = connection.execute("SELECT COUNT(*) FROM sentences").fetchone()[0]
        base_after = connection.execute(
            "SELECT COUNT(*) FROM sentences WHERE id NOT LIKE 'n1\\_%' ESCAPE '\\'"
        ).fetchone()[0]
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if after != before or base_after != EXPECTED_BASE_SENTENCES or integrity != "ok":
            raise RuntimeError(
                f"Safety check failed: sentences {before}->{after}, "
                f"base={base_after}, integrity={integrity}"
            )
        connection.commit()
        print(json.dumps({**jmdict, **kanji, "furigana_segments": furigana_count, "course_tokens": tokens, "linked_tokens": linked, "zh_teaching_glosses": zh_glosses, "course_sentences": after, "integrity": integrity}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
