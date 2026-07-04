"""
services/database.py
--------------------
Shared SQLAlchemy database engine and sessionmaker configuration for Synchropia.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from contextlib import contextmanager
from src.config.settings import settings

# Shared DB engine with pool pre-ping to ensure active connection validation
db_engine = create_engine(str(settings.DATABASE_URL_DEV), pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)

@contextmanager
def get_db_session():
    """Context manager to yield a scoped database session and close it automatically."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
