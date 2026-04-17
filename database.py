"""
database.py — SQLAlchemy engine + session factory.

SQLite is used by default (file-based, zero infra cost).
Set DATABASE_URL env-var to swap to PostgreSQL on any host.

SQLite optimisations applied on every connection:
  • WAL journal mode  → safe concurrent reads while a write is in flight
  • NORMAL synchronous → good durability / speed trade-off
  • foreign_keys ON   → enforce FK constraints
"""

import os

from dotenv import load_dotenv
load_dotenv()  # load .env before reading any os.getenv()

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "sqlite:///./qr_redirects.db",
)

# SQLite needs check_same_thread=False; other DBs don't accept that kwarg.
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

# pool_size / max_overflow are not valid for SQLite's StaticPool/NullPool,
# so we only pass them for non-SQLite backends.
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        DATABASE_URL,
        connect_args=_connect_args,
        pool_pre_ping=True,
    )
else:
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
    )


# ── SQLite runtime PRAGMAs ────────────────────────────────────────────────────
if DATABASE_URL.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA cache_size=-32000")  # ~32 MB page cache
        cur.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


# ── Dependency for FastAPI routes ─────────────────────────────────────────────
def get_db():
    """Yield a DB session and guarantee it is closed afterwards."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
