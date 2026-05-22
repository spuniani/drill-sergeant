"""app.py — SAT Study Tool (Flask)"""
import os, sys, json, uuid
from datetime import datetime
from flask import (Flask, render_template, request, redirect,
                   url_for, session, jsonify, send_from_directory, abort)
import db as DB

# ── Path helpers (work both in dev and as a PyInstaller binary) ────────────────
def _bundle_resource(relative):
    """Read-only bundled resources (templates, static).
    In frozen mode they are extracted to sys._MEIPASS."""
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, relative)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative)

def _data_dir():
    """Permanent writable directory for app.db.
    - Frozen: folder containing the sat_prep binary (= the install folder)
    - Dev:    folder containing app.py (sat_app/)
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

# Images: next to the binary in frozen mode; ../question_bank/ in dev
if getattr(sys, "frozen", False):
    IMG_DIR = os.path.join(_data_dir(), "question_bank", "images")
else:
    IMG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "question_bank", "images")

app = Flask(__name__,
            template_folder=_bundle_resource("templates"),
            static_folder=_bundle_resource("static"))
app.secret_key = "sat-study-tool-2026"

# Custom Jinja filters
app.jinja_env.filters["fromjson"] = json.loads

# Ensure runtime tables exist — safe on every startup, won't touch questions/modules
DB.init_db()

# ── image serving ─────────────────────────────────────────────────────────────
@app.route("/images/<path:filename>")
def serve_image(filename):
    return send_from_directory(IMG_DIR, filename)

# ── before every request: check setup ────────────────────────────────────────
@app.before_request
def check_setup():
    allowed = ("setup", "static", "serve_image")
    if request.endpoint in allowed: return
    if not DB.is_setup_complete():
        return redirect(url_for("setup"))

# ── First Run Experience ──────────────────────────────────────────────────────
@app.route("/setup", methods=["GET","POST"])
def setup():
    if request.method == "POST":
        name = request.form.get("name","").strip()
        if name:
            DB.set_config("student_name", name)
            DB.set_config("setup_complete", "1")
            return redirect(url_for("home"))
    return render_template("setup.html")

# ── Home / Dashboard ──────────────────────────────────────────────────────────
@app.route("/")
def home():
    name    = DB.get_config("student_name", "Student")
    stats   = DB.get_skill_stats()
    modules = DB.get_modules()

    # Summary cards
    with DB.get_db() as con:
        total_attempted = con.execute(
            "SELECT COUNT(DISTINCT qid) FROM question_history"
        ).fetchone()[0]
        total_correct = con.execute(
            "SELECT COUNT(*) FROM question_history WHERE correct=1"
        ).fetchone()[0]
        total_answers = con.execute(
            "SELECT COUNT(*) FROM question_history"
        ).fetchone()[0]
        sessions_done = con.execute(
            "SELECT COUNT(*) FROM drill_sessions WHERE completed_at IS NOT NULL"
        ).fetchone()[0]
        timed_done = con.execute(
            "SELECT COUNT(*) FROM module_attempts WHERE completed_at IS NOT NULL"
        ).fetchone()[0]

    accuracy = round(total_correct / total_answers * 100) if total_answers else 0

    return render_template("home.html",
        name=name,
        total_attempted=total_attempted,
        accuracy=accuracy,
        sessions_done=sessions_done,
        timed_done=timed_done,
        stats=stats,
        modules=modules,
    )

# ── Drill: picker ─────────────────────────────────────────────────────────────
@app.route("/drill")
def drill_pick():
    name    = DB.get_config("student_name", "Student")
    section = request.args.get("section", "")
    domains = request.args.getlist("domain")
    skills  = request.args.getlist("skill")

    sections     = DB.get_sections()
    all_domains  = DB.get_domains(section) if section else []
    all_skills   = DB.get_skills(section, domains) if domains else []

    return render_template("drill_pick.html",
        name=name,
        section=section, sections=sections,
        domains=domains, all_domains=all_domains,
        skills=skills,   all_skills=all_skills,
    )

# ── Drill: start session ──────────────────────────────────────────────────────
@app.route("/drill/start", methods=["POST"])
def drill_start():
    section     = request.form.get("section")
    domains     = request.form.getlist("domain")
    skills      = request.form.getlist("skill")
    difficulties= [int(x) for x in request.form.getlist("difficulty")]
    n           = int(request.form.get("n", 10))

    recent = DB.get_recent_qids(days=7)
    qs = DB.get_drill_questions(section, domains, skills, difficulties, n, recent)

    if not qs:
        # Fall back without recency filter
        qs = DB.get_drill_questions(section, domains, skills, difficulties, n, set())

    if not qs:
        return redirect(url_for("drill_pick") + "?error=no_questions")

    sid = str(uuid.uuid4())
    filters = {"section": section, "domains": domains,
               "skills": skills, "difficulties": difficulties}

    with DB.get_db() as con:
        con.execute("""
            INSERT INTO drill_sessions (session_id, started_at, filters_json, questions_json, answers_json)
            VALUES (?, datetime('now'), ?, ?, ?)
        """, (sid, json.dumps(filters), json.dumps([q["question_id"] for q in qs]), "{}"))

    session["drill_sid"]  = sid
    session["drill_qids"] = [q["question_id"] for q in qs]
    session["drill_idx"]  = 0
    return redirect(url_for("drill_question"))

# ── Drill: question view ──────────────────────────────────────────────────────
@app.route("/drill/q")
def drill_question():
    sid   = session.get("drill_sid")
    qids  = session.get("drill_qids", [])
    idx   = session.get("drill_idx", 0)

    if not sid or idx >= len(qids):
        return redirect(url_for("drill_results"))

    q = DB.get_question(qids[idx])
    if not q:
        return redirect(url_for("drill_results"))

    return render_template("drill.html",
        q=q,
        idx=idx,
        total=len(qids),
        sid=sid,
        show_answer=False,
    )

# ── Drill: submit answer ──────────────────────────────────────────────────────
@app.route("/drill/answer", methods=["POST"])
def drill_answer():
    sid      = session.get("drill_sid")
    qids     = session.get("drill_qids", [])
    idx      = session.get("drill_idx", 0)
    qid      = request.form.get("qid")
    given    = request.form.get("answer", "").strip().upper()
    time_sec = float(request.form.get("time_sec", 0))

    q = DB.get_question(qid)
    if not q:
        return redirect(url_for("drill_results"))

    correct_raw = (q["answer"] or "").strip().upper()
    if not correct_raw:
        # Answer not extractable — show self-check (AK image) and don't score
        correct = None
        valid   = []
    else:
        # For SPR: accept any of the comma-separated valid answers
        valid   = [v.strip().upper() for v in correct_raw.split(",")]
        correct = given in valid

    DB.log_answer(qid, sid, "drill", correct, time_sec)

    # Update answers_json in drill_sessions
    with DB.get_db() as con:
        row = con.execute(
            "SELECT answers_json FROM drill_sessions WHERE session_id=?", (sid,)
        ).fetchone()
        answers = json.loads(row["answers_json"]) if row else {}
        answers[qid] = {"answer": given, "correct": correct, "time_sec": time_sec}
        con.execute(
            "UPDATE drill_sessions SET answers_json=? WHERE session_id=?",
            (json.dumps(answers), sid)
        )

    return render_template("drill.html",
        q=q, idx=idx, total=len(qids), sid=sid,
        show_answer=True,
        given=given, correct=correct,
        valid_answers=valid,
    )

# ── Drill: next question ──────────────────────────────────────────────────────
@app.route("/drill/next", methods=["POST"])
def drill_next():
    flag = request.form.get("flag") == "1"
    idx  = session.get("drill_idx", 0)
    qids = session.get("drill_qids", [])
    qid  = qids[idx] if idx < len(qids) else None

    if flag and qid:
        # Re-queue this question at the end
        qids.append(qid)
        session["drill_qids"] = qids

    session["drill_idx"] = idx + 1

    if session["drill_idx"] >= len(session["drill_qids"]):
        sid = session.get("drill_sid")
        with DB.get_db() as con:
            con.execute(
                "UPDATE drill_sessions SET completed_at=datetime('now') WHERE session_id=?",
                (sid,)
            )
        return redirect(url_for("drill_results"))

    return redirect(url_for("drill_question"))

# ── Drill: results ────────────────────────────────────────────────────────────
@app.route("/drill/results")
@app.route("/drill/results/<string:from_sid>")
def drill_results(from_sid=None):
    sid  = from_sid or session.get("drill_sid")
    if not sid:
        return redirect(url_for("home"))

    with DB.get_db() as con:
        row = con.execute(
            "SELECT * FROM drill_sessions WHERE session_id=?", (sid,)
        ).fetchone()

    if not row:
        return redirect(url_for("home"))

    answers  = json.loads(row["answers_json"] or "{}")
    filters  = json.loads(row["filters_json"] or "{}")
    # Exclude self-check (correct=None) from scored totals
    n_correct = sum(1 for v in answers.values() if v["correct"] is True)
    n_total   = sum(1 for v in answers.values() if v["correct"] is not None)

    # Per-skill breakdown (scoreable questions only)
    skill_stats = {}
    for qid, data in answers.items():
        if data["correct"] is None: continue   # self-check — skip
        q = DB.get_question(qid)
        if not q: continue
        key = f"{q['domain']} L{q['difficulty']}"
        if key not in skill_stats:
            skill_stats[key] = {"correct": 0, "total": 0, "domain": q["domain"], "difficulty": q["difficulty"]}
        skill_stats[key]["total"] += 1
        if data["correct"]: skill_stats[key]["correct"] += 1

    # Slow questions (> 2× expected time per difficulty)
    slow_threshold = {1: 120, 2: 180, 3: 210}  # seconds
    slow_qs = []
    for qid, data in answers.items():
        q = DB.get_question(qid)
        if q and data["time_sec"] > slow_threshold.get(q["difficulty"], 180):
            slow_qs.append({"qid": qid, "skill": q["skill"], "difficulty": q["difficulty"],
                            "time_sec": data["time_sec"], "correct": data["correct"]})

    wrong_qs = []
    for qid, data in answers.items():
        if data["correct"] is False:   # only truly incorrect, not self-check
            q = DB.get_question(qid)
            if q:
                wrong_qs.append({"qid": qid, "skill": q["skill"],
                                 "difficulty": q["difficulty"], "answer": data["answer"]})

    # Rolling accuracy per domain (last 3 sessions)
    domain_rolling = {}
    with DB.get_db() as con:
        rows = con.execute("""
            SELECT q.domain, q.difficulty, h.correct
            FROM question_history h JOIN questions q ON q.question_id=h.qid
            WHERE h.session_id IN (
                SELECT session_id FROM drill_sessions
                ORDER BY started_at DESC LIMIT 3
            )
        """).fetchall()
    for r in rows:
        key = f"{r['domain']} L{r['difficulty']}"
        if key not in domain_rolling:
            domain_rolling[key] = {"correct": 0, "total": 0}
        domain_rolling[key]["total"] += 1
        if r["correct"]: domain_rolling[key]["correct"] += 1

    student_name = DB.get_config("student_name", "Student")

    return render_template("drill_results.html",
        sid=sid,
        filters=filters,
        n_correct=n_correct,
        n_total=n_total,
        skill_stats=skill_stats,
        slow_qs=slow_qs,
        wrong_qs=wrong_qs,
        domain_rolling=domain_rolling,
        student_name=student_name,
        date=datetime.now().strftime("%Y-%m-%d"),
        answers=answers,
    )

# ── Drill: review session (prev/next through AK images after completion) ─────
@app.route("/drill/review/<session_id>/<int:idx>")
def drill_review(session_id, idx):
    with DB.get_db() as con:
        row = con.execute(
            "SELECT * FROM drill_sessions WHERE session_id=?", (session_id,)
        ).fetchone()
    if not row:
        return redirect(url_for("home"))

    qids    = json.loads(row["questions_json"])
    answers = json.loads(row["answers_json"] or "{}")
    total   = len(qids)

    # Clamp index
    idx = max(0, min(idx, total - 1))
    qid = qids[idx]
    q   = DB.get_question(qid)

    ans_data = answers.get(qid, {})
    correct  = ans_data.get("correct")          # True / False / None
    given    = ans_data.get("answer", "—")

    correct_raw = (q["answer"] or "").strip().upper() if q else ""
    valid = [v.strip().upper() for v in correct_raw.split(",")] if correct_raw else []

    return render_template("drill_review.html",
        session_id=session_id,
        q=q, idx=idx, total=total,
        correct=correct, given=given, valid_answers=valid,
        prev_idx=idx - 1 if idx > 0 else None,
        next_idx=idx + 1 if idx < total - 1 else None,
    )

# ── Timed Module: picker ──────────────────────────────────────────────────────
@app.route("/module")
def module_pick():
    name    = DB.get_config("student_name", "Student")
    modules = DB.get_modules()
    attempt_no = DB.get_timed_attempt_count()

    module_list = []
    for m in modules:
        qs = json.loads(m["questions_json"])
        time_limit = DB.compute_time_limit(m["section"], attempt_no)
        label_type = "Easy / Medium" if m["type"] == "easy_medium" else "Hard"
        module_list.append({
            "module_id":  m["module_id"],
            "section":    m["section"],
            "type":       label_type,
            "n_questions":len(qs),
            "time_limit": time_limit,
            "time_display": f"{time_limit//60} min",
        })

    return render_template("module_pick.html", name=name, modules=module_list, attempt_no=attempt_no)

# ── Timed Module: start ───────────────────────────────────────────────────────
@app.route("/module/start", methods=["POST"])
def module_start():
    module_id  = request.form.get("module_id")
    module     = DB.get_module(module_id)
    if not module:
        return redirect(url_for("module_pick"))

    attempt_no = DB.get_timed_attempt_count()
    time_limit = DB.compute_time_limit(module["section"], attempt_no)
    attempt_id = str(uuid.uuid4())

    with DB.get_db() as con:
        con.execute("""
            INSERT INTO module_attempts
            (attempt_id, module_id, started_at, time_limit_sec, answers_json, timed_attempt_no)
            VALUES (?, ?, datetime('now'), ?, ?, ?)
        """, (attempt_id, module_id, time_limit, "{}", attempt_no))

    session["mod_attempt_id"] = attempt_id
    session["mod_module_id"]  = module_id
    session["mod_qids"]       = module["questions"]
    session["mod_idx"]        = 0
    session["mod_time_limit"] = time_limit
    return redirect(url_for("module_question"))

# ── Timed Module: question view ───────────────────────────────────────────────
@app.route("/module/q")
def module_question():
    attempt_id = session.get("mod_attempt_id")
    qids       = session.get("mod_qids", [])
    idx        = session.get("mod_idx", 0)
    time_limit = session.get("mod_time_limit", 2100)

    if not attempt_id or idx >= len(qids):
        return redirect(url_for("module_submit_get"))

    q = DB.get_question(qids[idx])
    return render_template("module.html",
        q=q, idx=idx, total=len(qids),
        attempt_id=attempt_id,
        time_limit=time_limit,
        qids_json=json.dumps(qids),
    )

# ── Timed Module: save single answer (AJAX) ───────────────────────────────────
@app.route("/module/save_answer", methods=["POST"])
def module_save_answer():
    attempt_id = session.get("mod_attempt_id")
    data       = request.get_json()
    qid        = data.get("qid")
    answer     = data.get("answer", "").strip().upper()
    idx        = data.get("idx", 0)

    session["mod_idx"] = idx

    with DB.get_db() as con:
        row = con.execute(
            "SELECT answers_json FROM module_attempts WHERE attempt_id=?", (attempt_id,)
        ).fetchone()
        answers = json.loads(row["answers_json"]) if row else {}
        answers[qid] = answer
        con.execute(
            "UPDATE module_attempts SET answers_json=? WHERE attempt_id=?",
            (json.dumps(answers), attempt_id)
        )
    return jsonify({"ok": True})

# ── Timed Module: submit ──────────────────────────────────────────────────────
@app.route("/module/submit", methods=["POST","GET"])
def module_submit_get():
    attempt_id = session.get("mod_attempt_id")
    if not attempt_id:
        return redirect(url_for("home"))

    time_used = int(request.form.get("time_used", 0)) if request.method == "POST" else 0

    with DB.get_db() as con:
        row = con.execute(
            "SELECT * FROM module_attempts WHERE attempt_id=?", (attempt_id,)
        ).fetchone()
        if not row:
            return redirect(url_for("home"))

        answers  = json.loads(row["answers_json"] or "{}")
        module   = DB.get_module(row["module_id"])
        qids     = module["questions"]

        n_correct = 0
        domain_scores = {}
        for qid in qids:
            q = DB.get_question(qid)
            if not q: continue
            given       = answers.get(qid, "").upper()
            correct_raw = (q["answer"] or "").strip().upper()
            if not correct_raw:
                # No extractable answer — self-check; exclude from score
                correct = None
            else:
                valid   = [v.strip().upper() for v in correct_raw.split(",")]
                correct = given in valid

            if correct: n_correct += 1
            dom = q["domain"]
            if dom not in domain_scores:
                domain_scores[dom] = {"correct": 0, "total": 0}
            if correct is not None:          # only count scoreable questions
                domain_scores[dom]["total"] += 1
                if correct: domain_scores[dom]["correct"] += 1

            DB.log_answer(qid, attempt_id, "module", correct, 0)

        con.execute("""
            UPDATE module_attempts
            SET completed_at=datetime('now'), time_used_sec=?,
                n_correct=?, n_total=?, domain_json=?
            WHERE attempt_id=?
        """, (time_used, n_correct, len(qids), json.dumps(domain_scores), attempt_id))

    session.pop("mod_attempt_id", None)
    return redirect(url_for("module_results", attempt_id=attempt_id))

# ── Timed Module: results ─────────────────────────────────────────────────────
@app.route("/module/results/<attempt_id>")
def module_results(attempt_id):
    with DB.get_db() as con:
        row = con.execute(
            "SELECT * FROM module_attempts WHERE attempt_id=?", (attempt_id,)
        ).fetchone()
    if not row:
        return redirect(url_for("home"))

    module       = DB.get_module(row["module_id"])
    answers      = json.loads(row["answers_json"] or "{}")
    domain_scores= json.loads(row["domain_json"]  or "{}")
    qids         = module["questions"]

    question_review = []
    for qid in qids:
        q     = DB.get_question(qid)
        given = answers.get(qid, "—")
        correct_raw = (q["answer"] or "").strip().upper() if q else ""
        if not correct_raw:
            correct = None   # self-check — no extractable answer
            valid   = []
        else:
            valid   = [v.strip().upper() for v in correct_raw.split(",")]
            correct = given.upper() in valid
        question_review.append({
            "qid": qid, "given": given, "correct": correct,
            "valid": valid, "q": q,
        })

    student_name = DB.get_config("student_name", "Student")
    time_limit   = row["time_limit_sec"]
    time_used    = row["time_used_sec"] or 0

    return render_template("module_results.html",
        attempt_id=attempt_id,
        module=module,
        n_correct=row["n_correct"],
        n_total=row["n_total"],
        domain_scores=domain_scores,
        question_review=question_review,
        student_name=student_name,
        date=datetime.now().strftime("%Y-%m-%d"),
        time_limit=time_limit,
        time_used=time_used,
    )

# ── Progress dashboard ────────────────────────────────────────────────────────
@app.route("/progress")
def progress():
    name    = DB.get_config("student_name", "Student")
    mastery = DB.get_skill_mastery()
    pstats  = DB.get_progress_stats()

    with DB.get_db() as con:
        recent_drills = con.execute("""
            SELECT * FROM drill_sessions
            WHERE completed_at IS NOT NULL
            ORDER BY completed_at DESC LIMIT 10
        """).fetchall()
        recent_modules = con.execute("""
            SELECT ma.*, m.section, m.type
            FROM module_attempts ma JOIN modules m ON m.module_id=ma.module_id
            WHERE ma.completed_at IS NOT NULL
            ORDER BY ma.completed_at DESC LIMIT 10
        """).fetchall()

    return render_template("progress.html",
        name=name,
        mastery=mastery,
        pstats=pstats,
        recent_drills=recent_drills,
        recent_modules=recent_modules,
    )

# ── API: get domains for section (for drill picker AJAX) ──────────────────────
@app.route("/api/domains")
def api_domains():
    section = request.args.get("section","")
    return jsonify(DB.get_domains(section))

@app.route("/api/skills")
def api_skills():
    section = request.args.get("section","")
    domains = request.args.getlist("domain")
    return jsonify(DB.get_skills(section, domains))

if __name__ == "__main__":
    import threading, webbrowser, time

    frozen = getattr(sys, "frozen", False)

    # Auto-open browser after Flask binds
    def _open_browser():
        time.sleep(1.5)
        webbrowser.open("http://127.0.0.1:5000")

    threading.Thread(target=_open_browser, daemon=True).start()

    # debug=True in dev so tracebacks appear in browser; off when frozen
    app.run(debug=not frozen, port=5000, use_reloader=False)
