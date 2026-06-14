import os
from sqlalchemy import text
from app.database import engine

def upgrade_db():
    try:
        with engine.connect() as conn:
            # Check if column exists to avoid error
            result = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='notification_logs' AND column_name='is_read'"))
            if not result.fetchone():
                conn.execute(text("ALTER TABLE notification_logs ADD COLUMN is_read BOOLEAN DEFAULT FALSE"))
                conn.commit()
                print("Successfully added is_read column to notification_logs table.")
            else:
                print("Column is_read already exists.")
    except Exception as e:
        print(f"Error upgrading db: {e}")

if __name__ == '__main__':
    upgrade_db()
