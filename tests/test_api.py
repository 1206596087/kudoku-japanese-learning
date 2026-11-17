from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEST_DIR = Path(tempfile.mkdtemp(prefix="kudoku-tests-"))
TEST_DB = TEST_DIR / "test.sqlite3"
shutil.copy2(ROOT / "database" / "japanese_learning.sqlite3", TEST_DB)
with sqlite3.connect(TEST_DB) as _connection:
    _connection.execute("DELETE FROM app_learning_events")
    _connection.execute("DELETE FROM learner_sentence_state")
    _connection.execute("DELETE FROM learner_grammar_state")
    _connection.execute("DELETE FROM learner_lexeme_state")
    _connection.execute("UPDATE learner_profiles SET current_level_code='N5' WHERE id=1")
os.environ["NIHONGO_DB_PATH"] = str(TEST_DB)
os.environ["NIHONGO_AUTH_DISABLED"] = "1"

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402


def test_health_and_static_shell() -> None:
    with TestClient(app) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["total"] == 6120
        assert data["counts"] == {
            "core": 510,
            "personal": 1020,
            "real": 3060,
            "variant": 1530,
        }
        assert data["base_total"] == 6000
        assert data["base_counts"] == {
            "core": 500, "personal": 1000, "real": 3000, "variant": 1500,
        }
        assert data["dictionary_entries"] > 200_000
        assert data["reading_sentences"] == 14_000
        assert data["all_sentences"] == 20_120
        page = client.get("/")
        assert page.status_code == 200
        assert "句读" in page.text
        assert "<ruby>" not in page.text


def test_dashboard_and_full_sentence_detail() -> None:
    with TestClient(app) as client:
        dashboard = client.get("/api/dashboard").json()
        assert dashboard["profile"]["current_level_code"] in {"N5", "N4", "N3", "N2", "N1"}
        assert len(dashboard["levels"]) == 5

        response = client.get("/api/sentences/next", params={"level": "N5"})
        assert response.status_code == 200
        sentence = response.json()
        assert sentence["kind"] == "core"
        assert len(sentence["children"]["variant"]) == 3
        assert len(sentence["children"]["real"]) == 6
        assert len(sentence["children"]["personal"]) == 2
        assert sentence["ruby_segments"]
        assert sentence["chunks"]
        assert sentence["grammar_title"]
        assert sentence["tokens"]
        assert any(token["lexeme_id"] for token in sentence["tokens"])
        assert "<ruby>" not in sentence["text"]
        assert any(part["reading"] for part in sentence["ruby_segments"])


def test_progress_persists_and_review_uses_changed_sentence() -> None:
    with TestClient(app) as client:
        sentence = client.get("/api/sentences/next", params={"level": "N5"}).json()
        payload = {
            "learner_id": 1,
            "sentence_id": sentence["id"],
            "listening_score": 3,
            "comprehension_score": 4,
            "production_score": 2,
            "event_type": "learn",
        }
        saved = client.post("/api/progress", json=payload)
        assert saved.status_code == 200
        detail = client.get(f"/api/sentences/{sentence['id']}").json()
        assert detail["progress"]["review_count"] == 1
        assert detail["progress"]["comprehension_score"] == 4
        dashboard = client.get("/api/dashboard").json()
        assert dashboard["today_count"] >= 1

        with sqlite3.connect(TEST_DB) as connection:
            connection.execute(
                "UPDATE learner_sentence_state SET due_at = '2000-01-01 00:00:00' WHERE sentence_id = ?",
                (sentence["id"],),
            )
        review = client.get("/api/review/next", params={"level": "N5", "mode": "listening"})
        assert review.status_code == 200
        reviewed_sentence = review.json()
        assert reviewed_sentence["id"] != sentence["id"]
        assert reviewed_sentence["parent_sentence_id"] == sentence["id"]


def test_grammar_library_filters_and_n1_course() -> None:
    with TestClient(app) as client:
        grammar = client.get("/api/grammars", params={"level": "N5"})
        assert grammar.status_code == 200
        assert grammar.json()["items"]
        grammar_id = grammar.json()["items"][0]["id"]
        detail = client.get(f"/api/grammars/{grammar_id}")
        assert detail.status_code == 200
        assert detail.json()["sentences"]

        library = client.get("/api/library", params={"level": "N5", "kind": "core", "q": "私"})
        assert library.status_code == 200
        assert library.json()["total"] > 0
        assert all(item["level_code"] == "N5" and item["kind"] == "core" for item in library.json()["items"])

        n1 = client.get("/api/sentences/next", params={"level": "N1"})
        assert n1.status_code == 200
        assert n1.json()["level_code"] == "N1"
        assert n1.json()["kind"] == "core"
        assert len(n1.json()["children"]["variant"]) == 3
        assert len(n1.json()["children"]["real"]) == 6
        assert len(n1.json()["children"]["personal"]) == 2


def test_level_update_and_double_confirm_reset_api() -> None:
    with TestClient(app) as client:
        updated = client.post("/api/profile/level", json={"learner_id": 1, "level": "N4"})
        assert updated.status_code == 200
        assert client.get("/api/dashboard").json()["profile"]["current_level_code"] == "N4"

        rejected = client.post("/api/reset", json={"learner_id": 1, "confirmation": "no"})
        assert rejected.status_code == 400
        accepted = client.post("/api/reset", json={"learner_id": 1, "confirmation": "RESET"})
        assert accepted.status_code == 200
        dashboard = client.get("/api/dashboard").json()
        assert dashboard["today_count"] == 0
        assert dashboard["mastered_count"] == 0


def test_dictionary_reading_and_sentence_analysis() -> None:
    with TestClient(app) as client:
        words = client.get("/api/words", params={"q": "読む"})
        assert words.status_code == 200
        assert words.json()["total"] > 0
        word = words.json()["items"][0]
        detail = client.get(f"/api/words/{word['id']}")
        assert detail.status_code == 200
        assert detail.json()["forms"]
        assert any(item["language_code"] == "eng" for item in detail.json()["glosses"])

        readings = client.get("/api/readings", params={"page_size": 3})
        assert readings.status_code == 200
        assert readings.json()["total"] == 14_000
        assert len(readings.json()["works"]) == 4
        reading_id = readings.json()["items"][0]["id"]
        reading = client.get(f"/api/readings/{reading_id}")
        assert reading.status_code == 200
        assert reading.json()["source_name"] == "青空文庫"
        assert reading.json()["tokens"]

        analyzed = client.post("/api/analyze", json={"text": "私は本を読みます。"})
        assert analyzed.status_code == 200
        tokens = analyzed.json()["tokens"]
        assert any(token["surface"] == "は" and token["particle_note_zh"] for token in tokens)
        assert any(token["surface"] == "を" and token["particle_note_zh"] for token in tokens)


def test_chinese_dictionary_reading_workspace_and_fsrs_preferences() -> None:
    with TestClient(app) as client:
        words = client.get("/api/words", params={"q": "読む"}).json()
        assert words["items"]
        word_id = words["items"][0]["id"]
        detail = client.get(f"/api/words/{word_id}", params={"learner_id": 1}).json()
        assert any(item["language_code"] == "zho" for item in detail["glosses"])
        saved = client.post(
            "/api/saved-words/toggle",
            json={"learner_id": 1, "lexeme_id": word_id, "source_type": "dictionary"},
        )
        assert saved.status_code == 200 and saved.json()["saved"] is True
        assert client.get("/api/saved-words").json()["total"] >= 1

        readings = client.get("/api/readings", params={"learner_id": 1, "page_size": 2}).json()
        work = readings["works"][0]
        assert work["guide_zh"] and work["section_count"] > 0
        sections = client.get(
            "/api/reading-sections", params={"work_id": work["id"], "learner_id": 1}
        ).json()
        assert sections["items"]
        section = client.get(
            f"/api/reading-sections/{sections['items'][0]['id']}", params={"learner_id": 1}
        ).json()
        sentence_id = section["items"][0]["id"]
        progress = client.post(
            "/api/readings/progress", json={"learner_id": 1, "sentence_id": sentence_id}
        )
        assert progress.status_code == 200 and progress.json()["progress_percent"] > 0
        bookmarked = client.post(
            "/api/reading-bookmarks/toggle",
            json={"learner_id": 1, "sentence_id": sentence_id},
        )
        assert bookmarked.status_code == 200 and bookmarked.json()["bookmarked"] is True
        reading_detail = client.get(
            f"/api/readings/{sentence_id}", params={"learner_id": 1}
        ).json()
        assert reading_detail["bookmarked"] is True
        assert reading_detail["section"]

        retention = client.post(
            "/api/profile/retention", json={"learner_id": 1, "desired_retention": 0.95}
        )
        assert retention.status_code == 200
        assert client.get("/api/profile/retention").json()["desired_retention"] == 0.95

        analysis = client.post(
            "/api/analyze", json={"text": "雨が降ったので、予定を変更しました。"}
        ).json()
        assert len(analysis["clauses"]) >= 2
        assert analysis["literal_order_zh"]
        assert analysis["structure_summary_zh"]


def test_private_account_random_learning_and_unit_records() -> None:
    with TestClient(app) as client:
        status = client.get("/api/account/status").json()
        assert status["authenticated"] is True
        first = client.get(
            "/api/sentences/next", params={"level": "N5", "selection": "random"}
        ).json()
        second = client.get(
            "/api/sentences/next",
            params={"level": "N5", "selection": "random", "exclude": first["id"]},
        ).json()
        assert first["id"] != second["id"]
        assert first["selection_mode"] == "random"
        assert first["exposure"]["shown_count"] >= 1

        saved = client.post(
            "/api/progress",
            json={
                "learner_id": 1,
                "sentence_id": first["id"],
                "listening_score": 4,
                "comprehension_score": 4,
                "production_score": 3,
                "event_type": "learn",
                "review_mode": "lesson",
            },
        )
        assert saved.status_code == 200 and saved.json()["fsrs"]["stability"] is not None
        records = client.get("/api/records", params={"level": "N5"}).json()
        assert records["summary"]["viewed_sentences"] >= 2
        unit = next(item for item in records["units"] if item["id"] == first["id"])
        assert unit["shown_count"] >= 1
        assert unit["completed_sentences"] >= 1

