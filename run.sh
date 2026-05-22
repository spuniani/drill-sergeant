#!/bin/bash
# run.sh — start the SAT Prep app
cd "$(dirname "$0")"

# Create venv on first run
if [ ! -d "venv" ]; then
  echo "Setting up environment (first run only)..."
  python3 -m venv venv
  source venv/bin/activate
  pip install flask --quiet
else
  source venv/bin/activate
fi

echo "Starting SAT Prep..."
echo "Open http://localhost:5000 in your browser"
echo "(Press Ctrl+C to stop)"
python3 app.py
