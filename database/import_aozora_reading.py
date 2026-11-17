#!/usr/bin/env python3
"""Build a 14,000-sentence, source-attributed Aozora reading corpus.

These are reading sentences, not replacements for the reviewed course path.
They retain the public-domain source text, receive local morphological/ruby
analysis, and can later be translated or promoted after editorial review.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

from extend_schema import extend_schema
from import_dictionary import KANJI_RE, form_maps, tokenize_text


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "japanese_learning.sqlite3"
JAPANESE_RE = re.compile(r"[ぁ-んァ-ヶ一-龯]")


@dataclass(frozen=True)
class Work:
    id: str
    title: str
    author: str
    card_url: str
    file_url: str
    limit: int


WORKS = (
    Work("aozora-789", "吾輩は猫である", "夏目漱石", "https://www.aozora.gr.jp/cards/000148/card789.html", "https://www.aozora.gr.jp/cards/000148/files/789_14547.html", 5000),
    Work("aozora-752", "坊っちゃん", "夏目漱石", "https://www.aozora.gr.jp/cards/000148/card752.html", "https://www.aozora.gr.jp/cards/000148/files/752_14964.html", 4000),
    Work("aozora-773", "こころ", "夏目漱石", "https://www.aozora.gr.jp/cards/000148/card773.html", "https://www.aozora.gr.jp/cards/000148/files/773_14560.html", 5000),
    Work("aozora-794", "三四郎", "夏目漱石", "https://www.aozora.gr.jp/cards/000148/card794.html", "https://www.aozora.gr.jp/cards/000148/files/794_14946.html", 1500),
)


def fetch_main_text(work: Work) -> str:
    request = Request(work.file_url, headers={"User-Agent": "SentenceDriveJP/1.0"})
    with urlopen(request, timeout=60) as response:
        raw = response.read()
    html = raw.decode("shift_jis", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    main = soup.select_one(".main_text")
    if main is None:
        raise RuntimeError(f"No .main_text found for {work.title}")
    for node in main.select("rt, rp, h1, h2, h3, h4, h5, h6"):
        node.decompose()
    return main.get_text("\n")


def split_sentences(text: str) -> list[str]:
    text = text.replace("\r", "").replace("\u3000", " ")
    results: list[str] = []
    buffer = ""
    seen: set[str] = set()
    for char in text:
        if char == "\n":
            if buffer and buffer[-1:] in {"」", "』"}:
                candidate = buffer.strip()
                if 4 <= len(candidate) <= 240 and JAPANESE_RE.search(candidate) and candidate not in seen:
                    results.append(candidate)
                    seen.add(candidate)
                buffer = ""
            continue
        buffer += char
        if char in "。！？":
            candidate = re.sub(r"\s+", "", buffer).strip()
            if 4 <= len(candidate) <= 240 and JAPANESE_RE.search(candidate) and candidate not in seen:
                results.append(candidate)
                seen.add(candidate)
            buffer = ""
    return results


def difficulty(token_rows: list[tuple], rank_by_lexeme: dict[int, int | None]) -> float:
    content = [row for row in token_rows if row[6] not in {"補助記号", "助詞", "助動詞"}]
    if not content:
        return 1.0
    unknown = sum(row[1] is None for row in content) / len(content)
    ranks = [rank_by_lexeme.get(row[1]) for row in content if row[1] is not None]
    ranks = [rank for rank in ranks if rank is not None]
    rank_factor = min((sum(ranks) / len(ranks)) / 30000, 1.5) if ranks else 1.5
    length_factor = min(len(content) / 40, 1.0)
    return round(min(5.0, 1.0 + unknown * 2.0 + rank_factor * 1.2 + length_factor * 0.8), 2)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--target", type=int, default=14000)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()
    db_path = args.database.resolve()
    if not args.no_backup:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = db_path.with_name(f"{db_path.stem}.before-aozora-{stamp}{db_path.suffix}")
        shutil.copy2(db_path, backup)
        print(f"Backup: {backup}")

    work_sentences: list[tuple[Work, list[str]]] = []
    for work in WORKS:
        sentences = split_sentences(fetch_main_text(work))[: work.limit]
        print(f"{work.title}: selected {len(sentences)}")
        work_sentences.append((work, sentences))
    available = sum(len(items) for _, items in work_sentences)
    if available < args.target:
        raise RuntimeError(f"Only {available} source sentences available; target is {args.target}")

    # Respect per-work limits while trimming the combined set to exactly target.
    overflow = available - args.target
    if overflow:
        work, items = work_sentences[-1]
        work_sentences[-1] = (work, items[:-overflow])

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        extend_schema(connection)
        base_count = connection.execute(
            "SELECT COUNT(*) FROM sentences WHERE id NOT LIKE 'n1\\_%' ESCAPE '\\'"
        ).fetchone()[0]
        if base_count != 6000:
            raise RuntimeError("The 6,000-sentence course layer is not intact")
        if connection.execute("SELECT COUNT(*) FROM lexemes").fetchone()[0] < 200000:
            raise RuntimeError("Import JMdict before building the reading corpus")
        connection.execute("DELETE FROM reading_works")
        exact, by_written = form_maps(connection)
        rank_by_lexeme = dict(connection.execute("SELECT id,common_rank FROM lexemes"))
        sentence_rows: list[tuple] = []
        ruby_rows: list[tuple] = []
        token_rows_out: list[tuple] = []
        inserted = 0
        linked = 0
        for work, sentences in work_sentences:
            connection.execute(
                """INSERT INTO reading_works
                   (id,title,author,source_name,source_url,source_file_url,copyright_status,orthography)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (work.id, work.title, work.author, "青空文庫", work.card_url, work.file_url, "public_domain", "新字新仮名"),
            )
            for position, text in enumerate(sentences, 1):
                sentence_id = f"{work.id}-{position:05d}"
                tokens = tokenize_text(text, exact, by_written)
                reading = "".join(row[5] for row in tokens)
                sentence_rows.append((sentence_id, work.id, position, text, reading, None, difficulty(tokens, rank_by_lexeme), len(text), "segmented"))
                for segment_position, row in enumerate(tokens):
                    token_position, lexeme_id, _form_id, surface, base, token_reading, pos, start, end = row
                    ruby_rows.append((sentence_id, segment_position, surface, token_reading if KANJI_RE.search(surface) else None))
                    token_rows_out.append((sentence_id, token_position, lexeme_id, surface, base, token_reading, pos, start, end))
                    linked += int(lexeme_id is not None)
                inserted += 1
                if len(sentence_rows) >= 500:
                    connection.executemany("INSERT INTO reading_sentences VALUES (?,?,?,?,?,?,?,?,?)", sentence_rows)
                    connection.executemany("INSERT INTO reading_sentence_ruby_segments VALUES (?,?,?,?)", ruby_rows)
                    connection.executemany("INSERT INTO reading_sentence_lexemes VALUES (?,?,?,?,?,?,?,?,?)", token_rows_out)
                    sentence_rows.clear(); ruby_rows.clear(); token_rows_out.clear()
        if sentence_rows:
            connection.executemany("INSERT INTO reading_sentences VALUES (?,?,?,?,?,?,?,?,?)", sentence_rows)
            connection.executemany("INSERT INTO reading_sentence_ruby_segments VALUES (?,?,?,?)", ruby_rows)
            connection.executemany("INSERT INTO reading_sentence_lexemes VALUES (?,?,?,?,?,?,?,?,?)", token_rows_out)
        total = connection.execute("SELECT COUNT(*) FROM reading_sentences").fetchone()[0]
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if total != args.target or integrity != "ok":
            raise RuntimeError(f"Corpus safety check failed: total={total}, integrity={integrity}")
        connection.execute(
            """INSERT INTO metadata(key,value) VALUES ('reading_corpus_target',?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (str(total),),
        )
        connection.commit()
        token_count = connection.execute("SELECT COUNT(*) FROM reading_sentence_lexemes").fetchone()[0]
        print(f"Imported {total} reading sentences; {token_count} tokens; {linked} linked to JMdict; integrity={integrity}")


if __name__ == "__main__":
    main()
