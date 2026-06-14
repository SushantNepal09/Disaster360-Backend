#!/bin/bash

# --- Database Migrations ---
# This will safely run your custom migration script(s) before the server starts.
# Because you added "IF NOT EXISTS" logic to migrate_db.py, this is safe to run on every deploy.
echo "Running custom database migrations..."
python migrate_db.py

# Run official Alembic migrations (this handles columns, dropping tables, etc.)
echo "Running Alembic migrations..."
alembic upgrade head
# --- Start Server ---
# Start the FastAPI application on the port specified by Render (or 8000 locally)
echo "Starting FastAPI server..."
uvicorn app.main2:app2 --host 0.0.0.0 --port ${PORT:-8000}
