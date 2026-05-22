# SAT Prep Tool — AGENTS.md

## Dev commands

```bash
python app.py                # dev server on :5000, auto-opens browser
bash build.sh                # PyInstaller → dist/SAT Prep.app + clean app.db
source venv/bin/activate     # venv at project root
```

**No tests exist.** No pytest, no test files.

## Architecture

- All Flask routes in `app.py`, all DB logic in `db.py`, single `static/style.css`.
- `_bundle_resource()` → templates/static from `sys._MEIPASS` (frozen) or CWD (dev).
- `_data_dir()` → writable data (app.db) from `sys.executable` dir (frozen) or CWD (dev).
- Question image dir: `../question_bank/images/` relative to app root (dev), or next to binary (frozen).

## DB schema

```sql
questions (question_id PK, section, domain, skill, difficulty 1-3, answer TEXT|null,
           question_type mcq|spr, q_image, ak_image, is_active, is_reserved)
modules (module_id PK, section, type easy_medium|hard, questions_json)
drill_sessions (session_id PK, filters_json, questions_json, answers_json DEFAULT '{}')
module_attempts (attempt_id PK, module_id, answers_json DEFAULT '{}', ...)
question_history (id PK, qid, session_id, session_type, correct INTEGER|null, time_sec)
config (key PK, value)       -- keys: student_name, setup_complete
```

## Critical conventions

- **`answer=NULL` questions** (~85 self-check): `correct=None` in Python, stored as SQL NULL. In Jinja2 always use `is true` / `is false` / `is none` — never `==` or `not`.
- **SPR answers**: comma-separated accepted values in `answer` column; any match = correct.
- **Mastery bands** in `db.get_skill_mastery()`: weighted accuracy (L1=1×, L2=2×, L3=3×), coverage-based thresholds, 14-day recency decay → `review_due`.
- **Drill recency filter**: excludes questions seen in last 7 days; falls back to no filter if pool empties.
- **Slow thresholds**: L1>120s, L2>180s, L3>210s.
- **Module time scaling**: 1.30× for attempts 0-3, 1.15× for attempts 4-7, 1.00× thereafter.
- **Module question selection** is reproducible (seeded, done in pipeline, not in app code).

## Key routes

| Path | Purpose |
|------|---------|
| `/setup` | First-run: enter student name |
| `/` | Dashboard: stat cards, skill mastery table |
| `/drill` | Drill picker with cascading section→domain→skill |
| `/drill/start` | POST — create drill session |
| `/drill/q` | Current drill question |
| `/drill/answer` | POST — grade and reveal |
| `/drill/next` | POST — advance or flag "still confused" |
| `/drill/results` | Session results + Claude-copy block |
| `/drill/review/<sid>/<idx>` | Full-screen answer key review with keyboard nav |
| `/module/*` | Timed module attempt flow (22 Math / 27 R&W Qs) |
| `/progress` | Accuracy trend, mastery table, session history |

## Open tickets (high priority backlog)

- **T04** — Manual answer editor (`/admin/answers`) for self-check questions
- **T06** — Dashboard redesign with section→domain→skill hierarchy
- **T08** — One-click re-drill mistakes
- **T14** — Wrong-answer AK images inline on drill results
- **T15** — Formal schema migration system

## DB migrations

`init_db()` creates tables with `CREATE TABLE IF NOT EXISTS`. Column additions use `ALTER TABLE` wrapped in `try/except`. Future migrations should track `schema_version` in the `config` table.

## Other quirks

- `app.secret_key = "sat-study-tool-2026"` (hardcoded).
- Keyboard shortcuts: A/B/C/D keys select MCQ, Enter submits, Escape closes lightbox, Arrow keys navigate review.
- Jinja filter `fromjson` registered for parsing JSON DB fields.
- DB uses WAL mode (`PRAGMA journal_mode=WAL`).
