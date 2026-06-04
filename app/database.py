import os

from dotenv import load_dotenv

from sqlalchemy import create_engine

from sqlalchemy.orm import sessionmaker, declarative_base

load_dotenv()

# Replace YOUR_PASSWORD with your PostgreSQL password
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:9845@localhost:5432/disaster360_db")

# PostgreSQL lai fastapi sanga connect garne
engine = create_engine(DATABASE_URL, pool_pre_ping=True)

# Data haru insert, read, update ani delete garna
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

# Base class (all tables will inherit from this)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
