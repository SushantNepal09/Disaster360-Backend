#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

# --- 1. Initialize Tables ---
# This runs the create_all() logic inside main2.py so that any NEW tables (like notification_logs)
# are created before migrate_db.py tries to modify them.
echo "Initializing database tables..."
python -c "import app.main2"

# --- 2. Database Migrations ---
echo "Running custom database migrations..."
python migrate_db.py

echo "Running Alembic migrations..."
alembic upgrade head

# --- 3. Start Server ---
echo "Starting FastAPI server..."
uvicorn app.main2:app2 --host 0.0.0.0 --port ${PORT:-8000}
