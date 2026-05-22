# SAT Prep — Build Notes

## File structure
```
Question Bank (Unformatted)/
├── sat_app/               ← Flask web app
│   ├── app.py             ← all routes
│   ├── db.py              ← DB helpers, query functions
│   ├── app.db             ← SQLite (questions + progress + modules)
│   ├── run.sh             ← launch script
│   ├── BUILD.md           ← this file
│   ├── templates/
│   │   ├── base.html
│   │   ├── setup.html     ← FRE (student name)
│   │   ├── home.html      ← dashboard
│   │   ├── drill_pick.html
│   │   ├── drill.html     ← question view (drill)
│   │   ├── drill_results.html
│   │   ├── module_pick.html
│   │   ├── module.html    ← question view (timed)
│   │   ├── module_results.html
│   │   └── progress.html
│   └── static/
│       └── style.css
├── question_bank/
│   ├── images/            ← 2820 PNGs ({qid}_q.png, {qid}_ak.png)
│   ├── questions.db       ← original (kept for reference)
│   └── metadata.json
└── Answer Keys/           ← source PDFs
```

## DB schema (app.db)

### questions (extended from original)
- question_id, section, domain, skill, difficulty, is_active
- answer TEXT          — correct answer (A/B/C/D or SPR value, comma-sep if multiple)
- question_type TEXT   — 'mcq' or 'spr'
- is_reserved INT      — 1 = in a timed module, excluded from drill pool
- reserved_for TEXT    — module_id it belongs to
- q_image, ak_image    — relative paths into question_bank/images/

### modules
- module_id, section, type (easy_medium|hard), questions_json, created_at
- 8 pre-built: math_em_01/02, math_hd_01/02, rw_em_01/02, rw_hd_01/02

### module_attempts
- attempt_id, module_id, started_at, completed_at
- time_limit_sec, time_used_sec
- answers_json {qid: answer}
- n_correct, n_total, domain_json, timed_attempt_no

### drill_sessions
- session_id, started_at, completed_at
- filters_json {section, domains, skills, difficulties}
- questions_json, answers_json

### question_history
- qid, session_id, session_type, answered_at, correct, time_sec
- indexed on qid, answered_at

### config
- key/value: student_name, setup_complete

## Key decisions
- Images served via Flask route /images/<filename> from question_bank/images/
- One DB file (app.db) for everything — copy of questions.db + new tables
- Time scaling: 1.30x (first 4 timed modules) → 1.15x (next 4) → 1.00x (thereafter)
- Recency window: 7 days (questions seen in last 7 days excluded from drill)
- Module question selection is seeded (random.seed(42)) — reproducible
- SPR answers: comma-separated accepted values, any match = correct
- "Still confused" flag re-queues question at end of current session only

## Module composition
Math (22 questions):
- Easy/Med: Alg L1×2, L2×5, L3×1 | AdvMath L1×2, L2×5, L3×1 | PSDA L1×1, L2×2 | Geo L1×1, L2×2
- Hard:     Alg L1×1, L2×4, L3×3 | AdvMath L1×1, L2×4, L3×3 | PSDA L2×2, L3×1 | Geo L2×2, L3×1

R&W (27 questions):
- Easy/Med: C&S L1×2, L2×5, L3×1 | I&I L1×2, L2×4, L3×1 | EoI L1×2, L2×3 | SEC L1×2, L2×4, L3×1
- Hard:     C&S L1×1, L2×4, L3×3 | I&I L1×1, L2×3, L3×3 | EoI L2×3, L3×2 | SEC L2×3, L3×4

## Data pipeline (run once, already done)
- outputs/build_qb.py  — scanned Q/AK PDFs, rendered 2820 PNGs, built questions.db
- outputs/pipeline.py  — extracted answers, created new tables, built 8 modules → app.db

## Deploying to Khushi's laptop (PyInstaller)

### Build (run on your Mac)
```
cd sat_app && ./build.sh          # ~60 s
```
Produces `dist/sat_prep` (binary) and `dist/SAT Prep.app` (launcher).

### Install layout
```
~/sat_prep/
├── SAT Prep.app      ← double-click to launch
├── sat_prep          ← Flask binary (must stay alongside .app)
├── question_bank/
│   └── images/       ← 2820 PNGs
└── app.db            ← auto-created on first run; never delete
```
Double-clicking `SAT Prep.app` opens a Terminal window running the app,
then auto-opens http://127.0.0.1:5000 in the browser.

### Updating
Replace `sat_prep` + `SAT Prep.app` from the new build.
`app.db` is untouched — all progress preserved.

### First-time macOS security prompt
Right-click → Open (not double-click) the first time. System Settings →
Privacy & Security → Open Anyway if macOS blocks it.

### Path layout (dev vs frozen)
| Resource       | Dev (run.sh)             | Frozen binary               |
|---------------|--------------------------|-----------------------------|
| templates/    | next to app.py           | bundled in sys._MEIPASS     |
| static/       | next to app.py           | bundled in sys._MEIPASS     |
| app.db        | next to app.py           | next to sat_prep binary     |
| question_bank/| ../question_bank/images/ | next to sat_prep binary     |

## Schema migrations

`app.db` on Khushi's laptop is **never overwritten by an app update** — it holds all her progress. Any schema change must be applied as an incremental migration.

### Rules
- `init_db()` in db.py handles new *tables* via `CREATE TABLE IF NOT EXISTS` — safe on every startup
- New *columns* on existing tables need `ALTER TABLE … ADD COLUMN`, wrapped in try/except (SQLite has no IF NOT EXISTS for ALTER)
- Add a `schema_version` integer to the `config` table when the first column migration ships; bump it with each subsequent change
- Every migration also ships as a standalone `migrations/NNN_description.py` script that can be run manually: `python3 migrations/001_add_notes.py`

### Pattern for a new column migration
```python
# migrations/001_add_question_notes.py
import sqlite3, sys
DB = sys.argv[1] if len(sys.argv) > 1 else "app.db"
con = sqlite3.connect(DB)
try:
    con.execute("ALTER TABLE question_history ADD COLUMN notes TEXT")
    con.commit()
    print("OK")
except sqlite3.OperationalError as e:
    print(f"Skipped (already applied?): {e}")
con.close()
```

## Next / V2
- Vocab mode (flashcards, separate table)
- Weekly planner integration (paste session summaries to Claude)
- Analytics charts on progress page
- Multi-student support via --db flag
