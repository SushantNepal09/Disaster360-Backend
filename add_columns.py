import sys
from sqlalchemy import create_engine, text
from app.database import DATABASE_URL

def migrate():
    engine = create_engine(DATABASE_URL)
    with engine.connect() as conn:
        print("Adding in_progress_at and resolved_at to rescue_updates table...")
        
        # We wrap in try/except in case columns already exist
        try:
            conn.execute(text("ALTER TABLE rescue_updates ADD COLUMN in_progress_at TIMESTAMP;"))
            print("Added in_progress_at column.")
        except Exception as e:
            print(f"in_progress_at might already exist: {e}")
            
        try:
            conn.execute(text("ALTER TABLE rescue_updates ADD COLUMN resolved_at TIMESTAMP;"))
            print("Added resolved_at column.")
        except Exception as e:
            print(f"resolved_at might already exist: {e}")
            
        try:
            # Update any existing records
            conn.execute(text("UPDATE rescue_updates SET status = 'acknowledged' WHERE status = 'Acknowledged';"))
            conn.execute(text("UPDATE rescue_updates SET status = 'acknowledged' WHERE status = 'Not Acknowledged';"))
            print("Updated existing status values.")
        except Exception as e:
            print(f"Error updating statuses: {e}")
            
        conn.commit()
        print("Migration complete.")

if __name__ == "__main__":
    migrate()
