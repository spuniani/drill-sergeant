"""Tests for drill skip / I don't know feature."""
import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import db as DB
import app as flask_app


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    """Flask test client with a temp SQLite DB seeded with two questions."""
    db_file = str(tmp_path / "test.db")
    monkeypatch.setattr(DB, "DB_PATH", db_file)

    flask_app.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    DB.init_db()
    DB.set_config("student_name", "Khushi")
    DB.set_config("setup_complete", "1")

    with DB.get_db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS questions (
                question_id   TEXT PRIMARY KEY,
                section       TEXT,
                domain        TEXT,
                skill         TEXT,
                difficulty    INTEGER,
                question_type TEXT,
                answer        TEXT,
                is_reserved   INTEGER DEFAULT 0,
                is_active     INTEGER DEFAULT 0
            )
        """)
        con.executemany(
            """INSERT INTO questions
               (question_id, section, domain, skill, difficulty, question_type,
                answer, is_reserved, is_active)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0)""",
            [
                ("q001", "Math", "Algebra", "Linear Equations", 1, "mcq", "A", ),
                ("q002", "Math", "Algebra", "Linear Equations", 1, "mcq", "B", ),
                ("q003", "Math", "Algebra", "Linear Equations", 2, "mcq", "C", ),
            ],
        )

    with flask_app.app.test_client() as c:
        yield c


def _seed_session(sid, qids):
    """Insert a drill session row and return the sid."""
    with DB.get_db() as con:
        con.execute(
            """INSERT INTO drill_sessions
               (session_id, started_at, filters_json, questions_json, answers_json)
               VALUES (?, datetime('now'), '{}', ?, '{}')""",
            (sid, json.dumps(qids)),
        )
    return sid


def _set_flask_session(client, sid, qids, idx=0):
    with client.session_transaction() as sess:
        sess["drill_sid"]  = sid
        sess["drill_qids"] = qids
        sess["drill_idx"]  = idx


# ── Unit: accuracy calculation logic ──────────────────────────────────────────

def test_skip_counts_as_wrong_in_accuracy():
    """Skipped answers (correct=False, skipped=True) are in the denominator."""
    answers = {
        "q1": {"correct": True,  "time_sec": 30.0},                          # correct
        "q2": {"correct": False, "time_sec": 40.0},                          # wrong
        "q3": {"correct": False, "time_sec": 25.0, "skipped": True},         # skipped
        "q4": {"correct": None,  "time_sec": 20.0},                          # self-check (excluded)
    }
    n_correct = sum(1 for v in answers.values() if v["correct"] is True)
    n_total   = sum(1 for v in answers.values() if v["correct"] is not None)
    n_skipped = sum(1 for v in answers.values() if v.get("skipped"))

    assert n_correct == 1
    assert n_total   == 3   # q1 + q2 + q3; q4 (None) excluded
    assert n_skipped == 1
    assert round(n_correct / n_total * 100) == 33


def test_skipped_separated_from_wrong():
    """wrong_qs and skipped_qs are computed from the same loop without overlap."""
    answers = {
        "q1": {"correct": False, "time_sec": 30.0, "answer": "B"},
        "q2": {"correct": False, "time_sec": 25.0, "answer": "",  "skipped": True},
    }
    wrong_qs   = [qid for qid, d in answers.items()
                  if d["correct"] is False and not d.get("skipped")]
    skipped_qs = [qid for qid, d in answers.items() if d.get("skipped")]

    assert wrong_qs   == ["q1"]
    assert skipped_qs == ["q2"]


# ── Integration: POST /drill/skip ─────────────────────────────────────────────

def test_skip_returns_200_with_skipped_badge(client):
    sid  = _seed_session("sid1", ["q001"])
    _set_flask_session(client, "sid1", ["q001"])

    rv = client.post("/drill/skip", data={"qid": "q001", "time_sec": "45"})

    assert rv.status_code == 200
    assert b"Skipped" in rv.data


def test_skip_records_answers_json(client):
    sid = _seed_session("sid2", ["q001"])
    _set_flask_session(client, "sid2", ["q001"])

    client.post("/drill/skip", data={"qid": "q001", "time_sec": "30"})

    with DB.get_db() as con:
        row = con.execute(
            "SELECT answers_json FROM drill_sessions WHERE session_id='sid2'"
        ).fetchone()
    answers = json.loads(row["answers_json"])

    assert answers["q001"]["skipped"] is True
    assert answers["q001"]["correct"] is False
    assert answers["q001"]["answer"]  == ""
    assert answers["q001"]["time_sec"] == 30.0


def test_skip_logs_as_incorrect_in_history(client):
    _seed_session("sid3", ["q001"])
    _set_flask_session(client, "sid3", ["q001"])

    client.post("/drill/skip", data={"qid": "q001", "time_sec": "20"})

    with DB.get_db() as con:
        row = con.execute(
            "SELECT correct FROM question_history WHERE qid='q001' AND session_id='sid3'"
        ).fetchone()
    assert row is not None
    assert row["correct"] == 0   # stored as 0 (wrong), counts against accuracy


# ── Integration: results page ─────────────────────────────────────────────────

def test_results_accuracy_includes_skip(client):
    """1 correct + 1 wrong + 1 skipped → accuracy = 1/3 = 33%."""
    answers = {
        "q001": {"answer": "A", "correct": True,  "time_sec": 30.0},
        "q002": {"answer": "X", "correct": False, "time_sec": 40.0},
        "q003": {"answer": "",  "correct": False, "time_sec": 25.0, "skipped": True},
    }
    with DB.get_db() as con:
        con.execute(
            """INSERT INTO drill_sessions
               (session_id, started_at, completed_at, filters_json, questions_json, answers_json)
               VALUES ('sid4', datetime('now'), datetime('now'), '{}', ?, ?)""",
            (json.dumps(["q001", "q002", "q003"]), json.dumps(answers)),
        )

    rv = client.get("/drill/results/sid4")
    assert rv.status_code == 200
    html = rv.data.decode()
    assert "33%" in html          # accuracy
    assert "1 / 3" in html        # correct / total


def test_results_skipped_section_shown(client):
    """Skipped questions appear in their own section, not in Incorrect."""
    answers = {
        "q001": {"answer": "",  "correct": False, "time_sec": 20.0, "skipped": True},
        "q002": {"answer": "X", "correct": False, "time_sec": 35.0},
    }
    with DB.get_db() as con:
        con.execute(
            """INSERT INTO drill_sessions
               (session_id, started_at, completed_at, filters_json, questions_json, answers_json)
               VALUES ('sid5', datetime('now'), datetime('now'), '{}', ?, ?)""",
            (json.dumps(["q001", "q002"]), json.dumps(answers)),
        )

    rv = client.get("/drill/results/sid5")
    html = rv.data.decode()

    # Both sections appear
    assert "Skipped"   in html
    assert "Incorrect" in html
    # q001 (skipped) appears only once — not doubled into wrong section
    assert html.count("q001") <= 1


def test_claude_summary_includes_skipped(client):
    """Claude copy block lists skipped questions and skip count in summary line."""
    answers = {
        "q001": {"answer": "",  "correct": False, "time_sec": 20.0, "skipped": True},
        "q002": {"answer": "B", "correct": True,  "time_sec": 30.0},
    }
    with DB.get_db() as con:
        con.execute(
            """INSERT INTO drill_sessions
               (session_id, started_at, completed_at, filters_json, questions_json, answers_json)
               VALUES ('sid6', datetime('now'), datetime('now'), '{}', ?, ?)""",
            (json.dumps(["q001", "q002"]), json.dumps(answers)),
        )

    rv = client.get("/drill/results/sid6")
    html = rv.data.decode()

    assert "1 skipped"       in html   # summary line
    assert "Skipped questions" in html  # section header in copy block
    assert "q001"            in html   # skipped qid listed
