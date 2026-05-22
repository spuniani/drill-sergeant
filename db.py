"""db.py — DB connection and helpers."""
import sqlite3, os, sys, json
from datetime import datetime, date as date_type, timedelta

def _data_dir():
    """Permanent writable directory for app.db.
    - Frozen: folder containing the sat_prep binary (the install folder)
    - Dev:    folder containing db.py (sat_app/)
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

DB_PATH = os.path.join(_data_dir(), "app.db")
IMG_DIR = os.path.join(_data_dir(), "question_bank", "images")

def get_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con

def init_db():
    """Create runtime tables if missing. Safe to call on every startup.
    questions/modules are pre-populated and not touched here."""
    with get_db() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS config (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS drill_sessions (
                session_id     TEXT PRIMARY KEY,
                started_at     TEXT,
                completed_at   TEXT,
                filters_json   TEXT,
                questions_json TEXT,
                answers_json   TEXT DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS question_history (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                qid          TEXT,
                session_id   TEXT,
                session_type TEXT,
                answered_at  TEXT,
                correct      INTEGER,
                time_sec     REAL
            );
            CREATE TABLE IF NOT EXISTS module_attempts (
                attempt_id       TEXT PRIMARY KEY,
                module_id        TEXT,
                started_at       TEXT,
                completed_at     TEXT,
                time_limit_sec   INTEGER,
                time_used_sec    INTEGER,
                answers_json     TEXT DEFAULT '{}',
                n_correct        INTEGER,
                n_total          INTEGER,
                domain_json      TEXT,
                timed_attempt_no INTEGER DEFAULT 0
            );
        """)

# ── config ────────────────────────────────────────────────────────────────────
def get_config(key, default=None):
    with get_db() as con:
        row = con.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

def set_config(key, value):
    with get_db() as con:
        con.execute("INSERT OR REPLACE INTO config (key,value) VALUES (?,?)", (key, str(value)))

def is_setup_complete():
    return get_config("setup_complete") == "1"

# ── question bank queries ─────────────────────────────────────────────────────
def get_sections():
    with get_db() as con:
        rows = con.execute("SELECT DISTINCT section FROM questions ORDER BY section").fetchall()
        return [r["section"] for r in rows]

def get_domains(section=None):
    with get_db() as con:
        if section:
            rows = con.execute(
                "SELECT DISTINCT domain FROM questions WHERE section=? ORDER BY domain", (section,)
            ).fetchall()
        else:
            rows = con.execute("SELECT DISTINCT domain FROM questions ORDER BY domain").fetchall()
        return [r["domain"] for r in rows]

def get_skills(section=None, domains=None):
    with get_db() as con:
        q = "SELECT DISTINCT skill FROM questions WHERE is_reserved=0 AND is_active=0"
        params = []
        if section:
            q += " AND section=?"; params.append(section)
        if domains:
            q += f" AND domain IN ({','.join('?'*len(domains))})"; params.extend(domains)
        q += " ORDER BY skill"
        return [r["skill"] for r in con.execute(q, params).fetchall()]

def get_drill_questions(section, domains, skills, difficulties, n, exclude_qids):
    """Return up to n question dicts, excluding recently seen."""
    with get_db() as con:
        params = [section]
        q = """
            SELECT q.*, COALESCE(MAX(h.answered_at), '1970-01-01') as last_seen
            FROM questions q
            LEFT JOIN question_history h ON h.qid = q.question_id
            WHERE q.section=? AND q.is_reserved=0 AND q.is_active=0
        """
        if domains:
            q += f" AND q.domain IN ({','.join('?'*len(domains))})"; params.extend(domains)
        if skills:
            q += f" AND q.skill IN ({','.join('?'*len(skills))})"; params.extend(skills)
        if difficulties:
            q += f" AND q.difficulty IN ({','.join('?'*len(difficulties))})"; params.extend(difficulties)
        if exclude_qids:
            q += f" AND q.question_id NOT IN ({','.join('?'*len(exclude_qids))})"; params.extend(exclude_qids)
        q += " GROUP BY q.question_id ORDER BY last_seen ASC, RANDOM() LIMIT ?"
        params.append(n)
        return [dict(r) for r in con.execute(q, params).fetchall()]

def get_question(qid):
    with get_db() as con:
        row = con.execute("SELECT * FROM questions WHERE question_id=?", (qid,)).fetchone()
        return dict(row) if row else None

# ── modules ───────────────────────────────────────────────────────────────────
def get_modules():
    with get_db() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM modules ORDER BY section, type, module_id"
        ).fetchall()]

def get_module(module_id):
    with get_db() as con:
        row = con.execute("SELECT * FROM modules WHERE module_id=?", (module_id,)).fetchone()
        if not row: return None
        m = dict(row)
        import json
        m["questions"] = json.loads(m["questions_json"])
        return m

# ── history ───────────────────────────────────────────────────────────────────
def log_answer(qid, session_id, session_type, correct, time_sec):
    # correct=None means self-check (no extractable answer); stored as NULL
    correct_val = None if correct is None else int(correct)
    with get_db() as con:
        con.execute("""
            INSERT INTO question_history (qid, session_id, session_type, answered_at, correct, time_sec)
            VALUES (?, ?, ?, datetime('now'), ?, ?)
        """, (qid, session_id, session_type, correct_val, time_sec))

def get_recent_qids(days=7):
    with get_db() as con:
        rows = con.execute("""
            SELECT DISTINCT qid FROM question_history
            WHERE answered_at >= datetime('now', ?)
        """, (f"-{days} days",)).fetchall()
        return {r["qid"] for r in rows}

def get_skill_stats():
    """Per skill/difficulty: total in bank, distinct attempted, accuracy, last seen."""
    with get_db() as con:
        return con.execute("""
            SELECT q.section, q.domain, q.skill, q.difficulty,
                   COUNT(DISTINCT q.question_id)                        as total_in_bank,
                   COUNT(DISTINCT h.qid)                                as attempted,
                   COUNT(h.id)                                          as attempts,
                   ROUND(AVG(CASE WHEN h.correct IS NOT NULL
                                  THEN h.correct END)*100, 1)           as accuracy,
                   MAX(h.answered_at)                                   as last_seen
            FROM questions q
            LEFT JOIN question_history h ON h.qid = q.question_id
            WHERE q.is_reserved=0 AND q.is_active=0
            GROUP BY q.section, q.domain, q.skill, q.difficulty
            ORDER BY q.section, q.domain, q.skill, q.difficulty
        """).fetchall()

# ── mastery inference ─────────────────────────────────────────────────────────
def get_skill_mastery():
    """
    Aggregate per (section, domain, skill) across all difficulties.
    Weighted accuracy: L1=1x, L2=2x, L3=3x (harder questions count more).
    Coverage: attempted / total_in_bank across all levels.
    Recency decay: last_seen > 14 days → review_due override if proficient/mastered.

    Mastery bands:
      not_started  — 0 attempts
      exploring    — < 20% coverage (too little data)
      developing   — weighted accuracy < 65%
      proficient   — coverage ≥ 40%, accuracy 65–84%
      mastered     — coverage ≥ 60%, accuracy ≥ 85%
      review_due   — was proficient/mastered, last seen > 14 days
    """
    rows = get_skill_stats()     # per (section, domain, skill, difficulty)

    # Group by skill
    from collections import defaultdict
    groups = defaultdict(lambda: {
        "total_in_bank": 0, "attempted": 0,
        "w_correct": 0.0, "w_total": 0.0,
        "last_seen": None,
        "section": "", "domain": "",
    })
    weights = {1: 1, 2: 2, 3: 3}

    for row in rows:
        key = (row["section"], row["domain"], row["skill"])
        g = groups[key]
        g["section"] = row["section"]
        g["domain"]  = row["domain"]
        g["total_in_bank"] += row["total_in_bank"]
        g["attempted"]     += row["attempted"]

        w = weights.get(row["difficulty"], 1)
        if row["accuracy"] is not None and row["attempted"] > 0:
            # accuracy is 0-100; estimate correct count
            correct_est = (row["accuracy"] / 100.0) * row["attempted"]
            g["w_correct"] += correct_est * w
            g["w_total"]   += row["attempted"] * w

        if row["last_seen"] and (g["last_seen"] is None or row["last_seen"] > g["last_seen"]):
            g["last_seen"] = row["last_seen"]

    today = datetime.utcnow().date()
    results = []

    for (section, domain, skill), g in groups.items():
        coverage_pct  = (g["attempted"] / g["total_in_bank"] * 100) if g["total_in_bank"] else 0
        w_acc         = (g["w_correct"] / g["w_total"] * 100) if g["w_total"] > 0 else None

        # Days since last practice
        days_since = None
        if g["last_seen"]:
            try:
                last = datetime.fromisoformat(g["last_seen"]).date()
                days_since = (today - last).days
            except Exception:
                pass

        # Band assignment
        if g["attempted"] == 0:
            band = "not_started"
        elif coverage_pct < 20:
            band = "exploring"
        elif w_acc is None or w_acc < 65:
            band = "developing"
        elif coverage_pct >= 60 and w_acc >= 85:
            band = "mastered"
        elif coverage_pct >= 40 and w_acc >= 65:
            band = "proficient"
        else:
            band = "developing"

        # Recency decay: proficient/mastered → review_due if stale
        if band in ("proficient", "mastered") and days_since is not None and days_since > 14:
            band = "review_due"

        results.append({
            "section":      section,
            "domain":       domain,
            "skill":        skill,
            "total_in_bank":g["total_in_bank"],
            "attempted":    g["attempted"],
            "coverage_pct": round(coverage_pct),
            "w_accuracy":   round(w_acc, 1) if w_acc is not None else None,
            "last_seen":    g["last_seen"],
            "days_since":   days_since,
            "band":         band,
        })

    results.sort(key=lambda r: (r["section"], r["domain"], r["skill"]))
    return results


def get_progress_stats():
    """Aggregate stats for the progress page header cards and accuracy trend."""
    with get_db() as con:
        # Total questions attempted (including repeats)
        total_q = con.execute(
            "SELECT COUNT(*) FROM question_history"
        ).fetchone()[0]

        # Distinct questions touched
        distinct_q = con.execute(
            "SELECT COUNT(DISTINCT qid) FROM question_history"
        ).fetchone()[0]

        # Total questions in bank
        bank_size = con.execute(
            "SELECT COUNT(*) FROM questions WHERE is_reserved=0 AND is_active=0"
        ).fetchone()[0]

        # Total practice time (sum of per-question timer)
        total_sec = con.execute(
            "SELECT COALESCE(SUM(time_sec), 0) FROM question_history"
        ).fetchone()[0]

        # Sessions completed this week (drills + modules)
        drills_week = con.execute("""
            SELECT COUNT(*) FROM drill_sessions
            WHERE completed_at >= datetime('now', '-7 days')
            AND completed_at IS NOT NULL
        """).fetchone()[0]

        modules_week = con.execute("""
            SELECT COUNT(*) FROM module_attempts
            WHERE completed_at >= datetime('now', '-7 days')
            AND completed_at IS NOT NULL
        """).fetchone()[0]

        # Accuracy trend: last 10 completed drill sessions (oldest → newest)
        trend_rows = con.execute("""
            SELECT ds.session_id,
                   SUBSTR(ds.completed_at, 1, 10)           AS day,
                   SUM(CASE WHEN h.correct=1 THEN 1 ELSE 0 END) AS n_correct,
                   SUM(CASE WHEN h.correct IS NOT NULL THEN 1 ELSE 0 END) AS n_scored
            FROM drill_sessions ds
            JOIN question_history h ON h.session_id = ds.session_id
            WHERE ds.completed_at IS NOT NULL
            GROUP BY ds.session_id
            ORDER BY ds.completed_at DESC
            LIMIT 10
        """).fetchall()
        trend = list(reversed([dict(r) for r in trend_rows]))  # oldest first

    # Format total time as "Xh Ym" or "Ym"
    total_sec = int(total_sec)
    hours, rem = divmod(total_sec, 3600)
    mins        = rem // 60
    if hours > 0:
        time_fmt = f"{hours}h {mins}m"
    elif mins > 0:
        time_fmt = f"{mins}m"
    else:
        time_fmt = "< 1m"

    return {
        "total_q":       total_q,
        "distinct_q":    distinct_q,
        "bank_size":     bank_size,
        "total_time":    time_fmt,
        "sessions_week": drills_week + modules_week,
        "trend":         trend,          # list of {day, n_correct, n_scored}
    }


# ── timed attempt count (drives time scaling) ─────────────────────────────────
def get_timed_attempt_count():
    with get_db() as con:
        row = con.execute(
            "SELECT COUNT(*) as n FROM module_attempts WHERE completed_at IS NOT NULL"
        ).fetchone()
        return row["n"]

def compute_time_limit(section, attempt_no):
    """
    attempt_no = completed timed modules so far (0-indexed for next one).
    1.30x for first 4, 1.15x for next 4, 1.00x thereafter.
    """
    base = 35 * 60 if section == "Math" else 32 * 60
    if attempt_no < 4:
        mult = 1.30
    elif attempt_no < 8:
        mult = 1.15
    else:
        mult = 1.00
    return int(base * mult)
