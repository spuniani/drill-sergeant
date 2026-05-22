#!/bin/bash
# build.sh — build SAT Prep for macOS
#
# Produces two files in dist/:
#   sat_prep          — the Flask binary (PyInstaller)
#   SAT Prep.app      — launcher: opens a Terminal window and runs sat_prep
#
# INSTALL LAYOUT (copy both into the same folder as question_bank/):
#   ~/sat_prep/
#   ├── SAT Prep.app      ← double-click to launch
#   ├── sat_prep          ← the actual binary (don't move this)
#   ├── question_bank/
#   │   └── images/
#   └── app.db            ← auto-created on first run, never delete
#
# UPDATING: rebuild, then replace sat_prep + SAT Prep.app.
#           app.db is untouched — all progress preserved.

set -e
cd "$(dirname "$0")"

# ── venv + deps ───────────────────────────────────────────────────────────────
if [ ! -d "venv" ]; then
  echo "Creating venv..."
  python3 -m venv venv
fi
source venv/bin/activate
pip install flask pyinstaller --quiet

# ── clean ─────────────────────────────────────────────────────────────────────
rm -rf build dist sat_prep.spec

# ── 1. Build the Flask binary ─────────────────────────────────────────────────
echo "Building sat_prep binary..."
pyinstaller \
  --onefile \
  --name sat_prep \
  --add-data "templates:templates" \
  --add-data "static:static" \
  --hidden-import flask \
  --hidden-import jinja2 \
  --hidden-import werkzeug \
  --hidden-import sqlite3 \
  --hidden-import uuid \
  --hidden-import json \
  app.py

# ── 2. Build the SAT Prep.app launcher ───────────────────────────────────────
echo "Building SAT Prep.app launcher..."
APP="dist/SAT Prep.app"
MACOS="$APP/Contents/MacOS"
mkdir -p "$MACOS"

# Shell script that opens a Terminal window running sat_prep
cat > "$MACOS/SAT Prep" << 'LAUNCHER'
#!/bin/bash
# Resolve the folder that contains SAT Prep.app
# __file__ is  .../SAT Prep.app/Contents/MacOS/SAT Prep
# We want      .../  (three levels up)
INSTALL_DIR="$(cd "$(dirname "$0")/../../.." && pwd)"

osascript - "$INSTALL_DIR" << 'EOF'
on run argv
    set installDir to item 1 of argv
    tell application "Terminal"
        activate
        do script "cd " & quoted form of installDir & " && ./sat_prep"
    end tell
end run
EOF
LAUNCHER
chmod +x "$MACOS/SAT Prep"

# Minimal Info.plist
cat > "$APP/Contents/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>  <string>SAT Prep</string>
  <key>CFBundleIdentifier</key> <string>com.satprep.launcher</string>
  <key>CFBundleName</key>       <string>SAT Prep</string>
  <key>CFBundleVersion</key>    <string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
</dict>
</plist>
PLIST

# ── 3. Create a clean app.db (questions + modules only, no student data) ──────
echo "Creating clean app.db..."
cp app.db dist/app.db
python3 - << 'EOF'
import sqlite3
con = sqlite3.connect("dist/app.db")
con.executescript("""
    DELETE FROM config;
    DELETE FROM drill_sessions;
    DELETE FROM question_history;
    DELETE FROM module_attempts;
    DELETE FROM sqlite_sequence;
    VACUUM;
""")
con.close()
print("  questions:", sqlite3.connect("dist/app.db").execute("SELECT COUNT(*) FROM questions").fetchone()[0])
print("  modules  :", sqlite3.connect("dist/app.db").execute("SELECT COUNT(*) FROM modules").fetchone()[0])
EOF

# ── 4. Strip quarantine flags (prevents macOS App Translocation) ──────────────
xattr -cr dist/

echo ""
echo "✓  dist/sat_prep"
echo "✓  dist/SAT Prep.app"
echo "✓  dist/app.db  (clean — questions + modules only)"
echo ""
echo "Copy all three into your install folder alongside question_bank/:"
echo "  ~/sat_prep/"
echo "  ├── SAT Prep.app"
echo "  ├── sat_prep"
echo "  ├── app.db"
echo "  └── question_bank/"
echo ""
echo "After copying, run once to clear macOS quarantine on the destination:"
echo "  xattr -cr ~/sat_prep/"
