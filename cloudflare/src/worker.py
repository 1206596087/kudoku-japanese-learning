from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from fsrs import Card, Rating, Scheduler
from js import Object, TextEncoder, Uint8Array, crypto
from pydantic import BaseModel, Field
from pyodide.ffi import to_js
from workers import asgi

LEVELS = ("N5", "N4", "N3", "N2", "N1")
KINDS = ("core", "variant", "real", "personal")
SESSION_COOKIE = "kudoku_session"
# Cloudflare Workers' Web Crypto PBKDF2 implementation currently caps the
# iteration count at 100,000. The hosted app is additionally protected by its
# own session login and can be placed behind Cloudflare Access.
PASSWORD_ITERATIONS = 100_000
PUBLIC_API_PATHS = {"/api/health", "/api/account/status", "/api/account/setup", "/api/account/login"}
PARTICLE_NOTES = {
    "は": "提示主题或形成对比：接下来是在谈它；读作「わ」。", "が": "标记主语或新出现、被强调的信息，也可连接转折。",
    "を": "标记动作直接作用的对象。", "に": "常标记到达点、对象、存在位置或具体时间点。", "へ": "表示动作朝向的方向；作助词时读「え」。",
    "で": "常标记动作发生的场所、手段或原因范围。", "と": "可表示共同对象、并列、引用内容或条件结果。", "から": "表示起点、来源或原因。",
    "まで": "表示时间、空间或程度的终点。", "の": "连接名词表示所属或修饰，也可名词化。", "も": "表示追加或同类，相当于‘也’。",
    "ね": "句末寻求共识或确认。", "よ": "句末把新信息明确告诉对方。",
}

app = FastAPI(title="句读 · 日语学习（Cloudflare）", version="0.4.0-cloud", docs_url=None, redoc_url=None)
Default = asgi.entrypoint(app)


class AccountCredentials(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_.\-\u4e00-\u9fff]+$")
    password: str = Field(min_length=8, max_length=128)


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


def envs(request: Request) -> tuple[Any, Any]:
    env = request.scope["env"]
    # D1 bindings are borrowed JavaScript proxies. Keep their parent `env`
    # alive on request.state for the complete FastAPI request lifecycle.
    request.state.cloudflare_env = env
    return env.CONTENT_DB, env.USER_DB


async def rows(database: Any, sql: str, *params: Any) -> list[dict[str, Any]]:
    statement = database.prepare(sql)
    if params:
        statement = statement.bind(*params)
    result = await statement.all()
    # `results` is already a Python list, while each row may still be a
    # borrowed JavaScript proxy. Convert each row before advancing/exhausting
    # the iterator so no destroyed proxies escape this function.
    native: list[dict[str, Any]] = []
    for row in result.results:
        native.append(row.to_py(dict_converter=dict) if hasattr(row, "to_py") else dict(row))
    return native


async def one(database: Any, sql: str, *params: Any) -> dict[str, Any] | None:
    result = await rows(database, sql, *params)
    return result[0] if result else None


async def scalar(database: Any, sql: str, *params: Any, default: Any = 0) -> Any:
    row = await one(database, sql, *params)
    return next(iter(row.values())) if row else default


async def execute(database: Any, sql: str, *params: Any) -> Any:
    statement = database.prepare(sql)
    if params:
        statement = statement.bind(*params)
    return await statement.run()


def javascript_value(value: Any) -> Any:
    """Convert nested Python values into Web API-compatible JavaScript values."""
    return to_js(value, dict_converter=Object.fromEntries)


async def password_digest(
    password: str, salt_hex: str, iterations: int = PASSWORD_ITERATIONS
) -> str:
    """Derive the same PBKDF2-SHA256 hash used by the local Python app.

    Pyodide's hashlib intentionally omits ``pbkdf2_hmac``. Cloudflare Workers
    exposes the standards-based Web Crypto API, so use that implementation and
    keep existing local account hashes fully compatible with the deployed app.
    """
    password_bytes = TextEncoder.new().encode(password)
    salt = Uint8Array.new(javascript_value(list(bytes.fromhex(salt_hex))))
    key = await crypto.subtle.importKey(
        "raw",
        password_bytes,
        javascript_value({"name": "PBKDF2"}),
        False,
        javascript_value(["deriveBits"]),
    )
    derived = await crypto.subtle.deriveBits(
        javascript_value(
            {
                "name": "PBKDF2",
                "hash": "SHA-256",
                "salt": salt,
                "iterations": iterations,
            }
        ),
        key,
        256,
    )
    return bytes(Uint8Array.new(derived).to_py()).hex()


async def authenticated_account(user_db: Any, token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    return await one(user_db, """SELECT a.id,a.learner_id,a.username,s.expires_at FROM app_sessions s JOIN app_accounts a ON a.id=s.account_id WHERE s.token_hash=? AND s.expires_at>CURRENT_TIMESTAMP""", hashlib.sha256(token.encode()).hexdigest())


async def create_session(user_db: Any, account_id: int) -> str:
    raw = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    expires = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    await execute(user_db, "DELETE FROM app_sessions WHERE expires_at<=CURRENT_TIMESTAMP")
    await execute(user_db, "INSERT INTO app_sessions(token_hash,account_id,expires_at) VALUES (?,?,?)", token_hash, account_id, expires)
    return raw


@app.middleware("http")
async def private_account_guard(request: Request, call_next):
    if not request.url.path.startswith("/api/") or request.url.path in PUBLIC_API_PATHS:
        return await call_next(request)
    _, user_db = envs(request)
    account = await authenticated_account(user_db, request.cookies.get(SESSION_COOKIE))
    if account is None:
        return JSONResponse({"detail": "请先登录私人学习账号"}, status_code=401)
    request.state.account = account
    return await call_next(request)


async def ruby_segments(content_db: Any, sentence_id: str) -> list[dict[str, Any]]:
    return await rows(content_db, "SELECT position,surface,reading FROM sentence_ruby_segments WHERE sentence_id=? ORDER BY position", sentence_id)


async def sentence_tokens(content_db: Any, sentence_id: str) -> list[dict[str, Any]]:
    tokens = await rows(content_db, """SELECT sl.position,sl.lexeme_id,sl.surface,sl.dictionary_form,sl.reading,sl.part_of_speech,sl.start_offset,sl.end_offset,
        (SELECT GROUP_CONCAT(gloss,'；') FROM (SELECT gloss FROM lexeme_glosses WHERE lexeme_id=sl.lexeme_id AND language_code='zho' ORDER BY is_machine_generated,id LIMIT 3)) AS gloss_zh,
        (SELECT GROUP_CONCAT(gloss,'; ') FROM (SELECT gloss FROM lexeme_glosses WHERE lexeme_id=sl.lexeme_id AND language_code='eng' ORDER BY id LIMIT 3)) AS gloss_en
        FROM sentence_lexemes sl WHERE sl.sentence_id=? ORDER BY sl.position""", sentence_id)
    for token in tokens:
        token["particle_note_zh"] = PARTICLE_NOTES.get(token["surface"], "这是连接前后成分的助词；具体作用需要结合所在句型判断。") if token["part_of_speech"] == "助詞" else None
    return tokens


async def sentence_preview(content_db: Any, item: dict[str, Any]) -> dict[str, Any]:
    preview = dict(item)
    preview["ruby_segments"] = await ruby_segments(content_db, item["id"])
    return preview


async def sentence_previews(
    content_db: Any, items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    return [await sentence_preview(content_db, item) for item in items]


async def sentence_detail(content_db: Any, user_db: Any, sentence_id: str, learner_id: int = 1) -> dict[str, Any]:
    data = await one(content_db, """SELECT s.*,sg.grammar_id,sg.role AS grammar_role,gp.title_zh AS grammar_title,gp.pattern AS grammar_pattern,
        gp.meaning_zh AS grammar_meaning,gp.formation_zh AS grammar_formation,gp.rationale_zh AS grammar_rationale,gp.nuance_zh AS grammar_nuance,
        gp.common_mistakes_zh AS grammar_mistakes,gp.status AS grammar_status FROM sentences s LEFT JOIN sentence_grammar sg ON sg.sentence_id=s.id
        LEFT JOIN grammar_points gp ON gp.id=sg.grammar_id WHERE s.id=? LIMIT 1""", sentence_id)
    if data is None:
        raise HTTPException(404, "没有找到这个句子")
    data["ruby_segments"] = await ruby_segments(content_db, sentence_id)
    data["tokens"] = await sentence_tokens(content_db, sentence_id)
    data["chunks"] = await rows(content_db, "SELECT position,surface,reading,meaning_zh,function_zh FROM sentence_chunks WHERE sentence_id=? ORDER BY position", sentence_id)
    data["progress"] = await one(user_db, """SELECT listening_score,comprehension_score,production_score,review_count,due_at,last_rating,last_reviewed_at,lapse_count,last_error_type,fsrs_card_json FROM learner_sentence_state WHERE learner_id=? AND sentence_id=?""", learner_id, sentence_id) or {"listening_score":0,"comprehension_score":0,"production_score":0,"review_count":0,"due_at":None,"last_rating":None,"last_reviewed_at":None,"lapse_count":0,"last_error_type":None,"fsrs_card_json":None}
    root_id = sentence_id if data["kind"] == "core" else data["parent_sentence_id"]
    data["root_id"] = root_id
    data["exposure"] = await one(user_db, "SELECT shown_count,completed_count,last_shown_at,last_completed_at FROM learner_sentence_exposure WHERE learner_id=? AND sentence_id=?", learner_id, sentence_id) or {"shown_count":0,"completed_count":0,"last_shown_at":None,"last_completed_at":None}
    data["unit_progress"] = await one(user_db, "SELECT shown_count,completed_sentences,mastery_percent,last_activity_at FROM learner_unit_state WHERE learner_id=? AND root_sentence_id=?", learner_id, root_id) or {"shown_count":0,"completed_sentences":0,"mastery_percent":0,"last_activity_at":None}
    children = await rows(content_db, """SELECT id,kind,variant_type,text,reading,translation_zh,grammar_note_zh,status,audio_text FROM sentences WHERE parent_sentence_id=? ORDER BY CASE kind WHEN 'variant' THEN 1 WHEN 'real' THEN 2 ELSE 3 END,id""", root_id)
    child_progress: dict[str, dict[str, Any]] = {}
    if children:
        placeholders = ",".join("?" for _ in children)
        states = await rows(user_db, f"SELECT sentence_id,listening_score,comprehension_score,production_score FROM learner_sentence_state WHERE learner_id=? AND sentence_id IN ({placeholders})", learner_id, *(child["id"] for child in children))
        child_progress = {state["sentence_id"]: state for state in states}
    groups: dict[str, list[dict[str, Any]]] = {"variant": [], "real": [], "personal": []}
    for child in children:
        state = child_progress.get(child["id"], {})
        child.update({"listening_score":state.get("listening_score",0),"comprehension_score":state.get("comprehension_score",0),"production_score":state.get("production_score",0),"ruby_segments":await ruby_segments(content_db,child["id"])})
        groups[child["kind"]].append(child)
    data["children"] = groups
    return data


def score_rating(listening: int, comprehension: int, production: int) -> Rating:
    values=(listening,comprehension,production); lowest=min(values); average=sum(values)/3
    if lowest<=1: return Rating.Again
    if lowest==2: return Rating.Hard
    if lowest>=4 and average>=4.5: return Rating.Easy
    return Rating.Good


def weakest_dimension(listening: int, comprehension: int, production: int) -> str:
    values={"listening":listening,"comprehension":comprehension,"production":production}; minimum=min(values.values()); weakest=[k for k,v in values.items() if v==minimum]
    return weakest[0] if len(weakest)==1 else "mixed"


def schedule_review(card_json: str | None, rating: Rating, retention: float) -> tuple[Card,str]:
    scheduler=Scheduler(desired_retention=retention); card=Card.from_json(card_json) if card_json else Card(); card,_=scheduler.review_card(card,rating,review_datetime=datetime.now(timezone.utc))
    return card,card.due.strftime("%Y-%m-%d %H:%M:%S")


@app.get("/api/health")
async def health(request: Request) -> dict[str,object]:
    content_db,user_db=envs(request); counts=await rows(content_db,"SELECT kind,COUNT(*) AS count FROM sentences GROUP BY kind")
    return {"ok":sum(x["count"] for x in counts)==6120,"runtime":"cloudflare-workers-python","counts":{x["kind"]:x["count"] for x in counts},"total":sum(x["count"] for x in counts),"dictionary_entries":await scalar(content_db,"SELECT COUNT(*) FROM lexemes"),"reading_sentences":await scalar(content_db,"SELECT COUNT(*) FROM reading_sentences"),"accounts":await scalar(user_db,"SELECT COUNT(*) FROM app_accounts")}


@app.get("/api/account/status")
async def account_status(request: Request) -> dict[str,Any]:
    _,user_db=envs(request); configured=await scalar(user_db,"SELECT COUNT(*) FROM app_accounts")>0; account=await authenticated_account(user_db,request.cookies.get(SESSION_COOKIE))
    return {"configured":configured,"authenticated":bool(account),"username":account["username"] if account else None,"auth_disabled":False}


@app.post("/api/account/login")
async def account_login(payload: AccountCredentials, request: Request) -> JSONResponse:
    _,user_db=envs(request); account=await one(user_db,"SELECT id,username,password_hash,password_salt,password_iterations FROM app_accounts WHERE username=?",payload.username.strip())
    valid=account and hmac.compare_digest(
        account["password_hash"],
        await password_digest(
            payload.password, account["password_salt"], account["password_iterations"]
        ),
    )
    if not valid: raise HTTPException(401,"用户名或密码不正确")
    token=await create_session(user_db,account["id"]); await execute(user_db,"UPDATE app_accounts SET last_login_at=CURRENT_TIMESTAMP WHERE id=?",account["id"])
    response=JSONResponse({"ok":True,"username":account["username"]}); response.set_cookie(SESSION_COOKIE,token,max_age=30*86400,httponly=True,samesite="strict",secure=True); return response


@app.post("/api/account/setup")
async def account_setup(payload: AccountCredentials, request: Request) -> JSONResponse:
    _,user_db=envs(request)
    if await scalar(user_db,"SELECT COUNT(*) FROM app_accounts"): raise HTTPException(409,"私人账号已经创建")
    learner=await one(user_db,"SELECT id FROM learner_profiles ORDER BY id LIMIT 1")
    if learner is None: raise HTTPException(500,"学习档案不存在")
    salt=secrets.token_hex(16); digest=await password_digest(payload.password,salt); result=await execute(user_db,"INSERT INTO app_accounts(learner_id,username,password_hash,password_salt,password_iterations,last_login_at) VALUES (?,?,?,?,?,CURRENT_TIMESTAMP)",learner["id"],payload.username.strip(),digest,salt,PASSWORD_ITERATIONS)
    token=await create_session(user_db,int(result.meta.last_row_id)); response=JSONResponse({"ok":True,"username":payload.username.strip()}); response.set_cookie(SESSION_COOKIE,token,max_age=30*86400,httponly=True,samesite="strict",secure=True); return response


@app.post("/api/account/logout")
async def account_logout(request: Request) -> JSONResponse:
    _,user_db=envs(request); raw=request.cookies.get(SESSION_COOKIE)
    if raw: await execute(user_db,"DELETE FROM app_sessions WHERE token_hash=?",hashlib.sha256(raw.encode()).hexdigest())
    response=JSONResponse({"ok":True}); response.delete_cookie(SESSION_COOKIE); return response


@app.get("/api/dashboard")
async def dashboard(request: Request, learner_id: int=1) -> dict[str,Any]:
    content_db,user_db=envs(request); profile=await one(user_db,"SELECT id,display_name,current_level_code FROM learner_profiles WHERE id=?",learner_id)
    if profile is None: raise HTTPException(404,"学习者不存在")
    today=await scalar(user_db,"SELECT COUNT(DISTINCT sentence_id) FROM app_learning_events WHERE learner_id=? AND date(reviewed_at)=date('now')",learner_id); mastered=await scalar(user_db,"SELECT COUNT(*) FROM learner_sentence_state WHERE learner_id=? AND listening_score>=4 AND comprehension_score>=4 AND production_score>=3",learner_id)
    averages=await one(user_db,"SELECT COALESCE(AVG(listening_score),0) AS listening,COALESCE(AVG(comprehension_score),0) AS comprehension,COALESCE(AVG(production_score),0) AS production FROM learner_sentence_state WHERE learner_id=?",learner_id); due=await scalar(user_db,"SELECT COUNT(*) FROM learner_sentence_state WHERE learner_id=? AND due_at IS NOT NULL AND due_at<=CURRENT_TIMESTAMP",learner_id)
    levels=await rows(content_db,"SELECT l.code,l.label_zh,l.goal_zh,COUNT(s.id) AS sentence_count,SUM(CASE WHEN s.kind='core' THEN 1 ELSE 0 END) AS core_count FROM levels l LEFT JOIN sentences s ON s.level_code=l.code WHERE l.code IN ('N5','N4','N3','N2','N1') GROUP BY l.code,l.sort_order ORDER BY l.sort_order")
    return {"profile":profile,"today_count":today,"mastered_count":mastered,"averages":{k:round(float(v),2) for k,v in averages.items()},"due_count":due,"levels":levels,"recent_grammars":[]}


@app.get("/api/sentences/next")
async def next_sentence(request: Request, level: str=Query("N5"), learner_id: int=1, exclude: str|None=None, selection: Literal["adaptive","random"]="adaptive") -> dict[str,Any]:
    if level not in LEVELS: raise HTTPException(400,"不支持的等级")
    content_db,user_db=envs(request); candidates=await rows(content_db,"SELECT id FROM sentences WHERE kind='core' AND level_code=? AND (? IS NULL OR id<>?) ORDER BY RANDOM()",level,exclude,exclude)
    if not candidates: raise HTTPException(404,f"{level} 暂无可学习内容")
    if selection=="random": chosen=candidates[0]["id"]
    else:
        exposure=await rows(user_db,"SELECT sentence_id,shown_count,completed_count,last_shown_at FROM learner_sentence_exposure WHERE learner_id=?",learner_id); mapping={x["sentence_id"]:x for x in exposure}; candidates.sort(key=lambda x:(0 if x["id"] not in mapping else 1,mapping.get(x["id"],{}).get("completed_count",0),mapping.get(x["id"],{}).get("last_shown_at") or "1970")); chosen=candidates[0]["id"]
    await execute(user_db,"INSERT INTO learner_sentence_exposure(learner_id,sentence_id,shown_count,last_shown_at) VALUES (?,?,1,CURRENT_TIMESTAMP) ON CONFLICT(learner_id,sentence_id) DO UPDATE SET shown_count=shown_count+1,last_shown_at=CURRENT_TIMESTAMP",learner_id,chosen)
    await execute(user_db,"INSERT INTO learner_unit_state(learner_id,root_sentence_id,shown_count,last_activity_at) VALUES (?,?,1,CURRENT_TIMESTAMP) ON CONFLICT(learner_id,root_sentence_id) DO UPDATE SET shown_count=shown_count+1,last_activity_at=CURRENT_TIMESTAMP",learner_id,chosen)
    result=await sentence_detail(content_db,user_db,chosen,learner_id); result["selection_mode"]=selection; return result


@app.get("/api/sentences/{sentence_id}")
async def get_sentence(sentence_id: str, request: Request, learner_id: int=1) -> dict[str,Any]:
    content_db,user_db=envs(request); return await sentence_detail(content_db,user_db,sentence_id,learner_id)


@app.post("/api/progress")
async def save_progress(payload: ProgressRequest, request: Request) -> dict[str,Any]:
    content_db,user_db=envs(request); sentence=await one(content_db,"SELECT id,kind,parent_sentence_id FROM sentences WHERE id=?",payload.sentence_id)
    if sentence is None: raise HTTPException(404,"句子不存在")
    rating=score_rating(payload.listening_score,payload.comprehension_score,payload.production_score); error_type=payload.error_type or weakest_dimension(payload.listening_score,payload.comprehension_score,payload.production_score); current=await one(user_db,"SELECT fsrs_card_json FROM learner_sentence_state WHERE learner_id=? AND sentence_id=?",payload.learner_id,payload.sentence_id); retention=float(await scalar(user_db,"SELECT desired_retention FROM learner_preferences WHERE learner_id=?",payload.learner_id,default=0.9)); card,due_text=schedule_review(current["fsrs_card_json"] if current else None,rating,retention); reviewed=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    await execute(user_db,"""INSERT INTO learner_sentence_state(learner_id,sentence_id,listening_score,comprehension_score,production_score,review_count,due_at,fsrs_card_json,last_rating,last_reviewed_at,lapse_count,last_error_type) VALUES (?,?,?,?,?,1,?,?,?,?,?,?) ON CONFLICT(learner_id,sentence_id) DO UPDATE SET listening_score=excluded.listening_score,comprehension_score=excluded.comprehension_score,production_score=excluded.production_score,review_count=review_count+1,due_at=excluded.due_at,fsrs_card_json=excluded.fsrs_card_json,last_rating=excluded.last_rating,last_reviewed_at=excluded.last_reviewed_at,lapse_count=lapse_count+excluded.lapse_count,last_error_type=excluded.last_error_type""",payload.learner_id,payload.sentence_id,payload.listening_score,payload.comprehension_score,payload.production_score,due_text,card.to_json(),rating.value,reviewed,1 if rating==Rating.Again else 0,error_type)
    await execute(user_db,"INSERT INTO app_review_logs(learner_id,sentence_id,rating,review_mode,error_type,card_json,reviewed_at) VALUES (?,?,?,?,?,?,?)",payload.learner_id,payload.sentence_id,rating.value,payload.review_mode,error_type,card.to_json(),reviewed); await execute(user_db,"INSERT INTO learner_sentence_exposure(learner_id,sentence_id,completed_count,last_completed_at) VALUES (?,?,1,CURRENT_TIMESTAMP) ON CONFLICT(learner_id,sentence_id) DO UPDATE SET completed_count=completed_count+1,last_completed_at=CURRENT_TIMESTAMP",payload.learner_id,payload.sentence_id)
    root_id=sentence["id"] if sentence["kind"]=="core" else sentence["parent_sentence_id"]; family=await rows(content_db,"SELECT id FROM sentences WHERE id=? OR parent_sentence_id=?",root_id,root_id); placeholders=",".join("?" for _ in family); states=await rows(user_db,f"SELECT listening_score,comprehension_score,production_score FROM learner_sentence_state WHERE learner_id=? AND sentence_id IN ({placeholders})",payload.learner_id,*(x["id"] for x in family)); mastery=round(sum((x["listening_score"]+x["comprehension_score"]+x["production_score"])/15*100 for x in states)/len(states),1) if states else 0; await execute(user_db,"INSERT INTO learner_unit_state(learner_id,root_sentence_id,completed_sentences,mastery_percent,last_activity_at) VALUES (?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(learner_id,root_sentence_id) DO UPDATE SET completed_sentences=excluded.completed_sentences,mastery_percent=excluded.mastery_percent,last_activity_at=CURRENT_TIMESTAMP",payload.learner_id,root_id,len(states),mastery)
    grammars=await rows(content_db,"SELECT grammar_id FROM sentence_grammar WHERE sentence_id=?",payload.sentence_id); recognition=round((payload.listening_score+payload.comprehension_score)/2)
    for grammar in grammars:
        await execute(user_db,"INSERT INTO learner_grammar_state(learner_id,grammar_id,recognition_score,production_score,review_count,due_at) VALUES (?,?,?,?,1,?) ON CONFLICT(learner_id,grammar_id) DO UPDATE SET recognition_score=MAX(recognition_score,excluded.recognition_score),production_score=MAX(production_score,excluded.production_score),review_count=review_count+1,due_at=excluded.due_at",payload.learner_id,grammar["grammar_id"],recognition,payload.production_score,due_text); await execute(user_db,"INSERT INTO app_learning_events(learner_id,sentence_id,grammar_id,event_type,listening_score,comprehension_score,production_score) VALUES (?,?,?,?,?,?,?)",payload.learner_id,payload.sentence_id,grammar["grammar_id"],payload.event_type,payload.listening_score,payload.comprehension_score,payload.production_score)
    return {"ok":True,"due_at":due_text,"rating":rating.value,"rating_label":rating.name.lower(),"error_type":error_type,"fsrs":{"state":card.state.name.lower(),"stability":card.stability,"difficulty":card.difficulty}}


@app.get("/api/review/next")
async def next_review(request: Request, learner_id: int=1, mode: Literal["listening","recall","grammar"]="listening", level: str|None=None) -> dict[str,Any]:
    if level is not None and level not in LEVELS: raise HTTPException(400,"不支持的等级")
    content_db,user_db=envs(request); preferred={"listening":"listening","recall":"production","grammar":"comprehension"}[mode]; due_states=await rows(user_db,"SELECT sentence_id FROM learner_sentence_state WHERE learner_id=? AND due_at<=CURRENT_TIMESTAMP ORDER BY CASE WHEN last_error_type=? THEN 0 ELSE 1 END,MIN(listening_score,comprehension_score,production_score),due_at LIMIT 40",learner_id,preferred); due=None
    for state in due_states:
        item=await one(content_db,"SELECT id,kind,parent_sentence_id,level_code FROM sentences WHERE id=?",state["sentence_id"])
        if item and (level is None or item["level_code"]==level): due=item; break
    if due:
        root=due["id"] if due["kind"]=="core" else due["parent_sentence_id"]; child=await one(content_db,"SELECT id FROM sentences WHERE parent_sentence_id=? ORDER BY RANDOM() LIMIT 1",root); chosen=child["id"] if child else due["id"]; review_id=due["id"]; reason=f"FSRS已到期；优先针对{preferred}薄弱项，并换用同句式的新表达。"
    else:
        current_level=level or await scalar(user_db,"SELECT current_level_code FROM learner_profiles WHERE id=?",learner_id,default="N5"); fallback=await one(content_db,"SELECT id FROM sentences WHERE kind='core' AND level_code=? ORDER BY RANDOM() LIMIT 1",current_level)
        if not fallback: raise HTTPException(404,"当前等级没有可复习内容")
        chosen=review_id=fallback["id"]; reason="当前没有到期卡片，先提供当前等级中的随机核心句。"
    result=await sentence_detail(content_db,user_db,chosen,learner_id); result.update({"review_mode":mode,"review_card_id":review_id,"review_reason_zh":reason}); return result


@app.get("/api/records")
async def learning_records(
    request: Request, learner_id: int = 1, level: str | None = None
) -> dict[str, Any]:
    if level and level not in LEVELS:
        raise HTTPException(400, "不支持的等级")
    content_db, user_db = envs(request)
    exposures = await rows(
        user_db,
        "SELECT sentence_id,shown_count,completed_count,last_shown_at,last_completed_at "
        "FROM learner_sentence_exposure WHERE learner_id=? ORDER BY last_shown_at DESC",
        learner_id,
    )
    units = await rows(
        user_db,
        "SELECT root_sentence_id,shown_count,completed_sentences,mastery_percent,last_activity_at "
        "FROM learner_unit_state WHERE learner_id=? ORDER BY last_activity_at DESC LIMIT 200",
        learner_id,
    )
    sentence_ids = list(dict.fromkeys(
        [item["sentence_id"] for item in exposures]
        + [item["root_sentence_id"] for item in units]
    ))
    metadata: dict[str, dict[str, Any]] = {}
    for start in range(0, len(sentence_ids), 80):
        batch = sentence_ids[start : start + 80]
        marks = ",".join("?" for _ in batch)
        if not batch:
            continue
        found = await rows(
            content_db,
            f"""SELECT s.id,s.level_code,s.text,s.translation_zh,gp.title_zh,gp.pattern,
                       (SELECT COUNT(*) FROM sentences child WHERE child.parent_sentence_id=s.id)+1 AS sentence_count
                FROM sentences s
                LEFT JOIN sentence_grammar sg ON sg.sentence_id=s.id AND sg.role='new'
                LEFT JOIN grammar_points gp ON gp.id=sg.grammar_id
                WHERE s.id IN ({marks})""",
            *batch,
        )
        metadata.update({item["id"]: item for item in found})
    filtered_exposures = [
        item for item in exposures
        if item["sentence_id"] in metadata
        and (not level or metadata[item["sentence_id"]]["level_code"] == level)
    ]
    filtered_units = [
        item for item in units
        if item["root_sentence_id"] in metadata
        and (not level or metadata[item["root_sentence_id"]]["level_code"] == level)
    ]
    unit_items = []
    for item in filtered_units[:120]:
        merged = {**metadata[item["root_sentence_id"]], **item}
        unit_items.append(merged)
    recent_items = []
    for item in filtered_exposures[:30]:
        recent_items.append({**metadata[item["sentence_id"]], **item})
    average = (
        sum(float(item["mastery_percent"] or 0) for item in filtered_units)
        / len(filtered_units)
        if filtered_units else 0
    )
    return {
        "summary": {
            "viewed_sentences": len(filtered_exposures),
            "total_showings": sum(int(item["shown_count"] or 0) for item in filtered_exposures),
            "total_completions": sum(int(item["completed_count"] or 0) for item in filtered_exposures),
            "started_units": len(filtered_units),
            "mastered_units": sum(1 for item in filtered_units if float(item["mastery_percent"] or 0) >= 80),
            "average_mastery": round(average, 1),
        },
        "units": unit_items,
        "recent_sentences": recent_items,
        "level": level,
    }


@app.get("/api/grammars")
async def grammars(
    request: Request,
    level: str | None = None,
    q: str = "",
    learner_id: int = 1,
    limit: int = Query(60, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    if level and level not in LEVELS:
        raise HTTPException(400, "不支持的等级")
    content_db, user_db = envs(request)
    clauses = ["1=1"]
    params: list[Any] = []
    if level:
        clauses.append("gp.level_code=?")
        params.append(level)
    if q.strip():
        token = f"%{q.strip()}%"
        clauses.append("(gp.title_zh LIKE ? OR gp.pattern LIKE ? OR gp.meaning_zh LIKE ?)")
        params.extend([token, token, token])
    items = await rows(
        content_db,
        f"""SELECT gp.*,COUNT(DISTINCT sg.sentence_id) AS sentence_count
            FROM grammar_points gp LEFT JOIN sentence_grammar sg ON sg.grammar_id=gp.id
            WHERE {' AND '.join(clauses)} GROUP BY gp.id
            ORDER BY CASE gp.level_code WHEN 'N5' THEN 1 WHEN 'N4' THEN 2 WHEN 'N3' THEN 3 WHEN 'N2' THEN 4 ELSE 5 END,gp.id
            LIMIT ? OFFSET ?""",
        *params, limit, offset,
    )
    states = await rows(
        user_db,
        "SELECT grammar_id,recognition_score,production_score,review_count FROM learner_grammar_state WHERE learner_id=?",
        learner_id,
    )
    mapping = {item["grammar_id"]: item for item in states}
    for item in items:
        state = mapping.get(item["id"], {})
        item.update({
            "recognition_score": state.get("recognition_score", 0),
            "production_score": state.get("production_score", 0),
            "review_count": state.get("review_count", 0),
        })
    return {"items": items, "limit": limit, "offset": offset}


@app.get("/api/grammars/{grammar_id}")
async def grammar_detail(
    grammar_id: str, request: Request, learner_id: int = 1
) -> dict[str, Any]:
    content_db, user_db = envs(request)
    grammar = await one(content_db, "SELECT * FROM grammar_points WHERE id=?", grammar_id)
    if grammar is None:
        raise HTTPException(404, "语法不存在")
    state = await one(
        user_db,
        "SELECT recognition_score,production_score,review_count FROM learner_grammar_state WHERE learner_id=? AND grammar_id=?",
        learner_id, grammar_id,
    ) or {}
    grammar.update({
        "recognition_score": state.get("recognition_score", 0),
        "production_score": state.get("production_score", 0),
        "review_count": state.get("review_count", 0),
    })
    related = await rows(
        content_db,
        """SELECT s.id,s.kind,s.text,s.reading,s.translation_zh,s.status
           FROM sentence_grammar sg JOIN sentences s ON s.id=sg.sentence_id
           WHERE sg.grammar_id=? ORDER BY CASE s.kind WHEN 'core' THEN 1 WHEN 'variant' THEN 2 WHEN 'real' THEN 3 ELSE 4 END,s.id LIMIT 24""",
        grammar_id,
    )
    grammar["sentences"] = await sentence_previews(content_db, related)
    return grammar


@app.get("/api/library")
async def library(
    request: Request,
    level: str | None = None,
    kind: str | None = None,
    grammar_id: str | None = None,
    q: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=60),
) -> dict[str, Any]:
    if level and level not in LEVELS:
        raise HTTPException(400, "不支持的等级")
    if kind and kind not in KINDS:
        raise HTTPException(400, "不支持的句子类型")
    content_db, _ = envs(request)
    clauses = ["1=1"]
    params: list[Any] = []
    if level:
        clauses.append("s.level_code=?"); params.append(level)
    if kind:
        clauses.append("s.kind=?"); params.append(kind)
    if grammar_id:
        clauses.append("sg.grammar_id=?"); params.append(grammar_id)
    if q.strip():
        token = f"%{q.strip()}%"
        clauses.append("(s.text LIKE ? OR s.reading LIKE ? OR s.translation_zh LIKE ?)")
        params.extend([token, token, token])
    where = " AND ".join(clauses)
    total = await scalar(
        content_db,
        f"SELECT COUNT(DISTINCT s.id) FROM sentences s LEFT JOIN sentence_grammar sg ON sg.sentence_id=s.id WHERE {where}",
        *params,
    )
    items = await rows(
        content_db,
        f"""SELECT s.id,s.level_code,s.kind,s.variant_type,s.text,s.reading,s.translation_zh,s.status,
                    sg.grammar_id,gp.title_zh AS grammar_title
             FROM sentences s LEFT JOIN sentence_grammar sg ON sg.sentence_id=s.id
             LEFT JOIN grammar_points gp ON gp.id=sg.grammar_id WHERE {where}
             GROUP BY s.id ORDER BY s.id LIMIT ? OFFSET ?""",
        *params, page_size, (page - 1) * page_size,
    )
    return {"items": await sentence_previews(content_db, items), "total": total, "page": page, "page_size": page_size}


@app.get("/api/words")
async def words(
    request: Request,
    q: str = "",
    common_only: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=60),
) -> dict[str, Any]:
    content_db, _ = envs(request)
    clauses: list[str] = []
    params: list[Any] = []
    if q.strip():
        token = f"{q.strip()}%"
        clauses.append("""l.id IN (SELECT lexeme_id FROM lexeme_forms WHERE written_form LIKE ? OR reading LIKE ? UNION SELECT lexeme_id FROM lexeme_glosses WHERE gloss LIKE ?)""")
        params.extend([token, token, token])
    if common_only:
        clauses.append("(l.common_rank IS NOT NULL OR EXISTS(SELECT 1 FROM lexeme_forms cf WHERE cf.lexeme_id=l.id AND cf.is_common=1))")
    where = " AND ".join(clauses) or "1=1"
    total = await scalar(content_db, f"SELECT COUNT(*) FROM lexemes l WHERE {where}", *params)
    items = await rows(
        content_db,
        f"""SELECT l.id,l.jmdict_seq,l.common_rank,ld.part_of_speech,
          (SELECT written_form FROM lexeme_forms f WHERE f.lexeme_id=l.id ORDER BY f.is_common DESC,f.id LIMIT 1) AS written_form,
          (SELECT reading FROM lexeme_forms f WHERE f.lexeme_id=l.id ORDER BY f.is_common DESC,f.id LIMIT 1) AS reading,
          (SELECT GROUP_CONCAT(gloss,'；') FROM (SELECT gloss FROM lexeme_glosses g WHERE g.lexeme_id=l.id AND g.language_code='zho' ORDER BY g.is_machine_generated,g.id LIMIT 3)) AS gloss_zh,
          (SELECT GROUP_CONCAT(gloss,'; ') FROM (SELECT gloss FROM lexeme_glosses g WHERE g.lexeme_id=l.id AND g.language_code='eng' ORDER BY g.id LIMIT 4)) AS gloss_en,
          ld.memory_note_zh,ld.memory_note_status
          FROM lexemes l LEFT JOIN lexeme_details ld ON ld.lexeme_id=l.id WHERE {where}
          ORDER BY CASE WHEN l.common_rank IS NULL THEN 1 ELSE 0 END,l.common_rank,l.id LIMIT ? OFFSET ?""",
        *params, page_size, (page - 1) * page_size,
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.get("/api/words/{lexeme_id}")
async def word_detail(
    lexeme_id: int, request: Request, learner_id: int = 1
) -> dict[str, Any]:
    content_db, user_db = envs(request)
    data = await one(
        content_db,
        "SELECT l.*,ld.part_of_speech,ld.usage_notes,ld.memory_note_zh,ld.memory_note_status FROM lexemes l LEFT JOIN lexeme_details ld ON ld.lexeme_id=l.id WHERE l.id=?",
        lexeme_id,
    )
    if data is None:
        raise HTTPException(404, "没有找到这个词条")
    data["saved"] = bool(await one(user_db, "SELECT 1 AS found FROM learner_saved_lexemes WHERE learner_id=? AND lexeme_id=?", learner_id, lexeme_id))
    data["forms"] = await rows(content_db, "SELECT id,written_form,reading,is_common FROM lexeme_forms WHERE lexeme_id=? ORDER BY is_common DESC,id LIMIT 40", lexeme_id)
    data["glosses"] = await rows(content_db, "SELECT language_code,gloss,source,is_machine_generated FROM lexeme_glosses WHERE lexeme_id=? ORDER BY language_code DESC,is_machine_generated,id LIMIT 80", lexeme_id)
    form_id = data["forms"][0]["id"] if data["forms"] else -1
    data["furigana_segments"] = await rows(content_db, "SELECT position,surface,reading FROM furigana_segments WHERE lexeme_form_id=? ORDER BY position", form_id)
    data["course_examples"] = await rows(content_db, """SELECT DISTINCT s.id,s.text,s.reading,s.translation_zh,s.level_code,s.kind FROM sentence_lexemes sl JOIN sentences s ON s.id=sl.sentence_id WHERE sl.lexeme_id=? ORDER BY CASE s.kind WHEN 'core' THEN 0 ELSE 1 END,s.id LIMIT 8""", lexeme_id)
    data["reading_examples"] = await rows(content_db, """SELECT DISTINCT rs.id,rs.text,rw.title,rw.author FROM reading_sentence_lexemes rsl JOIN reading_sentences rs ON rs.id=rsl.reading_sentence_id JOIN reading_works rw ON rw.id=rs.work_id WHERE rsl.lexeme_id=? ORDER BY rs.id LIMIT 8""", lexeme_id)
    written = data["forms"][0]["written_form"] if data["forms"] else ""
    data["kanji"] = []
    for literal in dict.fromkeys(char for char in written if "一" <= char <= "龯"):
        entry = await one(content_db, "SELECT * FROM kanji_entries WHERE literal=?", literal)
        if entry:
            entry["readings"] = await rows(content_db, "SELECT reading_type,reading FROM kanji_readings WHERE literal=? ORDER BY reading_type,reading", literal)
            data["kanji"].append(entry)
    return data


@app.post("/api/saved-words/toggle")
async def toggle_saved_word(payload: SavedLexemeRequest, request: Request) -> dict[str, Any]:
    content_db, user_db = envs(request)
    exists = await one(user_db, "SELECT 1 AS found FROM learner_saved_lexemes WHERE learner_id=? AND lexeme_id=?", payload.learner_id, payload.lexeme_id)
    if exists:
        await execute(user_db, "DELETE FROM learner_saved_lexemes WHERE learner_id=? AND lexeme_id=?", payload.learner_id, payload.lexeme_id)
        return {"ok": True, "saved": False}
    if not await one(content_db, "SELECT 1 AS found FROM lexemes WHERE id=?", payload.lexeme_id):
        raise HTTPException(404, "词条不存在")
    await execute(user_db, "INSERT INTO learner_saved_lexemes(learner_id,lexeme_id,source_sentence_id,source_type) VALUES (?,?,?,?)", payload.learner_id, payload.lexeme_id, payload.source_sentence_id, payload.source_type)
    return {"ok": True, "saved": True}


@app.get("/api/saved-words")
async def saved_words(
    request: Request, learner_id: int = 1, limit: int = Query(100, ge=1, le=500)
) -> dict[str, Any]:
    content_db, user_db = envs(request)
    saved = await rows(user_db, "SELECT lexeme_id,created_at,source_type FROM learner_saved_lexemes WHERE learner_id=? ORDER BY created_at DESC LIMIT ?", learner_id, limit)
    items = []
    for state in saved:
        item = await one(content_db, """SELECT l.id,(SELECT written_form FROM lexeme_forms f WHERE f.lexeme_id=l.id ORDER BY f.is_common DESC,f.id LIMIT 1) AS written_form,(SELECT reading FROM lexeme_forms f WHERE f.lexeme_id=l.id ORDER BY f.is_common DESC,f.id LIMIT 1) AS reading,(SELECT gloss FROM lexeme_glosses g WHERE g.lexeme_id=l.id AND g.language_code='zho' ORDER BY g.is_machine_generated,g.id LIMIT 1) AS gloss_zh FROM lexemes l WHERE l.id=?""", state["lexeme_id"])
        if item:
            items.append({**item, "created_at": state["created_at"], "source_type": state["source_type"]})
    return {"items": items, "total": len(items)}


@app.get("/api/readings")
async def readings(
    request: Request,
    work_id: str | None = None,
    q: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=60),
    learner_id: int = 1,
) -> dict[str, Any]:
    content_db, user_db = envs(request)
    work_items = await rows(
        content_db,
        """SELECT rw.*,
           (SELECT COUNT(*) FROM reading_sentences rs WHERE rs.work_id=rw.id) AS sentence_count,
           (SELECT COUNT(*) FROM reading_sections sec WHERE sec.work_id=rw.id) AS section_count,
           (SELECT id FROM reading_sentences rs WHERE rs.work_id=rw.id ORDER BY position LIMIT 1) AS first_sentence_id
           FROM reading_works rw ORDER BY rw.id""",
    )
    reading_states = await rows(user_db, "SELECT work_id,last_sentence_id,progress_percent,updated_at FROM learner_reading_state WHERE learner_id=?", learner_id)
    state_map = {item["work_id"]: item for item in reading_states}
    bookmarks = await rows(user_db, "SELECT reading_sentence_id FROM reading_bookmarks WHERE learner_id=?", learner_id)
    bookmark_ids = {item["reading_sentence_id"] for item in bookmarks}
    bookmark_work_counts: dict[str, int] = {}
    if bookmark_ids:
        ids = list(bookmark_ids)
        for start in range(0, len(ids), 80):
            batch = ids[start : start + 80]
            marks = ",".join("?" for _ in batch)
            linked = await rows(content_db, f"SELECT work_id,COUNT(*) AS count FROM reading_sentences WHERE id IN ({marks}) GROUP BY work_id", *batch)
            for item in linked:
                bookmark_work_counts[item["work_id"]] = bookmark_work_counts.get(item["work_id"], 0) + int(item["count"])
    for work in work_items:
        state = state_map.get(work["id"], {})
        work.update({
            "progress_percent": state.get("progress_percent", 0),
            "last_sentence_id": state.get("last_sentence_id"),
            "updated_at": state.get("updated_at"),
            "bookmark_count": bookmark_work_counts.get(work["id"], 0),
        })
    clauses = ["1=1"]
    params: list[Any] = []
    if work_id:
        clauses.append("rs.work_id=?"); params.append(work_id)
    if q.strip():
        token = f"%{q.strip()}%"
        clauses.append("(rs.text LIKE ? OR rs.reading LIKE ? OR rw.title LIKE ? OR rw.author LIKE ?)")
        params.extend([token, token, token, token])
    where = " AND ".join(clauses)
    total = await scalar(content_db, f"SELECT COUNT(*) FROM reading_sentences rs JOIN reading_works rw ON rw.id=rs.work_id WHERE {where}", *params)
    items = await rows(
        content_db,
        f"""SELECT rs.id,rs.text,rs.reading,rs.translation_zh,rs.difficulty_score,rs.status,
                    rw.id AS work_id,rw.title,rw.author
             FROM reading_sentences rs JOIN reading_works rw ON rw.id=rs.work_id
             WHERE {where} ORDER BY rs.id LIMIT ? OFFSET ?""",
        *params, page_size, (page - 1) * page_size,
    )
    for item in items:
        item["bookmarked"] = item["id"] in bookmark_ids
    return {"items": items, "works": work_items, "total": total, "page": page, "page_size": page_size}


@app.get("/api/reading-sections")
async def reading_sections(
    work_id: str, request: Request, learner_id: int = 1
) -> dict[str, Any]:
    content_db, user_db = envs(request)
    work = await one(content_db, "SELECT * FROM reading_works WHERE id=?", work_id)
    if work is None:
        raise HTTPException(404, "没有找到这部作品")
    state = await one(user_db, "SELECT last_position,progress_percent FROM learner_reading_state WHERE learner_id=? AND work_id=?", learner_id, work_id) or {}
    work.update({"last_position": state.get("last_position", 0), "progress_percent": state.get("progress_percent", 0)})
    items = await rows(
        content_db,
        """SELECT sec.*,(SELECT text FROM reading_sentences rs WHERE rs.work_id=sec.work_id AND rs.position=sec.start_position) AS opening_text
           FROM reading_sections sec WHERE sec.work_id=? ORDER BY sec.section_number""",
        work_id,
    )
    return {"work": work, "items": items}


@app.get("/api/reading-sections/{section_id}")
async def reading_section_detail(
    section_id: str, request: Request, learner_id: int = 1
) -> dict[str, Any]:
    content_db, user_db = envs(request)
    section = await one(
        content_db,
        """SELECT sec.*,rw.title,rw.author,rw.guide_zh,rw.era_note_zh
           FROM reading_sections sec JOIN reading_works rw ON rw.id=sec.work_id WHERE sec.id=?""",
        section_id,
    )
    if section is None:
        raise HTTPException(404, "没有找到这个阅读单元")
    items = await rows(
        content_db,
        "SELECT id,position,text,reading,difficulty_score FROM reading_sentences WHERE work_id=? AND position BETWEEN ? AND ? ORDER BY position",
        section["work_id"], section["start_position"], section["end_position"],
    )
    bookmarks = await rows(user_db, "SELECT reading_sentence_id FROM reading_bookmarks WHERE learner_id=?", learner_id)
    saved = {item["reading_sentence_id"] for item in bookmarks}
    for item in items:
        item["bookmarked"] = item["id"] in saved
    return {"section": section, "items": items}


@app.get("/api/reading-bookmarks")
async def reading_bookmarks(
    request: Request, learner_id: int = 1, limit: int = Query(100, ge=1, le=500)
) -> dict[str, Any]:
    content_db, user_db = envs(request)
    saved = await rows(user_db, "SELECT reading_sentence_id,note_zh,created_at FROM reading_bookmarks WHERE learner_id=? ORDER BY created_at DESC LIMIT ?", learner_id, limit)
    items = []
    for state in saved:
        item = await one(content_db, """SELECT rs.id,rs.text,rs.position,rw.id AS work_id,rw.title,rw.author FROM reading_sentences rs JOIN reading_works rw ON rw.id=rs.work_id WHERE rs.id=?""", state["reading_sentence_id"])
        if item:
            items.append({**item, "note_zh": state["note_zh"], "created_at": state["created_at"]})
    return {"items": items, "total": len(items)}


@app.get("/api/readings/{sentence_id}")
async def reading_detail(
    sentence_id: str, request: Request, learner_id: int = 1
) -> dict[str, Any]:
    content_db, user_db = envs(request)
    data = await one(
        content_db,
        """SELECT rs.*,rw.title,rw.author,rw.source_name,rw.source_url,rw.source_file_url,
                  rw.copyright_status,rw.orthography,rw.guide_zh,rw.guide_status,rw.era_note_zh
           FROM reading_sentences rs JOIN reading_works rw ON rw.id=rs.work_id WHERE rs.id=?""",
        sentence_id,
    )
    if data is None:
        raise HTTPException(404, "没有找到这个阅读句")
    data["ruby_segments"] = await rows(content_db, "SELECT position,surface,reading FROM reading_sentence_ruby_segments WHERE reading_sentence_id=? ORDER BY position", sentence_id)
    data["tokens"] = await rows(
        content_db,
        """SELECT rsl.*,
          (SELECT GROUP_CONCAT(gloss,'；') FROM (SELECT gloss FROM lexeme_glosses g WHERE g.lexeme_id=rsl.lexeme_id AND g.language_code='zho' ORDER BY is_machine_generated,id LIMIT 3)) AS gloss_zh,
          (SELECT GROUP_CONCAT(gloss,'; ') FROM (SELECT gloss FROM lexeme_glosses g WHERE g.lexeme_id=rsl.lexeme_id AND g.language_code='eng' ORDER BY id LIMIT 3)) AS gloss_en
          FROM reading_sentence_lexemes rsl WHERE reading_sentence_id=? ORDER BY position""",
        sentence_id,
    )
    lexeme_ids = [item["lexeme_id"] for item in data["tokens"] if item.get("lexeme_id")]
    saved_lexemes: set[int] = set()
    if lexeme_ids:
        marks = ",".join("?" for _ in lexeme_ids)
        saved_rows = await rows(user_db, f"SELECT lexeme_id FROM learner_saved_lexemes WHERE learner_id=? AND lexeme_id IN ({marks})", learner_id, *lexeme_ids)
        saved_lexemes = {int(item["lexeme_id"]) for item in saved_rows}
    for token in data["tokens"]:
        token["particle_note_zh"] = PARTICLE_NOTES.get(token["surface"], "这是连接前后成分的助词；具体作用需要结合所在句型判断。") if token.get("part_of_speech") == "助詞" else None
        token["saved"] = bool(token.get("lexeme_id") in saved_lexemes)
    data["bookmarked"] = bool(await one(user_db, "SELECT 1 AS found FROM reading_bookmarks WHERE learner_id=? AND reading_sentence_id=?", learner_id, sentence_id))
    previous = await one(content_db, "SELECT id FROM reading_sentences WHERE work_id=? AND position<? ORDER BY position DESC LIMIT 1", data["work_id"], data["position"])
    following = await one(content_db, "SELECT id FROM reading_sentences WHERE work_id=? AND position>? ORDER BY position LIMIT 1", data["work_id"], data["position"])
    data["previous_id"] = previous["id"] if previous else None
    data["next_id"] = following["id"] if following else None
    data["section"] = await one(content_db, "SELECT id,title_zh,section_number,summary_zh FROM reading_sections WHERE work_id=? AND ? BETWEEN start_position AND end_position LIMIT 1", data["work_id"], data["position"])
    rules = await rows(content_db, "SELECT id,pattern,title_zh,explanation_zh,nuance_zh,status FROM literary_expression_rules ORDER BY length(pattern) DESC")
    data["literary_notes"] = [rule for rule in rules if rule["pattern"] and rule["pattern"] in data["text"]]
    return data


@app.post("/api/readings/progress")
async def save_reading_progress(payload: ReadingProgressRequest, request: Request) -> dict[str, Any]:
    content_db, user_db = envs(request)
    item = await one(content_db, """SELECT rs.id,rs.work_id,rs.position,(SELECT COUNT(*) FROM reading_sentences total WHERE total.work_id=rs.work_id) AS total FROM reading_sentences rs WHERE rs.id=?""", payload.sentence_id)
    if item is None:
        raise HTTPException(404, "阅读句不存在")
    percent = round(int(item["position"]) * 100 / max(1, int(item["total"])), 2)
    await execute(user_db, """INSERT INTO learner_reading_state(learner_id,work_id,last_sentence_id,last_position,progress_percent,updated_at) VALUES (?,?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(learner_id,work_id) DO UPDATE SET last_sentence_id=excluded.last_sentence_id,last_position=MAX(last_position,excluded.last_position),progress_percent=MAX(progress_percent,excluded.progress_percent),updated_at=CURRENT_TIMESTAMP""", payload.learner_id, item["work_id"], item["id"], item["position"], percent)
    await execute(user_db, "INSERT INTO reading_history(learner_id,reading_sentence_id) VALUES (?,?)", payload.learner_id, payload.sentence_id)
    return {"ok": True, "work_id": item["work_id"], "position": item["position"], "progress_percent": percent}


@app.post("/api/reading-bookmarks/toggle")
async def toggle_reading_bookmark(payload: BookmarkRequest, request: Request) -> dict[str, Any]:
    content_db, user_db = envs(request)
    exists = await one(user_db, "SELECT 1 AS found FROM reading_bookmarks WHERE learner_id=? AND reading_sentence_id=?", payload.learner_id, payload.sentence_id)
    if exists:
        await execute(user_db, "DELETE FROM reading_bookmarks WHERE learner_id=? AND reading_sentence_id=?", payload.learner_id, payload.sentence_id)
        return {"ok": True, "bookmarked": False}
    if not await one(content_db, "SELECT 1 AS found FROM reading_sentences WHERE id=?", payload.sentence_id):
        raise HTTPException(404, "阅读句不存在")
    await execute(user_db, "INSERT INTO reading_bookmarks(learner_id,reading_sentence_id,note_zh) VALUES (?,?,?)", payload.learner_id, payload.sentence_id, payload.note_zh)
    return {"ok": True, "bookmarked": True}


@app.post("/api/analyze")
async def analyze(payload: AnalyzeRequest, request: Request) -> dict[str, Any]:
    content_db, _ = envs(request)
    text = payload.text.strip()
    tokens: list[dict[str, Any]] = []
    position = 0
    cursor = 0
    punctuation = set("、。！？…・「」『』（）() \t\r\n")
    while cursor < len(text) and len(tokens) < 160:
        char = text[cursor]
        if char in punctuation:
            cursor += 1
            continue
        match = None
        upper = min(len(text), cursor + 12)
        for end in range(upper, cursor, -1):
            candidate = text[cursor:end]
            if any(mark in punctuation for mark in candidate):
                continue
            match = await one(
                content_db,
                """SELECT f.lexeme_id,f.written_form,f.reading,COALESCE(d.part_of_speech,'') AS part_of_speech
                   FROM lexeme_forms f LEFT JOIN lexeme_details d ON d.lexeme_id=f.lexeme_id
                   WHERE f.written_form=? OR f.reading=? ORDER BY f.is_common DESC,f.id LIMIT 1""",
                candidate, candidate,
            )
            if match:
                break
        surface = text[cursor:end] if match else char
        end_offset = end if match else cursor + 1
        lexeme_id = match["lexeme_id"] if match else None
        glosses = await rows(content_db, "SELECT language_code,gloss,source,is_machine_generated FROM lexeme_glosses WHERE lexeme_id=? ORDER BY CASE language_code WHEN 'zho' THEN 0 ELSE 1 END,is_machine_generated,id LIMIT 6", lexeme_id) if lexeme_id else []
        part = match["part_of_speech"] if match else ("助詞" if surface in PARTICLE_NOTES else "未识别")
        tokens.append({
            "position": position,
            "surface": surface,
            "dictionary_form": match["written_form"] if match else surface,
            "reading": match["reading"] if match else surface,
            "part_of_speech": part,
            "start_offset": cursor,
            "end_offset": end_offset,
            "lexeme_id": lexeme_id,
            "glosses": glosses,
            "particle_note_zh": PARTICLE_NOTES.get(surface) if part == "助詞" else None,
        })
        cursor = end_offset
        position += 1
    clause_surfaces = [part.strip() for part in text.replace("！", "。").replace("？", "。").split("。") if part.strip()]
    clauses = [{"surface": part, "role_zh": "句子或分句", "connector_zh": "根据句末和助词判断关系"} for part in clause_surfaces]
    return {
        "text": text,
        "direction": "ja-structure",
        "tokens": tokens,
        "structure_summary_zh": "按词典词形与助词拆分；先确认谓语，再沿助词判断各成分作用。",
        "clauses": clauses or [{"surface": text, "role_zh": "句子", "connector_zh": ""}],
        "literal_order_zh": "保持日语原顺序阅读，句末谓语通常决定整句核心意思。",
        "ambiguity_notes": ["自动拆词可能存在复合词和活用形歧义，请结合上下文与词典详情确认。"],
    }


@app.post("/api/reset")
async def reset_progress(payload: ResetRequest, request: Request) -> dict[str, Any]:
    if payload.confirmation != "RESET":
        raise HTTPException(400, "需要明确确认重置")
    _, user_db = envs(request)
    tables = (
        "app_learning_events", "learner_sentence_state", "learner_grammar_state",
        "learner_lexeme_state", "app_review_logs", "learner_reading_state",
        "reading_history", "reading_bookmarks", "learner_saved_lexemes",
        "learner_sentence_exposure", "learner_unit_state",
    )
    for table in tables:
        await execute(user_db, f"DELETE FROM {table} WHERE learner_id=?", payload.learner_id)
    return {"ok": True}


@app.post("/api/profile/level")
async def set_level(payload: LevelRequest, request: Request) -> dict[str,Any]:
    _,user_db=envs(request); await execute(user_db,"UPDATE learner_profiles SET current_level_code=? WHERE id=?",payload.level,payload.learner_id); return {"ok":True,"level":payload.level}


@app.get("/api/profile/retention")
async def get_retention(request: Request, learner_id: int=1) -> dict[str,Any]:
    _,user_db=envs(request); return {"desired_retention":float(await scalar(user_db,"SELECT desired_retention FROM learner_preferences WHERE learner_id=?",learner_id,default=0.9))}


@app.post("/api/profile/retention")
async def set_retention(payload: RetentionRequest, request: Request) -> dict[str,Any]:
    _,user_db=envs(request); await execute(user_db,"INSERT INTO learner_preferences(learner_id,desired_retention) VALUES (?,?) ON CONFLICT(learner_id) DO UPDATE SET desired_retention=excluded.desired_retention",payload.learner_id,payload.desired_retention); return {"ok":True,"desired_retention":payload.desired_retention}


@app.get("/{path:path}")
async def frontend(path: str, request: Request) -> Response:
    env = request.scope["env"]
    asset_path = path or "index.html"
    # The local FastAPI app mounts files at /static/*, while a Workers static
    # asset binding exposes the configured directory at its root. Keep one HTML
    # build working in both environments by translating that prefix here.
    if asset_path.startswith("static/"):
        asset_path = asset_path.removeprefix("static/")
    response = await env.ASSETS.fetch(f"https://assets.local/{asset_path}")
    body = await response.bytes()
    return Response(content=body, status_code=response.status, headers=response.headers)
