#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

# --- 1. Run DB tasks in background to avoid Render port scan timeout ---
# Render free tier databases can take up to 2 minutes to wake up.
# Running DB migrations synchronously blocks uvicorn from binding the port,
# causing the deploy to fail due to a port scan timeout.
(
  echo "Initializing database tables..."
  python -c "from app.database import engine, Base; from app.models import *; Base.metadata.create_all(bind=engine)"

  echo "Running custom database migrations..."
  python migrate_db.py

  echo "Running Alembic migrations..."
  alembic upgrade head
) &

# --- 2. Start Server ---
echo "Starting FastAPI server..."
exec uvicorn app.main2:app2 --host 0.0.0.0 --port ${PORT:-8000}
