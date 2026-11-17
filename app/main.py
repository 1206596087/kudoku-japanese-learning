from __future__ import annotations

import json
import hashlib
import hmac
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fsrs import Card, Rating, Scheduler
from pydantic import BaseModel, Field
from sudachipy import dictionary as sudachi_dictionary, tokenizer as sudachi_tokenizer

from database.extend_schema import extend_schema


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "app" / "static"
DEFAULT_DATABASE_PATH = ROOT / "database" / "japanese_learning.sqlite3"
DATABASE_PATH = Path(os.getenv("NIHONGO_DB_PATH", str(DEFAULT_DATABASE_PATH))).resolve()

LEVELS = ("N5", "N4", "N3", "N2", "N1")
KINDS = ("core", "variant", "real", "personal")
SUDACHI = sudachi_dictionary.Dictionary().create()
SPLIT_MODE = sudachi_tokenizer.Tokenizer.SplitMode.C
PARTICLE_NOTES = {
    "は": "提示主题或形成对比：接下来是在谈它；读作「わ」。",
    "が": "标记主语或新出现、被强调的信息，也可连接转折。",
    "を": "标记动作直接作用的对象；有时也表示移动经过的场所。",
    "に": "常标记到达点、对象、存在位置或具体时间点，需结合前后词判断。",
    "へ": "表示动作朝向的方向，强调‘往……去’；作助词时读「え」。",
    "で": "常标记动作发生的场所、使用的手段/材料，或原因范围。",
    "と": "可表示共同对象、并列、引用内容或条件结果。",
    "から": "表示起点、来源或原因，相当于‘从……’或‘因为……’。",
    "まで": "表示时间、空间或程度的终点，相当于‘到……为止’。",
    "の": "连接名词表示所属或修饰，也可把前面的表达名词化。",
    "も": "表示追加或同类，相当于‘也’；还能与疑问词构成全面表达。",
    "や": "列举代表性事物，暗示还有其他同类，语气不像「と」那样穷尽。",
    "か": "表示疑问、选择或不确定内容。",
    "ね": "句末寻求共识或确认，类似‘对吧/呢’。",
    "よ": "句末把新信息明确告诉对方，带提醒或强调。",
    "より": "表示比较基准，也可表示时间或空间的起点。",
    "だけ": "限定范围，表示‘只有/仅仅’。",
    "しか": "与否定形式搭配，表示‘除了……以外都不’。",
    "でも": "可表示举例、让步或转折；具体作用取决于连接位置。",
    "て": "连接动词前后：可表示动作先后、并列、原因或方式；具体关系由上下文决定。",
    "ばかり": "限定范围，常表示‘净是/只顾着’，也可表示‘刚刚做完’或大致数量。",
    "し": "列举并列的事实或理由，暗示除此之外可能还有其他原因。",
    "ので": "把前项作为较客观、较柔和的原因，引出后面的结果。",
    "のに": "连接预期与实际相反的情况，表示‘明明……却……’。",
    "ながら": "表示两个动作同时进行，也可表示转折‘虽然……但是……’。",
}


def particle_note(surface: str, part_of_speech: str) -> str | None:
    if part_of_speech != "助詞":
        return None
    return PARTICLE_NOTES.get(surface, "这是连接前后成分的助词；具体作用需要结合所在句型判断。")


@contextmanager
def db(write: bool = False) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    if write:
        connection.execute("PRAGMA busy_timeout = 10000")
    try:
        yield connection
        if write:
            connection.commit()
    except Exception:
        if write:
            connection.rollback()
        raise
    finally:
        connection.close()


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


SESSION_COOKIE = "kudoku_session"
PASSWORD_ITERATIONS = 310_000
AUTH_DISABLED = os.getenv("NIHONGO_AUTH_DISABLED", "0") == "1"


def password_digest(password: str, salt_hex: str, iterations: int = PASSWORD_ITERATIONS) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), iterations
    ).hex()


def authenticated_account(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with db() as connection:
        row = connection.execute(
            """SELECT a.id,a.learner_id,a.username,s.expires_at
               FROM app_sessions s JOIN app_accounts a ON a.id=s.account_id
               WHERE s.token_hash=? AND s.expires_at>CURRENT_TIMESTAMP""",
            (token_hash,),
        ).fetchone()
    return dict(row) if row else None


def create_session(connection: sqlite3.Connection, account_id: int) -> str:
    raw = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    expires = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    connection.execute("DELETE FROM app_sessions WHERE expires_at<=CURRENT_TIMESTAMP")
    connection.execute(
        "INSERT INTO app_sessions(token_hash,account_id,expires_at) VALUES (?,?,?)",
        (token_hash, account_id, expires),
    )
    return raw


def ensure_database() -> None:
    if not DATABASE_PATH.exists():
        raise RuntimeError(f"Database not found: {DATABASE_PATH}")
    with db(write=True) as connection:
        integrity = connection.execute("PRAGMA quick_check(1)").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"Database integrity check failed: {integrity}")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS app_learning_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                learner_id INTEGER NOT NULL REFERENCES learner_profiles(id) ON DELETE CASCADE,
                sentence_id TEXT NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
                grammar_id TEXT REFERENCES grammar_points(id) ON DELETE SET NULL,
                event_type TEXT NOT NULL,
                listening_score INTEGER NOT NULL CHECK (listening_score BETWEEN 0 AND 5),
                comprehension_score INTEGER NOT NULL CHECK (comprehension_score BETWEEN 0 AND 5),
                production_score INTEGER NOT NULL CHECK (production_score BETWEEN 0 AND 5),
                reviewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_app_events_learner_time
                ON app_learning_events(learner_id, reviewed_at DESC);
            CREATE INDEX IF NOT EXISTS idx_app_events_sentence
                ON app_learning_events(sentence_id);
            """
        )
        extend_schema(connection)


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_database()
    yield


app = FastAPI(
    title="句读 · 日语学习",
    version="0.2.0",
    docs_url="/api/docs",
    redoc_url=None,
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

PUBLIC_API_PATHS = {
    "/api/health",
    "/api/account/status",
    "/api/account/setup",
    "/api/account/login",
}


@app.middleware("http")
async def private_account_guard(request: Request, call_next):
    if AUTH_DISABLED or not request.url.path.startswith("/api/") or request.url.path in PUBLIC_API_PATHS:
        return await call_next(request)
    account = authenticated_account(request.cookies.get(SESSION_COOKIE))
    if account is None:
        return JSONResponse({"detail": "请先登录私人学习账号"}, status_code=401)
    request.state.account = account
    return await call_next(request)


class ProgressRequest(BaseModel):
    learner_id: int = 1
    sentence_id: str
    listening_score: int = Field(ge=0, le=5)
    comprehension_score: int = Field(ge=0, le=5)
    production_score: int = Field(ge=0, le=5)
    event_type: Literal["learn", "review", "library"] = "learn"
    review_mode: Literal["lesson", "listening", "recall", "grammar"] = "lesson"
    error_type: Literal["listening", "comprehension", "production", "mixed"] | None = None


class LevelRequest(BaseModel):
    learner_id: int = 1
    level: Literal["N5", "N4", "N3", "N2", "N1"]


class ResetRequest(BaseModel):
    learner_id: int = 1
    confirmation: str


class AnalyzeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class ReadingProgressRequest(BaseModel):
    learner_id: int = 1
    sentence_id: str


class BookmarkRequest(BaseModel):
    learner_id: int = 1
    sentence_id: str
    note_zh: str | None = Field(default=None, max_length=500)


class SavedLexemeRequest(BaseModel):
    learner_id: int = 1
    lexeme_id: int
    source_sentence_id: str | None = None
    source_type: Literal["dictionary", "course", "reading", "analysis"] = "dictionary"


class RetentionRequest(BaseModel):
    learner_id: int = 1
    desired_retention: float = Field(ge=0.7, le=0.99)


class AccountCredentials(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_.\-\u4e00-\u9fff]+$")
    password: str = Field(min_length=8, max_length=128)


def ruby_segments(connection: sqlite3.Connection, sentence_id: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            """
            SELECT position, surface, reading
            FROM sentence_ruby_segments
            WHERE sentence_id = ?
            ORDER BY position
            """,
            (sentence_id,),
        )
    ]


def sentence_tokens(connection: sqlite3.Connection, sentence_id: str) -> list[dict[str, Any]]:
    tokens = [
        dict(row)
        for row in connection.execute(
            """
            SELECT sl.position, sl.lexeme_id, sl.surface, sl.dictionary_form,
                   sl.reading, sl.part_of_speech, sl.start_offset, sl.end_offset,
                   (SELECT GROUP_CONCAT(gloss, '；') FROM (
                       SELECT gloss FROM lexeme_glosses
                       WHERE lexeme_id = sl.lexeme_id AND language_code = 'zho'
                       ORDER BY is_machine_generated, id LIMIT 3
                   )) AS gloss_zh,
                   (SELECT GROUP_CONCAT(gloss, '; ') FROM (
                       SELECT gloss FROM lexeme_glosses
                       WHERE lexeme_id = sl.lexeme_id AND language_code = 'eng'
                       ORDER BY id LIMIT 3
                   )) AS gloss_en
            FROM sentence_lexemes AS sl
            WHERE sl.sentence_id = ?
            ORDER BY sl.position
            """,
            (sentence_id,),
        )
    ]
    for token in tokens:
        token["particle_note_zh"] = particle_note(token["surface"], token["part_of_speech"])
    return tokens


def katakana_to_hiragana(text: str) -> str:
    return "".join(chr(ord(char) - 0x60) if "ァ" <= char <= "ヶ" else char for char in text)


def find_lexeme(
    connection: sqlite3.Connection, written: str, reading: str = ""
) -> sqlite3.Row | None:
    reading = katakana_to_hiragana(reading)
    return connection.execute(
        """
        SELECT l.id AS lexeme_id, lf.id AS lexeme_form_id
        FROM lexeme_forms AS lf
        JOIN lexemes AS l ON l.id = lf.lexeme_id
        WHERE (lf.written_form = ? AND (? = '' OR lf.reading = ?))
           OR (lf.written_form = ?)
        ORDER BY CASE WHEN lf.reading = ? THEN 0 ELSE 1 END,
                 lf.is_common DESC, COALESCE(l.common_rank, 999999), lf.id
        LIMIT 1
        """,
        (written, reading, reading, written, reading),
    ).fetchone()


def sentence_preview(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["ruby_segments"] = ruby_segments(connection, data["id"])
    return data


def sentence_detail(connection: sqlite3.Connection, sentence_id: str, learner_id: int = 1) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT s.*, sg.grammar_id, sg.role AS grammar_role,
               gp.title_zh AS grammar_title, gp.pattern AS grammar_pattern,
               gp.meaning_zh AS grammar_meaning, gp.formation_zh AS grammar_formation,
               gp.rationale_zh AS grammar_rationale, gp.nuance_zh AS grammar_nuance,
               gp.common_mistakes_zh AS grammar_mistakes, gp.status AS grammar_status
        FROM sentences AS s
        LEFT JOIN sentence_grammar AS sg ON sg.sentence_id = s.id
        LEFT JOIN grammar_points AS gp ON gp.id = sg.grammar_id
        WHERE s.id = ?
        """,
        (sentence_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "没有找到这个句子")

    data = dict(row)
    data["ruby_segments"] = ruby_segments(connection, sentence_id)
    data["tokens"] = sentence_tokens(connection, sentence_id)
    data["chunks"] = [
        dict(chunk)
        for chunk in connection.execute(
            """
            SELECT position, surface, reading, meaning_zh, function_zh
            FROM sentence_chunks
            WHERE sentence_id = ?
            ORDER BY position
            """,
            (sentence_id,),
        )
    ]
    state = connection.execute(
        """
        SELECT listening_score, comprehension_score, production_score,
               review_count, due_at, last_rating, last_reviewed_at,
               lapse_count, last_error_type, fsrs_card_json
        FROM learner_sentence_state
        WHERE learner_id = ? AND sentence_id = ?
        """,
        (learner_id, sentence_id),
    ).fetchone()
    data["progress"] = row_dict(state) or {
        "listening_score": 0,
        "comprehension_score": 0,
        "production_score": 0,
        "review_count": 0,
        "due_at": None,
        "last_rating": None,
        "last_reviewed_at": None,
        "lapse_count": 0,
        "last_error_type": None,
        "fsrs_card_json": None,
    }

    root_id = sentence_id if data["kind"] == "core" else data["parent_sentence_id"]
    data["root_id"] = root_id
    exposure = connection.execute(
        """SELECT shown_count,completed_count,last_shown_at,last_completed_at
           FROM learner_sentence_exposure WHERE learner_id=? AND sentence_id=?""",
        (learner_id, sentence_id),
    ).fetchone()
    data["exposure"] = row_dict(exposure) or {
        "shown_count": 0,
        "completed_count": 0,
        "last_shown_at": None,
        "last_completed_at": None,
    }
    unit_state = connection.execute(
        """SELECT shown_count,completed_sentences,mastery_percent,last_activity_at
           FROM learner_unit_state WHERE learner_id=? AND root_sentence_id=?""",
        (learner_id, root_id),
    ).fetchone()
    data["unit_progress"] = row_dict(unit_state) or {
        "shown_count": 0,
        "completed_sentences": 0,
        "mastery_percent": 0,
        "last_activity_at": None,
    }
    child_rows = connection.execute(
        """
        SELECT s.id, s.kind, s.variant_type, s.text, s.reading,
               s.translation_zh, s.grammar_note_zh, s.status, s.audio_text,
               COALESCE(lss.listening_score, 0) AS listening_score,
               COALESCE(lss.comprehension_score, 0) AS comprehension_score,
               COALESCE(lss.production_score, 0) AS production_score
        FROM sentences AS s
        LEFT JOIN learner_sentence_state AS lss
          ON lss.sentence_id = s.id AND lss.learner_id = ?
        WHERE s.parent_sentence_id = ?
        ORDER BY CASE s.kind
                    WHEN 'variant' THEN 1 WHEN 'real' THEN 2 WHEN 'personal' THEN 3
                 END,
                 s.id
        """,
        (learner_id, root_id),
    ).fetchall()
    groups: dict[str, list[dict[str, Any]]] = {"variant": [], "real": [], "personal": []}
    for child in child_rows:
        preview = sentence_preview(connection, child)
        groups[preview["kind"]].append(preview)
    data["children"] = groups
    return data


def score_rating(listening: int, comprehension: int, production: int) -> Rating:
    scores = (listening, comprehension, production)
    lowest = min(scores)
    average = sum(scores) / len(scores)
    if lowest <= 1:
        return Rating.Again
    if lowest == 2:
        return Rating.Hard
    if lowest >= 4 and average >= 4.5:
        return Rating.Easy
    return Rating.Good


def weakest_dimension(listening: int, comprehension: int, production: int) -> str:
    values = {
        "listening": listening,
        "comprehension": comprehension,
        "production": production,
    }
    minimum = min(values.values())
    weakest = [name for name, score in values.items() if score == minimum]
    return weakest[0] if len(weakest) == 1 else "mixed"


def schedule_review(
    card_json: str | None,
    rating: Rating,
    desired_retention: float,
) -> tuple[Card, str]:
    scheduler = Scheduler(desired_retention=desired_retention)
    card = Card.from_json(card_json) if card_json else Card()
    card, _ = scheduler.review_card(card, rating, review_datetime=datetime.now(timezone.utc))
    return card, card.due.strftime("%Y-%m-%d %H:%M:%S")


def clause_analysis(text: str, tokens: list[dict[str, Any]]) -> dict[str, Any]:
    connector_notes = {
        "が": "转折或铺垫",
        "けれど": "转折",
        "けれども": "转折",
        "ので": "原因",
        "から": "原因或起点",
        "のに": "逆接",
        "ため": "原因或目的",
        "なら": "条件",
        "ば": "条件",
        "と": "引用、并列或条件",
        "ながら": "同时或逆接",
        "て": "动作连接",
    }
    clauses: list[dict[str, Any]] = []
    start = 0
    boundaries = {"。", "！", "？", "、", "；"}
    for index, char in enumerate(text):
        if char in boundaries:
            surface = text[start : index + 1].strip()
            if surface:
                clauses.append({"surface": surface, "role_zh": "分句", "connector_zh": None})
            start = index + 1
    tail = text[start:].strip()
    if tail:
        clauses.append({"surface": tail, "role_zh": "主要陈述", "connector_zh": None})
    if not clauses:
        clauses = [{"surface": text, "role_zh": "主要陈述", "connector_zh": None}]
    for clause in clauses:
        for connector, note in connector_notes.items():
            if connector in clause["surface"]:
                clause["connector_zh"] = f"「{connector}」：{note}"
                break
    clauses[-1]["role_zh"] = "句末核心陈述"
    literal = []
    unknown = []
    for token in tokens:
        chinese = next(
            (item["gloss"] for item in token.get("glosses", []) if item["language_code"] == "zho"),
            None,
        )
        if chinese:
            literal.append(f"{token['surface']}≈{chinese}")
        elif token["part_of_speech"] not in {"補助記号", "空白"}:
            literal.append(token["surface"])
            unknown.append(token["surface"])
    return {
        "clauses": clauses,
        "structure_summary_zh": f"全句暂按{len(clauses)}个意义段处理；先确认句末谓语，再沿连接词向前还原修饰关系。",
        "literal_order_zh": " / ".join(literal),
        "ambiguity_notes": ([f"这些成分暂未获得可靠中文义项：{'、'.join(dict.fromkeys(unknown))}"] if unknown else []),
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/manifest.webmanifest")
def manifest() -> FileResponse:
    return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/service-worker.js")
def service_worker() -> FileResponse:
    return FileResponse(STATIC_DIR / "service-worker.js", media_type="application/javascript")


@app.get("/api/account/status")
def account_status(request: Request) -> dict[str, Any]:
    with db() as connection:
        configured = connection.execute("SELECT COUNT(*) FROM app_accounts").fetchone()[0] > 0
    account = authenticated_account(request.cookies.get(SESSION_COOKIE))
    return {
        "configured": configured,
        "authenticated": bool(account) or AUTH_DISABLED,
        "username": account["username"] if account else None,
        "auth_disabled": AUTH_DISABLED,
    }


@app.post("/api/account/setup")
def account_setup(payload: AccountCredentials) -> JSONResponse:
    with db(write=True) as connection:
        if connection.execute("SELECT 1 FROM app_accounts LIMIT 1").fetchone():
            raise HTTPException(409, "私人账号已经创建")
        learner = connection.execute("SELECT id FROM learner_profiles ORDER BY id LIMIT 1").fetchone()
        if learner is None:
            raise HTTPException(500, "学习档案不存在")
        salt = secrets.token_hex(16)
        digest = password_digest(payload.password, salt)
        cursor = connection.execute(
            """INSERT INTO app_accounts
               (learner_id,username,password_hash,password_salt,password_iterations,last_login_at)
               VALUES (?,?,?,?,?,CURRENT_TIMESTAMP)""",
            (learner["id"], payload.username.strip(), digest, salt, PASSWORD_ITERATIONS),
        )
        connection.execute(
            "UPDATE learner_profiles SET display_name=? WHERE id=?",
            (payload.username.strip(), learner["id"]),
        )
        token = create_session(connection, cursor.lastrowid)
    response = JSONResponse({"ok": True, "username": payload.username.strip()})
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=30 * 24 * 60 * 60,
        httponly=True,
        samesite="strict",
        secure=os.getenv("NIHONGO_COOKIE_SECURE", "0") == "1",
    )
    return response


@app.post("/api/account/login")
def account_login(payload: AccountCredentials) -> JSONResponse:
    with db(write=True) as connection:
        account = connection.execute(
            """SELECT id,username,password_hash,password_salt,password_iterations
               FROM app_accounts WHERE username=?""",
            (payload.username.strip(),),
        ).fetchone()
        valid = account and hmac.compare_digest(
            account["password_hash"],
            password_digest(payload.password, account["password_salt"], account["password_iterations"]),
        )
        if not valid:
            raise HTTPException(401, "用户名或密码不正确")
        token = create_session(connection, account["id"])
        connection.execute("UPDATE app_accounts SET last_login_at=CURRENT_TIMESTAMP WHERE id=?", (account["id"],))
    response = JSONResponse({"ok": True, "username": account["username"]})
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=30 * 24 * 60 * 60,
        httponly=True,
        samesite="strict",
        secure=os.getenv("NIHONGO_COOKIE_SECURE", "0") == "1",
    )
    return response


@app.post("/api/account/logout")
def account_logout(request: Request) -> JSONResponse:
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        with db(write=True) as connection:
            connection.execute(
                "DELETE FROM app_sessions WHERE token_hash=?",
                (hashlib.sha256(raw.encode("utf-8")).hexdigest(),),
            )
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/api/health")
def health() -> dict[str, Any]:
    with db() as connection:
        counts = {
            row["kind"]: row["count"]
            for row in connection.execute(
                "SELECT kind, COUNT(*) AS count FROM sentences GROUP BY kind"
            )
        }
        integrity = connection.execute("PRAGMA quick_check(1)").fetchone()[0]
        base_counts = {
            row["kind"]: row["count"]
            for row in connection.execute(
                "SELECT kind,COUNT(*) AS count FROM sentences WHERE id NOT LIKE 'n1\\_%' ESCAPE '\\' GROUP BY kind"
            )
        }
        dictionary_count = connection.execute("SELECT COUNT(*) FROM lexemes").fetchone()[0]
        reading_count = connection.execute("SELECT COUNT(*) FROM reading_sentences").fetchone()[0]
    return {
        "ok": integrity == "ok" and sum(base_counts.values()) == 6000 and sum(counts.values()) >= 6000,
        "database": str(DATABASE_PATH),
        "integrity": integrity,
        "counts": counts,
        "base_counts": base_counts,
        "base_total": sum(base_counts.values()),
        "total": sum(counts.values()),
        "dictionary_entries": dictionary_count,
        "reading_sentences": reading_count,
        "all_sentences": sum(counts.values()) + reading_count,
    }


@app.get("/api/dashboard")
def dashboard(learner_id: int = 1) -> dict[str, Any]:
    with db() as connection:
        profile = connection.execute(
            "SELECT id, display_name, current_level_code FROM learner_profiles WHERE id = ?",
            (learner_id,),
        ).fetchone()
        if profile is None:
            raise HTTPException(404, "学习者不存在")
        today_count = connection.execute(
            """
            SELECT COUNT(DISTINCT sentence_id)
            FROM app_learning_events
            WHERE learner_id = ?
              AND date(reviewed_at, 'localtime') = date('now', 'localtime')
            """,
            (learner_id,),
        ).fetchone()[0]
        mastered = connection.execute(
            """
            SELECT COUNT(*) FROM learner_sentence_state
            WHERE learner_id = ? AND listening_score >= 4
              AND comprehension_score >= 4 AND production_score >= 3
            """,
            (learner_id,),
        ).fetchone()[0]
        averages = connection.execute(
            """
            SELECT COALESCE(AVG(listening_score), 0) AS listening,
                   COALESCE(AVG(comprehension_score), 0) AS comprehension,
                   COALESCE(AVG(production_score), 0) AS production
            FROM learner_sentence_state WHERE learner_id = ?
            """,
            (learner_id,),
        ).fetchone()
        due_count = connection.execute(
            """
            SELECT COUNT(*) FROM learner_sentence_state
            WHERE learner_id = ? AND due_at IS NOT NULL AND due_at <= CURRENT_TIMESTAMP
            """,
            (learner_id,),
        ).fetchone()[0]
        levels = [
            dict(row)
            for row in connection.execute(
                """
                SELECT l.code, l.label_zh, l.goal_zh,
                       COUNT(s.id) AS sentence_count,
                       SUM(CASE WHEN s.kind = 'core' THEN 1 ELSE 0 END) AS core_count
                FROM levels AS l
                LEFT JOIN sentences AS s ON s.level_code = l.code
                WHERE l.code IN ('N5', 'N4', 'N3', 'N2', 'N1')
                GROUP BY l.code, l.sort_order
                ORDER BY l.sort_order
                """
            )
        ]
        recent = [
            dict(row)
            for row in connection.execute(
                """
                SELECT DISTINCT gp.id, gp.title_zh, gp.pattern, gp.level_code
                FROM app_learning_events AS e
                JOIN grammar_points AS gp ON gp.id = e.grammar_id
                WHERE e.learner_id = ?
                ORDER BY e.reviewed_at DESC LIMIT 5
                """,
                (learner_id,),
            )
        ]
    return {
        "profile": dict(profile),
        "today_count": today_count,
        "mastered_count": mastered,
        "averages": {key: round(float(averages[key]), 2) for key in averages.keys()},
        "due_count": due_count,
        "levels": levels,
        "recent_grammars": recent,
    }


@app.get("/api/records")
def learning_records(learner_id: int = 1, level: str | None = None) -> dict[str, Any]:
    if level and level not in LEVELS:
        raise HTTPException(400, "不支持的等级")
    level_clause = "AND s.level_code=?" if level else ""
    level_params: list[Any] = [level] if level else []
    with db() as connection:
        summary = connection.execute(
            """SELECT COUNT(*) AS viewed_sentences,
                      COALESCE(SUM(e.shown_count),0) AS total_showings,
                      COALESCE(SUM(e.completed_count),0) AS total_completions
               FROM learner_sentence_exposure e JOIN sentences s ON s.id=e.sentence_id
               WHERE e.learner_id=? """ + level_clause,
            [learner_id, *level_params],
        ).fetchone()
        unit_summary = connection.execute(
            """SELECT COUNT(*) AS started_units,
                      COALESCE(SUM(CASE WHEN u.mastery_percent>=80 THEN 1 ELSE 0 END),0) AS mastered_units,
                      COALESCE(AVG(u.mastery_percent),0) AS average_mastery
               FROM learner_unit_state u JOIN sentences s ON s.id=u.root_sentence_id
               WHERE u.learner_id=? """ + level_clause,
            [learner_id, *level_params],
        ).fetchone()
        units = connection.execute(
            f"""SELECT s.id,s.level_code,s.text,s.translation_zh,gp.title_zh,gp.pattern,
                       COALESCE(lus.shown_count,0) AS shown_count,
                       COALESCE(lus.completed_sentences,0) AS completed_sentences,
                       COALESCE(lus.mastery_percent,0) AS mastery_percent,
                       lus.last_activity_at,
                       (SELECT COUNT(*) FROM sentences child WHERE child.parent_sentence_id=s.id)+1 AS sentence_count
                FROM sentences s
                LEFT JOIN sentence_grammar sg ON sg.sentence_id=s.id AND sg.role='new'
                LEFT JOIN grammar_points gp ON gp.id=sg.grammar_id
                LEFT JOIN learner_unit_state lus ON lus.root_sentence_id=s.id AND lus.learner_id=?
                WHERE s.kind='core' AND lus.root_sentence_id IS NOT NULL {level_clause}
                ORDER BY CASE WHEN lus.last_activity_at IS NULL THEN 1 ELSE 0 END,
                         lus.last_activity_at DESC,s.id LIMIT 120""",
            [learner_id, *level_params],
        ).fetchall()
        recent = connection.execute(
            f"""SELECT s.id,s.level_code,s.text,s.translation_zh,e.shown_count,
                       e.completed_count,e.last_shown_at,e.last_completed_at
                FROM learner_sentence_exposure e JOIN sentences s ON s.id=e.sentence_id
                WHERE e.learner_id=? {level_clause}
                ORDER BY e.last_shown_at DESC LIMIT 30""",
            [learner_id, *level_params],
        ).fetchall()
    return {
        "summary": {
            **dict(summary),
            **dict(unit_summary),
            "average_mastery": round(float(unit_summary["average_mastery"]), 1),
        },
        "units": [dict(row) for row in units],
        "recent_sentences": [dict(row) for row in recent],
        "level": level,
    }


@app.get("/api/sentences/next")
def next_sentence(
    level: str = Query("N5"),
    learner_id: int = 1,
    exclude: str | None = None,
    selection: Literal["adaptive", "random"] = "adaptive",
) -> dict[str, Any]:
    if level not in LEVELS:
        raise HTTPException(400, "不支持的等级")
    with db(write=True) as connection:
        params: list[Any] = [learner_id, level]
        exclusion = ""
        if exclude:
            exclusion = "AND s.id <> ?"
            params.append(exclude)
        ordering = (
            "RANDOM()"
            if selection == "random"
            else """CASE WHEN COALESCE(lse.shown_count,0)=0 THEN 0 ELSE 1 END,
                     COALESCE(lse.completed_count,0),
                     COALESCE(lse.last_shown_at,'1970-01-01 00:00:00'),
                     ABS(RANDOM())"""
        )
        row = connection.execute(
            f"""
            SELECT s.id
            FROM sentences AS s
            LEFT JOIN learner_sentence_state AS lss
              ON lss.sentence_id = s.id AND lss.learner_id = ?
            LEFT JOIN learner_sentence_exposure AS lse
              ON lse.sentence_id = s.id AND lse.learner_id = ?
            WHERE s.kind = 'core' AND s.level_code = ? {exclusion}
            ORDER BY {ordering}
            LIMIT 1
            """,
            [learner_id, *params],
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"{level} 暂无可学习内容")
        connection.execute(
            """INSERT INTO learner_sentence_exposure
               (learner_id,sentence_id,shown_count,last_shown_at)
               VALUES (?,?,1,CURRENT_TIMESTAMP)
               ON CONFLICT(learner_id,sentence_id) DO UPDATE SET
                 shown_count=learner_sentence_exposure.shown_count+1,
                 last_shown_at=CURRENT_TIMESTAMP""",
            (learner_id, row["id"]),
        )
        connection.execute(
            """INSERT INTO learner_unit_state
               (learner_id,root_sentence_id,shown_count,last_activity_at)
               VALUES (?,?,1,CURRENT_TIMESTAMP)
               ON CONFLICT(learner_id,root_sentence_id) DO UPDATE SET
                 shown_count=learner_unit_state.shown_count+1,
                 last_activity_at=CURRENT_TIMESTAMP""",
            (learner_id, row["id"]),
        )
        result = sentence_detail(connection, row["id"], learner_id)
        result["selection_mode"] = selection
        return result


@app.get("/api/sentences/{sentence_id}")
def get_sentence(sentence_id: str, learner_id: int = 1) -> dict[str, Any]:
    with db() as connection:
        return sentence_detail(connection, sentence_id, learner_id)


@app.post("/api/progress")
def save_progress(payload: ProgressRequest) -> dict[str, Any]:
    rating = score_rating(
        payload.listening_score,
        payload.comprehension_score,
        payload.production_score,
    )
    error_type = payload.error_type or weakest_dimension(
        payload.listening_score,
        payload.comprehension_score,
        payload.production_score,
    )
    with db(write=True) as connection:
        sentence = connection.execute(
            "SELECT id,kind,parent_sentence_id FROM sentences WHERE id = ?", (payload.sentence_id,)
        ).fetchone()
        if sentence is None:
            raise HTTPException(404, "句子不存在")
        current = connection.execute(
            "SELECT fsrs_card_json,lapse_count FROM learner_sentence_state WHERE learner_id=? AND sentence_id=?",
            (payload.learner_id, payload.sentence_id),
        ).fetchone()
        preference = connection.execute(
            "SELECT desired_retention FROM learner_preferences WHERE learner_id=?",
            (payload.learner_id,),
        ).fetchone()
        desired_retention = float(preference["desired_retention"]) if preference else 0.9
        card, due_text = schedule_review(
            current["fsrs_card_json"] if current else None,
            rating,
            desired_retention,
        )
        reviewed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        connection.execute(
            """
            INSERT INTO learner_sentence_state (
                learner_id, sentence_id, listening_score, comprehension_score,
                production_score, review_count, due_at, fsrs_card_json,
                last_rating, last_reviewed_at, lapse_count, last_error_type
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(learner_id, sentence_id) DO UPDATE SET
                listening_score = excluded.listening_score,
                comprehension_score = excluded.comprehension_score,
                production_score = excluded.production_score,
                review_count = learner_sentence_state.review_count + 1,
                due_at = excluded.due_at,
                fsrs_card_json = excluded.fsrs_card_json,
                last_rating = excluded.last_rating,
                last_reviewed_at = excluded.last_reviewed_at,
                lapse_count = learner_sentence_state.lapse_count + excluded.lapse_count,
                last_error_type = excluded.last_error_type
            """,
            (
                payload.learner_id,
                payload.sentence_id,
                payload.listening_score,
                payload.comprehension_score,
                payload.production_score,
                due_text,
                card.to_json(),
                rating.value,
                reviewed_at,
                1 if rating == Rating.Again else 0,
                error_type,
            ),
        )
        connection.execute(
            """INSERT INTO app_review_logs
               (learner_id,sentence_id,rating,review_mode,error_type,card_json,reviewed_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                payload.learner_id,
                payload.sentence_id,
                rating.value,
                payload.review_mode,
                error_type,
                card.to_json(),
                reviewed_at,
            ),
        )
        connection.execute(
            """INSERT INTO learner_sentence_exposure
               (learner_id,sentence_id,completed_count,last_completed_at)
               VALUES (?,?,1,CURRENT_TIMESTAMP)
               ON CONFLICT(learner_id,sentence_id) DO UPDATE SET
                 completed_count=learner_sentence_exposure.completed_count+1,
                 last_completed_at=CURRENT_TIMESTAMP""",
            (payload.learner_id, payload.sentence_id),
        )
        root_id = sentence["id"] if sentence["kind"] == "core" else sentence["parent_sentence_id"]
        unit = connection.execute(
            """SELECT COUNT(lss.sentence_id) AS completed,
                      COALESCE(AVG((lss.listening_score+lss.comprehension_score+lss.production_score)/15.0*100),0) AS mastery
               FROM sentences s
               LEFT JOIN learner_sentence_state lss
                 ON lss.sentence_id=s.id AND lss.learner_id=?
               WHERE s.id=? OR s.parent_sentence_id=?""",
            (payload.learner_id, root_id, root_id),
        ).fetchone()
        connection.execute(
            """INSERT INTO learner_unit_state
               (learner_id,root_sentence_id,completed_sentences,mastery_percent,last_activity_at)
               VALUES (?,?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(learner_id,root_sentence_id) DO UPDATE SET
                 completed_sentences=excluded.completed_sentences,
                 mastery_percent=excluded.mastery_percent,
                 last_activity_at=CURRENT_TIMESTAMP""",
            (payload.learner_id, root_id, unit["completed"], round(unit["mastery"], 1)),
        )
        grammar_rows = connection.execute(
            "SELECT grammar_id FROM sentence_grammar WHERE sentence_id = ?",
            (payload.sentence_id,),
        ).fetchall()
        recognition = round((payload.listening_score + payload.comprehension_score) / 2)
        for grammar in grammar_rows:
            connection.execute(
                """
                INSERT INTO learner_grammar_state (
                    learner_id, grammar_id, recognition_score, production_score,
                    review_count, due_at
                ) VALUES (?, ?, ?, ?, 1, ?)
                ON CONFLICT(learner_id, grammar_id) DO UPDATE SET
                    recognition_score = MAX(learner_grammar_state.recognition_score, excluded.recognition_score),
                    production_score = MAX(learner_grammar_state.production_score, excluded.production_score),
                    review_count = learner_grammar_state.review_count + 1,
                    due_at = excluded.due_at
                """,
                (
                    payload.learner_id,
                    grammar["grammar_id"],
                    recognition,
                    payload.production_score,
                    due_text,
                ),
            )
            connection.execute(
                """
                INSERT INTO app_learning_events (
                    learner_id, sentence_id, grammar_id, event_type,
                    listening_score, comprehension_score, production_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.learner_id,
                    payload.sentence_id,
                    grammar["grammar_id"],
                    payload.event_type,
                    payload.listening_score,
                    payload.comprehension_score,
                    payload.production_score,
                ),
            )
    return {
        "ok": True,
        "due_at": due_text,
        "rating": rating.value,
        "rating_label": rating.name.lower(),
        "error_type": error_type,
        "fsrs": {
            "state": card.state.name.lower(),
            "stability": card.stability,
            "difficulty": card.difficulty,
        },
    }


@app.get("/api/review/next")
def next_review(
    learner_id: int = 1,
    mode: Literal["listening", "recall", "grammar"] = "listening",
    level: str | None = None,
) -> dict[str, Any]:
    if level is not None and level not in LEVELS:
        raise HTTPException(400, "不支持的等级")
    preferred_error = {
        "listening": "listening",
        "recall": "production",
        "grammar": "comprehension",
    }[mode]
    with db() as connection:
        params: list[Any] = [learner_id]
        level_filter = ""
        if level:
            level_filter = "AND s.level_code = ?"
            params.append(level)
        due = connection.execute(
            f"""
            SELECT s.id, s.kind, s.parent_sentence_id
            FROM learner_sentence_state AS lss
            JOIN sentences AS s ON s.id = lss.sentence_id
            WHERE lss.learner_id = ? AND lss.due_at <= CURRENT_TIMESTAMP {level_filter}
            ORDER BY CASE WHEN lss.last_error_type = '{preferred_error}' THEN 0 ELSE 1 END,
                     MIN(lss.listening_score, lss.comprehension_score, lss.production_score),
                     lss.due_at, lss.review_count
            LIMIT 1
            """,
            params,
        ).fetchone()

        if due is not None:
            review_card_id = due["id"]
            root_id = due["id"] if due["kind"] == "core" else due["parent_sentence_id"]
            child = connection.execute(
                """
                SELECT c.id
                FROM sentences AS c
                LEFT JOIN learner_sentence_state AS cs
                  ON cs.sentence_id = c.id AND cs.learner_id = ?
                WHERE c.parent_sentence_id = ?
                ORDER BY COALESCE(cs.review_count, 0),
                         COALESCE(cs.comprehension_score, 0), c.id
                LIMIT 1
                """,
                (learner_id, root_id),
            ).fetchone()
            chosen_id = child["id"] if child is not None else due["id"]
        else:
            fallback_level = level or connection.execute(
                "SELECT current_level_code FROM learner_profiles WHERE id = ?",
                (learner_id,),
            ).fetchone()[0]
            fallback = connection.execute(
                """
                SELECT s.id FROM sentences AS s
                LEFT JOIN learner_sentence_state AS lss
                  ON lss.sentence_id = s.id AND lss.learner_id = ?
                WHERE s.kind = 'core' AND s.level_code = ?
                ORDER BY COALESCE(lss.review_count, 0), s.id LIMIT 1
                """,
                (learner_id, fallback_level),
            ).fetchone()
            if fallback is None:
                raise HTTPException(404, "当前等级没有可复习内容")
            chosen_id = fallback["id"]
            review_card_id = chosen_id
        result = sentence_detail(connection, chosen_id, learner_id)
        result["review_mode"] = mode
        result["review_card_id"] = review_card_id
        result["review_reason_zh"] = (
            f"FSRS已到期；优先针对{preferred_error}薄弱项，并换用同句式的新表达。"
            if due is not None
            else "当前没有到期卡片，先提供当前等级中复习次数较少的核心句。"
        )
        return result


@app.get("/api/grammars")
def grammars(
    level: str | None = None,
    q: str = "",
    learner_id: int = 1,
    limit: int = Query(60, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    if level and level not in LEVELS:
        raise HTTPException(400, "不支持的等级")
    clauses = ["1 = 1"]
    params: list[Any] = [learner_id]
    if level:
        clauses.append("gp.level_code = ?")
        params.append(level)
    if q:
        clauses.append("(gp.title_zh LIKE ? OR gp.pattern LIKE ? OR gp.meaning_zh LIKE ?)")
        token = f"%{q}%"
        params.extend([token, token, token])
    params.extend([limit, offset])
    with db() as connection:
        rows = connection.execute(
            f"""
            SELECT gp.*, COUNT(DISTINCT sg.sentence_id) AS sentence_count,
                   COALESCE(lgs.recognition_score, 0) AS recognition_score,
                   COALESCE(lgs.production_score, 0) AS production_score,
                   COALESCE(lgs.review_count, 0) AS review_count
            FROM grammar_points AS gp
            LEFT JOIN sentence_grammar AS sg ON sg.grammar_id = gp.id
            LEFT JOIN learner_grammar_state AS lgs
              ON lgs.grammar_id = gp.id AND lgs.learner_id = ?
            WHERE {' AND '.join(clauses)}
            GROUP BY gp.id
            ORDER BY CASE gp.level_code
                       WHEN 'N5' THEN 1 WHEN 'N4' THEN 2 WHEN 'N3' THEN 3
                       WHEN 'N2' THEN 4 WHEN 'N1' THEN 5 END,
                     gp.id
            LIMIT ? OFFSET ?
            """,
            params,
        ).fetchall()
    return {"items": [dict(row) for row in rows], "limit": limit, "offset": offset}


@app.get("/api/grammars/{grammar_id}")
def grammar_detail(grammar_id: str, learner_id: int = 1) -> dict[str, Any]:
    with db() as connection:
        grammar = connection.execute(
            """
            SELECT gp.*, COALESCE(lgs.recognition_score, 0) AS recognition_score,
                   COALESCE(lgs.production_score, 0) AS production_score,
                   COALESCE(lgs.review_count, 0) AS review_count
            FROM grammar_points AS gp
            LEFT JOIN learner_grammar_state AS lgs
              ON lgs.grammar_id = gp.id AND lgs.learner_id = ?
            WHERE gp.id = ?
            """,
            (learner_id, grammar_id),
        ).fetchone()
        if grammar is None:
            raise HTTPException(404, "语法不存在")
        rows = connection.execute(
            """
            SELECT s.id, s.kind, s.text, s.reading, s.translation_zh, s.status
            FROM sentence_grammar AS sg
            JOIN sentences AS s ON s.id = sg.sentence_id
            WHERE sg.grammar_id = ?
            ORDER BY CASE s.kind WHEN 'core' THEN 1 WHEN 'variant' THEN 2
                                  WHEN 'real' THEN 3 ELSE 4 END, s.id
            LIMIT 24
            """,
            (grammar_id,),
        ).fetchall()
        data = dict(grammar)
        data["sentences"] = [sentence_preview(connection, row) for row in rows]
        return data


@app.get("/api/library")
def library(
    level: str | None = None,
    kind: str | None = None,
    grammar_id: str | None = None,
    q: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=60),
    learner_id: int = 1,
) -> dict[str, Any]:
    if level and level not in LEVELS:
        raise HTTPException(400, "不支持的等级")
    if kind and kind not in KINDS:
        raise HTTPException(400, "不支持的句子类型")
    clauses = ["1 = 1"]
    params: list[Any] = []
    if level:
        clauses.append("s.level_code = ?")
        params.append(level)
    if kind:
        clauses.append("s.kind = ?")
        params.append(kind)
    if grammar_id:
        clauses.append("sg.grammar_id = ?")
        params.append(grammar_id)
    if q:
        clauses.append("(s.text LIKE ? OR s.reading LIKE ? OR s.translation_zh LIKE ?)")
        token = f"%{q}%"
        params.extend([token, token, token])
    where = " AND ".join(clauses)
    offset = (page - 1) * page_size
    with db() as connection:
        total = connection.execute(
            f"""
            SELECT COUNT(*) FROM sentences AS s
            LEFT JOIN sentence_grammar AS sg ON sg.sentence_id = s.id
            WHERE {where}
            """,
            params,
        ).fetchone()[0]
        rows = connection.execute(
            f"""
            SELECT s.id, s.level_code, s.kind, s.variant_type, s.text, s.reading,
                   s.translation_zh, s.status, sg.grammar_id, gp.title_zh AS grammar_title
            FROM sentences AS s
            LEFT JOIN sentence_grammar AS sg ON sg.sentence_id = s.id
            LEFT JOIN grammar_points AS gp ON gp.id = sg.grammar_id
            WHERE {where}
            ORDER BY s.id LIMIT ? OFFSET ?
            """,
            [*params, page_size, offset],
        ).fetchall()
        items = [sentence_preview(connection, row) for row in rows]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.get("/api/words")
def words(
    q: str = "",
    common_only: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=60),
) -> dict[str, Any]:
    query = q.strip()
    where: list[str] = []
    params: list[Any] = []
    if query:
        prefix = f"{query}%"
        where.append(
            """l.id IN (
                SELECT lexeme_id FROM lexeme_forms
                WHERE written_form LIKE ? OR reading LIKE ?
                UNION
                SELECT lexeme_id FROM lexeme_glosses WHERE gloss LIKE ?
            )"""
        )
        params.extend([prefix, prefix, prefix])
    if common_only:
        where.append("(l.common_rank IS NOT NULL OR EXISTS (SELECT 1 FROM lexeme_forms cf WHERE cf.lexeme_id=l.id AND cf.is_common=1))")
    condition = " AND ".join(where) or "1=1"
    offset = (page - 1) * page_size
    with db() as connection:
        total = connection.execute(
            f"SELECT COUNT(*) FROM lexemes l WHERE {condition}", params
        ).fetchone()[0]
        rows = connection.execute(
            f"""
            SELECT l.id, l.jmdict_seq, l.common_rank, ld.part_of_speech,
                   (SELECT written_form FROM lexeme_forms f WHERE f.lexeme_id=l.id
                    ORDER BY f.is_common DESC, f.id LIMIT 1) AS written_form,
                   (SELECT reading FROM lexeme_forms f WHERE f.lexeme_id=l.id
                    ORDER BY f.is_common DESC, f.id LIMIT 1) AS reading,
                   (SELECT GROUP_CONCAT(gloss, '；') FROM (
                       SELECT gloss FROM lexeme_glosses g
                       WHERE g.lexeme_id=l.id AND g.language_code='zho'
                       ORDER BY g.is_machine_generated, g.id LIMIT 3
                   )) AS gloss_zh,
                   (SELECT GROUP_CONCAT(gloss, '; ') FROM (
                       SELECT gloss FROM lexeme_glosses g
                       WHERE g.lexeme_id=l.id AND g.language_code='eng'
                       ORDER BY g.id LIMIT 4
                   )) AS gloss_en,
                   ld.memory_note_zh, ld.memory_note_status
            FROM lexemes l LEFT JOIN lexeme_details ld ON ld.lexeme_id=l.id
            WHERE {condition}
            ORDER BY CASE WHEN l.common_rank IS NULL THEN 1 ELSE 0 END,
                     l.common_rank, l.id
            LIMIT ? OFFSET ?
            """,
            [*params, page_size, offset],
        ).fetchall()
    return {"items": [dict(row) for row in rows], "total": total, "page": page, "page_size": page_size}


@app.get("/api/words/{lexeme_id}")
def word_detail(lexeme_id: int, learner_id: int = 1) -> dict[str, Any]:
    with db() as connection:
        row = connection.execute(
            """SELECT l.*,ld.part_of_speech,ld.usage_notes,ld.memory_note_zh,ld.memory_note_status
               FROM lexemes l LEFT JOIN lexeme_details ld ON ld.lexeme_id=l.id
               WHERE l.id=?""",
            (lexeme_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "没有找到这个词条")
        data = dict(row)
        data["saved"] = connection.execute(
            "SELECT 1 FROM learner_saved_lexemes WHERE learner_id=? AND lexeme_id=?",
            (learner_id, lexeme_id),
        ).fetchone() is not None
        forms = [dict(item) for item in connection.execute(
            "SELECT id,written_form,reading,is_common FROM lexeme_forms WHERE lexeme_id=? ORDER BY is_common DESC,id LIMIT 40",
            (lexeme_id,),
        )]
        data["forms"] = forms
        data["glosses"] = [dict(item) for item in connection.execute(
            "SELECT language_code,gloss,source,is_machine_generated FROM lexeme_glosses WHERE lexeme_id=? ORDER BY language_code DESC,is_machine_generated,id LIMIT 80",
            (lexeme_id,),
        )]
        preferred_form = forms[0] if forms else None
        data["furigana_segments"] = [dict(item) for item in connection.execute(
            "SELECT position,surface,reading FROM furigana_segments WHERE lexeme_form_id=? ORDER BY position",
            (preferred_form["id"] if preferred_form else -1,),
        )]
        data["course_examples"] = [dict(item) for item in connection.execute(
            """SELECT DISTINCT s.id,s.text,s.reading,s.translation_zh,s.level_code,s.kind
               FROM sentence_lexemes sl JOIN sentences s ON s.id=sl.sentence_id
               WHERE sl.lexeme_id=? ORDER BY CASE s.kind WHEN 'core' THEN 0 ELSE 1 END,s.id LIMIT 8""",
            (lexeme_id,),
        )]
        data["reading_examples"] = [dict(item) for item in connection.execute(
            """SELECT DISTINCT rs.id,rs.text,rw.title,rw.author
               FROM reading_sentence_lexemes rsl
               JOIN reading_sentences rs ON rs.id=rsl.reading_sentence_id
               JOIN reading_works rw ON rw.id=rs.work_id
               WHERE rsl.lexeme_id=? ORDER BY rs.id LIMIT 8""",
            (lexeme_id,),
        )]
        written = preferred_form["written_form"] if preferred_form else ""
        kanji = list(dict.fromkeys(char for char in written if "一" <= char <= "龯"))
        data["kanji"] = []
        for literal in kanji:
            entry = connection.execute("SELECT * FROM kanji_entries WHERE literal=?", (literal,)).fetchone()
            if entry:
                item = dict(entry)
                item["readings"] = [dict(value) for value in connection.execute(
                    "SELECT reading_type,reading FROM kanji_readings WHERE literal=? ORDER BY reading_type,reading",
                    (literal,),
                )]
                data["kanji"].append(item)
        return data


@app.post("/api/saved-words/toggle")
def toggle_saved_word(payload: SavedLexemeRequest) -> dict[str, Any]:
    with db(write=True) as connection:
        exists = connection.execute(
            "SELECT 1 FROM learner_saved_lexemes WHERE learner_id=? AND lexeme_id=?",
            (payload.learner_id, payload.lexeme_id),
        ).fetchone()
        if exists:
            connection.execute(
                "DELETE FROM learner_saved_lexemes WHERE learner_id=? AND lexeme_id=?",
                (payload.learner_id, payload.lexeme_id),
            )
            return {"ok": True, "saved": False}
        if connection.execute("SELECT 1 FROM lexemes WHERE id=?", (payload.lexeme_id,)).fetchone() is None:
            raise HTTPException(404, "词条不存在")
        connection.execute(
            """INSERT INTO learner_saved_lexemes
               (learner_id,lexeme_id,source_sentence_id,source_type)
               VALUES (?,?,?,?)""",
            (payload.learner_id, payload.lexeme_id, payload.source_sentence_id, payload.source_type),
        )
        return {"ok": True, "saved": True}


@app.get("/api/saved-words")
def saved_words(learner_id: int = 1, limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    with db() as connection:
        rows = connection.execute(
            """SELECT l.id,
                      (SELECT written_form FROM lexeme_forms f WHERE f.lexeme_id=l.id ORDER BY f.is_common DESC,f.id LIMIT 1) AS written_form,
                      (SELECT reading FROM lexeme_forms f WHERE f.lexeme_id=l.id ORDER BY f.is_common DESC,f.id LIMIT 1) AS reading,
                      (SELECT gloss FROM lexeme_glosses g WHERE g.lexeme_id=l.id AND g.language_code='zho' ORDER BY g.is_machine_generated,g.id LIMIT 1) AS gloss_zh,
                      sl.created_at,sl.source_type
               FROM learner_saved_lexemes sl JOIN lexemes l ON l.id=sl.lexeme_id
               WHERE sl.learner_id=? ORDER BY sl.created_at DESC LIMIT ?""",
            (learner_id, limit),
        ).fetchall()
    return {"items": [dict(row) for row in rows], "total": len(rows)}


@app.get("/api/readings")
def readings(
    work_id: str | None = None,
    q: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=60),
    learner_id: int = 1,
) -> dict[str, Any]:
    clauses = ["1=1"]
    params: list[Any] = []
    if work_id:
        clauses.append("rs.work_id=?")
        params.append(work_id)
    if q.strip():
        token = f"%{q.strip()}%"
        clauses.append("(rs.text LIKE ? OR rs.reading LIKE ? OR rw.title LIKE ? OR rw.author LIKE ?)")
        params.extend([token, token, token, token])
    where = " AND ".join(clauses)
    offset = (page - 1) * page_size
    with db() as connection:
        works = [dict(row) for row in connection.execute(
            """SELECT rw.*,
                      (SELECT COUNT(*) FROM reading_sentences crs WHERE crs.work_id=rw.id) AS sentence_count,
                      (SELECT COUNT(*) FROM reading_sections cs WHERE cs.work_id=rw.id) AS section_count,
                      (SELECT id FROM reading_sentences first_rs WHERE first_rs.work_id=rw.id ORDER BY position LIMIT 1) AS first_sentence_id,
                      COALESCE(lrs.progress_percent,0) AS progress_percent,
                      lrs.last_sentence_id,lrs.updated_at,
                      (SELECT COUNT(*) FROM reading_bookmarks rb
                       JOIN reading_sentences brs ON brs.id=rb.reading_sentence_id
                       WHERE rb.learner_id=? AND brs.work_id=rw.id) AS bookmark_count
               FROM reading_works rw
               LEFT JOIN learner_reading_state lrs ON lrs.work_id=rw.id AND lrs.learner_id=?
               ORDER BY rw.id""",
            (learner_id, learner_id),
        )]
        total = connection.execute(
            f"SELECT COUNT(*) FROM reading_sentences rs JOIN reading_works rw ON rw.id=rs.work_id WHERE {where}", params
        ).fetchone()[0]
        rows = connection.execute(
            f"""SELECT rs.id,rs.text,rs.reading,rs.translation_zh,rs.difficulty_score,
                       rs.status,rw.id AS work_id,rw.title,rw.author,
                       EXISTS(SELECT 1 FROM reading_bookmarks rb
                              WHERE rb.reading_sentence_id=rs.id AND rb.learner_id=?) AS bookmarked
                FROM reading_sentences rs JOIN reading_works rw ON rw.id=rs.work_id
                WHERE {where} ORDER BY rs.id LIMIT ? OFFSET ?""",
            [learner_id, *params, page_size, offset],
        ).fetchall()
    return {"items": [dict(row) for row in rows], "works": works, "total": total, "page": page, "page_size": page_size}


@app.get("/api/reading-sections")
def reading_sections(work_id: str, learner_id: int = 1) -> dict[str, Any]:
    with db() as connection:
        work = connection.execute(
            """SELECT rw.*,COALESCE(lrs.last_position,0) AS last_position,
                      COALESCE(lrs.progress_percent,0) AS progress_percent
               FROM reading_works rw
               LEFT JOIN learner_reading_state lrs ON lrs.work_id=rw.id AND lrs.learner_id=?
               WHERE rw.id=?""",
            (learner_id, work_id),
        ).fetchone()
        if work is None:
            raise HTTPException(404, "没有找到这部作品")
        rows = connection.execute(
            """SELECT sec.*,
                      (SELECT text FROM reading_sentences rs
                       WHERE rs.work_id=sec.work_id AND rs.position=sec.start_position) AS opening_text
               FROM reading_sections sec WHERE sec.work_id=?
               ORDER BY sec.section_number""",
            (work_id,),
        ).fetchall()
    return {"work": dict(work), "items": [dict(row) for row in rows]}


@app.get("/api/reading-sections/{section_id}")
def reading_section_detail(section_id: str, learner_id: int = 1) -> dict[str, Any]:
    with db() as connection:
        section = connection.execute(
            """SELECT sec.*,rw.title,rw.author,rw.guide_zh,rw.era_note_zh
               FROM reading_sections sec JOIN reading_works rw ON rw.id=sec.work_id
               WHERE sec.id=?""",
            (section_id,),
        ).fetchone()
        if section is None:
            raise HTTPException(404, "没有找到这个阅读单元")
        rows = connection.execute(
            """SELECT rs.id,rs.position,rs.text,rs.reading,rs.difficulty_score,
                      EXISTS(SELECT 1 FROM reading_bookmarks rb
                             WHERE rb.reading_sentence_id=rs.id AND rb.learner_id=?) AS bookmarked
               FROM reading_sentences rs
               WHERE rs.work_id=? AND rs.position BETWEEN ? AND ?
               ORDER BY rs.position""",
            (learner_id, section["work_id"], section["start_position"], section["end_position"]),
        ).fetchall()
    return {"section": dict(section), "items": [dict(row) for row in rows]}


@app.get("/api/reading-bookmarks")
def reading_bookmarks(learner_id: int = 1, limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    with db() as connection:
        rows = connection.execute(
            """SELECT rb.reading_sentence_id AS id,rb.note_zh,rb.created_at,
                      rs.text,rs.position,rw.id AS work_id,rw.title,rw.author
               FROM reading_bookmarks rb
               JOIN reading_sentences rs ON rs.id=rb.reading_sentence_id
               JOIN reading_works rw ON rw.id=rs.work_id
               WHERE rb.learner_id=? ORDER BY rb.created_at DESC LIMIT ?""",
            (learner_id, limit),
        ).fetchall()
    return {"items": [dict(row) for row in rows], "total": len(rows)}


@app.get("/api/readings/{sentence_id}")
def reading_detail(sentence_id: str, learner_id: int = 1) -> dict[str, Any]:
    with db() as connection:
        row = connection.execute(
            """SELECT rs.*,rw.title,rw.author,rw.source_name,rw.source_url,rw.source_file_url,
                      rw.copyright_status,rw.orthography,rw.guide_zh,
                      rw.guide_status,rw.era_note_zh
               FROM reading_sentences rs JOIN reading_works rw ON rw.id=rs.work_id WHERE rs.id=?""",
            (sentence_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "没有找到这个阅读句")
        data = dict(row)
        data["ruby_segments"] = [dict(item) for item in connection.execute(
            "SELECT position,surface,reading FROM reading_sentence_ruby_segments WHERE reading_sentence_id=? ORDER BY position",
            (sentence_id,),
        )]
        data["tokens"] = [dict(item) for item in connection.execute(
            """SELECT rsl.*, (SELECT GROUP_CONCAT(gloss,'；') FROM (
                       SELECT gloss FROM lexeme_glosses g WHERE g.lexeme_id=rsl.lexeme_id AND g.language_code='zho' ORDER BY is_machine_generated,id LIMIT 3
                   )) AS gloss_zh,
                   (SELECT GROUP_CONCAT(gloss,'; ') FROM (
                       SELECT gloss FROM lexeme_glosses g WHERE g.lexeme_id=rsl.lexeme_id AND g.language_code='eng' ORDER BY id LIMIT 3
                   )) AS gloss_en
               FROM reading_sentence_lexemes rsl WHERE reading_sentence_id=? ORDER BY position""",
            (sentence_id,),
        )]
        for token in data["tokens"]:
            token["particle_note_zh"] = particle_note(token["surface"], token["part_of_speech"])
            token["saved"] = bool(token["lexeme_id"] and connection.execute(
                "SELECT 1 FROM learner_saved_lexemes WHERE learner_id=? AND lexeme_id=?",
                (learner_id, token["lexeme_id"]),
            ).fetchone())
        data["bookmarked"] = connection.execute(
            "SELECT 1 FROM reading_bookmarks WHERE learner_id=? AND reading_sentence_id=?",
            (learner_id, sentence_id),
        ).fetchone() is not None
        data["previous_id"] = connection.execute(
            "SELECT id FROM reading_sentences WHERE work_id=? AND position<? ORDER BY position DESC LIMIT 1",
            (data["work_id"], data["position"]),
        ).fetchone()
        data["previous_id"] = data["previous_id"][0] if data["previous_id"] else None
        data["next_id"] = connection.execute(
            "SELECT id FROM reading_sentences WHERE work_id=? AND position>? ORDER BY position LIMIT 1",
            (data["work_id"], data["position"]),
        ).fetchone()
        data["next_id"] = data["next_id"][0] if data["next_id"] else None
        section = connection.execute(
            """SELECT id,title_zh,section_number,summary_zh FROM reading_sections
               WHERE work_id=? AND ? BETWEEN start_position AND end_position LIMIT 1""",
            (data["work_id"], data["position"]),
        ).fetchone()
        data["section"] = dict(section) if section else None
        data["literary_notes"] = [
            dict(rule)
            for rule in connection.execute(
                """SELECT id,pattern,title_zh,explanation_zh,nuance_zh,status
                   FROM literary_expression_rules
                   WHERE instr(?,pattern)>0 ORDER BY length(pattern) DESC""",
                (data["text"],),
            )
        ]
        return data


@app.post("/api/readings/progress")
def save_reading_progress(payload: ReadingProgressRequest) -> dict[str, Any]:
    with db(write=True) as connection:
        row = connection.execute(
            """SELECT rs.id,rs.work_id,rs.position,
                      (SELECT COUNT(*) FROM reading_sentences total WHERE total.work_id=rs.work_id) AS total
               FROM reading_sentences rs WHERE rs.id=?""",
            (payload.sentence_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "阅读句不存在")
        percent = round(row["position"] * 100 / max(1, row["total"]), 2)
        connection.execute(
            """INSERT INTO learner_reading_state
               (learner_id,work_id,last_sentence_id,last_position,progress_percent,updated_at)
               VALUES (?,?,?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(learner_id,work_id) DO UPDATE SET
                 last_sentence_id=excluded.last_sentence_id,
                 last_position=MAX(learner_reading_state.last_position,excluded.last_position),
                 progress_percent=MAX(learner_reading_state.progress_percent,excluded.progress_percent),
                 updated_at=CURRENT_TIMESTAMP""",
            (payload.learner_id, row["work_id"], row["id"], row["position"], percent),
        )
        connection.execute(
            "INSERT INTO reading_history(learner_id,reading_sentence_id) VALUES (?,?)",
            (payload.learner_id, payload.sentence_id),
        )
    return {"ok": True, "work_id": row["work_id"], "position": row["position"], "progress_percent": percent}


@app.post("/api/reading-bookmarks/toggle")
def toggle_reading_bookmark(payload: BookmarkRequest) -> dict[str, Any]:
    with db(write=True) as connection:
        exists = connection.execute(
            "SELECT 1 FROM reading_bookmarks WHERE learner_id=? AND reading_sentence_id=?",
            (payload.learner_id, payload.sentence_id),
        ).fetchone()
        if exists:
            connection.execute(
                "DELETE FROM reading_bookmarks WHERE learner_id=? AND reading_sentence_id=?",
                (payload.learner_id, payload.sentence_id),
            )
            return {"ok": True, "bookmarked": False}
        if connection.execute("SELECT 1 FROM reading_sentences WHERE id=?", (payload.sentence_id,)).fetchone() is None:
            raise HTTPException(404, "阅读句不存在")
        connection.execute(
            "INSERT INTO reading_bookmarks(learner_id,reading_sentence_id,note_zh) VALUES (?,?,?)",
            (payload.learner_id, payload.sentence_id, payload.note_zh),
        )
    return {"ok": True, "bookmarked": True}


@app.post("/api/analyze")
def analyze(payload: AnalyzeRequest) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    with db() as connection:
        for position, token in enumerate(SUDACHI.tokenize(payload.text, SPLIT_MODE)):
            surface = token.surface()
            reading = katakana_to_hiragana(token.reading_form() if token.reading_form() != "*" else surface)
            dictionary_form = token.dictionary_form() or surface
            match = find_lexeme(connection, dictionary_form, reading) or find_lexeme(connection, surface, reading)
            lexeme_id = match["lexeme_id"] if match else None
            glosses = []
            if lexeme_id is not None:
                glosses = [dict(row) for row in connection.execute(
                    """SELECT language_code,gloss,source,is_machine_generated FROM lexeme_glosses
                       WHERE lexeme_id=? ORDER BY CASE language_code WHEN 'zho' THEN 0 ELSE 1 END,is_machine_generated,id LIMIT 6""",
                    (lexeme_id,),
                )]
            results.append({
                "position": position,
                "surface": surface,
                "dictionary_form": dictionary_form,
                "reading": reading,
                "part_of_speech": token.part_of_speech()[0],
                "start_offset": token.begin(),
                "end_offset": token.end(),
                "lexeme_id": lexeme_id,
                "glosses": glosses,
                "particle_note_zh": particle_note(surface, token.part_of_speech()[0]),
            })
    structure = clause_analysis(payload.text, results)
    return {
        "text": payload.text,
        "direction": "ja-structure",
        "tokens": results,
        **structure,
    }


@app.post("/api/profile/level")
def update_level(payload: LevelRequest) -> dict[str, Any]:
    with db(write=True) as connection:
        result = connection.execute(
            "UPDATE learner_profiles SET current_level_code = ? WHERE id = ?",
            (payload.level, payload.learner_id),
        )
        if result.rowcount == 0:
            raise HTTPException(404, "学习者不存在")
    return {"ok": True, "level": payload.level}


@app.get("/api/profile/retention")
def get_retention(learner_id: int = 1) -> dict[str, Any]:
    with db() as connection:
        row = connection.execute(
            "SELECT desired_retention FROM learner_preferences WHERE learner_id=?",
            (learner_id,),
        ).fetchone()
    return {"learner_id": learner_id, "desired_retention": float(row[0]) if row else 0.9}


@app.post("/api/profile/retention")
def update_retention(payload: RetentionRequest) -> dict[str, Any]:
    with db(write=True) as connection:
        if connection.execute("SELECT 1 FROM learner_profiles WHERE id=?", (payload.learner_id,)).fetchone() is None:
            raise HTTPException(404, "学习者不存在")
        connection.execute(
            """INSERT INTO learner_preferences(learner_id,desired_retention) VALUES (?,?)
               ON CONFLICT(learner_id) DO UPDATE SET desired_retention=excluded.desired_retention""",
            (payload.learner_id, payload.desired_retention),
        )
    return {"ok": True, "desired_retention": payload.desired_retention}


@app.post("/api/reset")
def reset_progress(payload: ResetRequest) -> dict[str, Any]:
    if payload.confirmation != "RESET":
        raise HTTPException(400, "需要明确确认重置")
    with db(write=True) as connection:
        connection.execute(
            "DELETE FROM app_learning_events WHERE learner_id = ?", (payload.learner_id,)
        )
        connection.execute(
            "DELETE FROM learner_sentence_state WHERE learner_id = ?", (payload.learner_id,)
        )
        connection.execute(
            "DELETE FROM learner_grammar_state WHERE learner_id = ?", (payload.learner_id,)
        )
        connection.execute(
            "DELETE FROM learner_lexeme_state WHERE learner_id = ?", (payload.learner_id,)
        )
        connection.execute(
            "DELETE FROM app_review_logs WHERE learner_id = ?", (payload.learner_id,)
        )
        connection.execute(
            "DELETE FROM learner_reading_state WHERE learner_id = ?", (payload.learner_id,)
        )
        connection.execute(
            "DELETE FROM reading_history WHERE learner_id = ?", (payload.learner_id,)
        )
        connection.execute(
            "DELETE FROM reading_bookmarks WHERE learner_id = ?", (payload.learner_id,)
        )
        connection.execute(
            "DELETE FROM learner_saved_lexemes WHERE learner_id = ?", (payload.learner_id,)
        )
        connection.execute(
            "DELETE FROM learner_sentence_exposure WHERE learner_id = ?", (payload.learner_id,)
        )
        connection.execute(
            "DELETE FROM learner_unit_state WHERE learner_id = ?", (payload.learner_id,)
        )
    return {"ok": True}

