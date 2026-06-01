from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from app.config import settings

# Prefix with sqlite:/// for SQLAlchemy connection
DATABASE_URL = f"sqlite:///./{settings.DB_PATH}"

# check_same_thread=False is essential for SQLite when handled in a multi-threaded web API & detector pipeline
engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    """
    Dependency injection generator for database sessions.
    Automatically handles closure of connection after requests complete.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
