import os
from sqlalchemy import text
from app.database import engine

def upgrade_db():
    try:
        with engine.connect() as conn:
            # 1. Add is_read to notification_logs
            result = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='notification_logs' AND column_name='is_read'"))
            if not result.fetchone():
                conn.execute(text("ALTER TABLE notification_logs ADD COLUMN is_read BOOLEAN DEFAULT FALSE"))
                print("Successfully added is_read column to notification_logs table.")
            else:
                print("Column is_read already exists.")
                
            # 2. Add in_progress_at and resolved_at to rescue_updates
            try:
                conn.execute(text("ALTER TABLE rescue_updates ADD COLUMN IF NOT EXISTS in_progress_at TIMESTAMP;"))
                print("Checked/Added in_progress_at column.")
            except Exception as e:
                print(f"in_progress_at check note: {e}")
                
            try:
                conn.execute(text("ALTER TABLE rescue_updates ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMP;"))
                print("Checked/Added resolved_at column.")
            except Exception as e:
                print(f"resolved_at check note: {e}")
                
            # 3. Update existing status values
            try:
                conn.execute(text("UPDATE rescue_updates SET status = 'acknowledged' WHERE status = 'Acknowledged';"))
                conn.execute(text("UPDATE rescue_updates SET status = 'acknowledged' WHERE status = 'Not Acknowledged';"))
                print("Updated existing status values.")
            except Exception as e:
                print(f"Error updating statuses: {e}")
                
            conn.commit()
    except Exception as e:
        print(f"Error upgrading db: {e}")

if __name__ == '__main__':
    upgrade_db()
