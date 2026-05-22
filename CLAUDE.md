# SAT Prep Tool

Local Flask web app for SAT drill practice. Runs on `http://127.0.0.1:5000`.
Built for one student (Khushi), deployed as a native macOS `.app` bundle via PyInstaller.

## Repo vs disk layout

The **question bank** (1000+ PNG images) and **app.db** (student data) are NOT in the repo.
They live on disk next to the app binary. See BUILD.md for the full install layout:

```
sat_prep/
├── SAT Prep.app        ← built by build.sh, replace on updates
├── app.db              ← persists across updates, never overwrite
└── question_bank/
    └── images/         ← ~1000 PNGs, generated once from College Board PDFs
```

## Stack

- **Flask 3 + Jinja2** — all routes in `app.py`, templates in `templates/`
- **SQLite** via stdlib `sqlite3` — helpers in `db.py`
- **Single CSS file** — `static/style.css`
- **PyInstaller** — `build.sh` produces `dist/SAT Prep.app` (macOS .app bundle with visible Terminal)

## Key files

| File | Purpose |
|------|---------|
| `app.py` | All Flask routes |
| `db.py` | DB connection, queries, mastery inference |
| `build.sh` | PyInstaller build + .app bundle creation |
| `BUILD.md` | Full build, deploy, and migration docs |
| `tickets.csv` | Dev backlog (T01–T18) |

## Core concepts

**Self-check questions** — ~85 questions have no extractable answer (`answer=NULL`).
These show the answer key image for manual verification. `correct=None` in code (never scored).
Always use `is true` / `is false` / `is none` in Jinja2 (not `==` or `not`) for correct values.

**Mastery bands** — computed in `db.get_skill_mastery()`:
- Weighted accuracy: L1=1×, L2=2×, L3=3× (harder questions count more)
- Bands: `not_started` → `exploring` → `developing` → `proficient` → `mastered`
- 14-day recency decay: proficient/mastered → `review_due` if last_seen > 14 days

**DB migrations** — `app.db` persists through app updates. New tables use
`CREATE TABLE IF NOT EXISTS` in `init_db()`. New columns use `ALTER TABLE` in `try/except`.
See BUILD.md "Schema migrations" section.

**Path helpers** — `_bundle_resource()` for templates/static (frozen: `sys._MEIPASS`),
`_data_dir()` for app.db/question_bank (frozen: directory of `sys.executable`).

## Dev setup

```bash
cd sat_app
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py          # runs on :5000, auto-opens browser
```

Requires `app.db` and `question_bank/images/` to exist on disk (not in repo).
Copy from backup or Khushi's laptop.

## Build

```bash
bash build.sh
# produces dist/SAT Prep.app and dist/app.db (clean, data-wiped)
```

See BUILD.md for full instructions, update procedure, and macOS quarantine fix.

## Active tickets

See `tickets.csv` for the full backlog. High-priority open items:
- **T04** — Manual answer editor for ~85 self-check questions (`/admin/answers`)
- **T06** — Dashboard redesign: section→domain→skill hierarchy
- **T08** — One-click re-drill mistakes
- **T14** — Wrong-answer AK images inline on drill results page
- **T15** — Formal schema_version migration system
