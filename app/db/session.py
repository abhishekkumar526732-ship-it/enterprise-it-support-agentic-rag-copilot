"""
Database Session & Connection Management
────────────────────────────────────────
Configures SQLAlchemy engine, session maker, and FastAPI dependency.
Supports:
  • Primary: PostgreSQL with connection pooling & pre-ping
  • Fallback: Local SQLite if PostgreSQL service is offline or not running
  • Dependency: get_db() yields an active DB session
"""

import logging
from typing import Generator
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import QueuePool, StaticPool

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_engine = None
_session_factory = None
_is_postgres_active: bool = False


def _build_engine():
    """
    Constructs the SQLAlchemy engine. Tries PostgreSQL first if enabled.
    Falls back gracefully to SQLite if PostgreSQL is unreachable.
    """
    global _engine, _session_factory, _is_postgres_active

    if _engine is not None:
        return _engine

    if settings.postgres_enabled:
        pg_url = settings.sync_database_url
        logger.info("Attempting connection to PostgreSQL: %s:%s/%s",
                    settings.postgres_host, settings.postgres_port, settings.postgres_db)
        try:
            # Short connect_timeout so app starts fast even if Postgres is not currently running
            test_engine = create_engine(
                pg_url,
                poolclass=QueuePool,
                pool_size=settings.db_pool_size,
                max_overflow=settings.db_max_overflow,
                pool_pre_ping=True,
                echo=settings.db_echo,
                connect_args={"connect_timeout": settings.db_connect_timeout},
            )
            # Test connectivity
            with test_engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            
            logger.info(" Connected to PostgreSQL database successfully.")
            _engine = test_engine
            _is_postgres_active = True
            _session_factory = sessionmaker(autocommit=False, autoflush=False, bind=_engine)
            return _engine
        except Exception as exc:
            logger.warning(
                " PostgreSQL is not reachable (%s). Falling back to SQLite (%s). "
                "PostgreSQL will be used as soon as the service is started.",
                str(exc).split("\n")[0],
                settings.audit_db_path,
            )

    # Fallback to local SQLite engine
    sqlite_url = settings.fallback_sqlite_url
    logger.info("Using SQLite database at %s", sqlite_url)
    _engine = create_engine(
        sqlite_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=settings.db_echo,
    )
    _is_postgres_active = False
    _session_factory = sessionmaker(autocommit=False, autoflush=False, bind=_engine)
    return _engine


def get_engine():
    """Returns the initialized SQLAlchemy engine."""
    global _engine
    if _engine is None:
        _build_engine()
    return _engine


def get_session_factory() -> sessionmaker:
    """Returns the initialized sessionmaker."""
    global _session_factory
    if _session_factory is None:
        _build_engine()
    return _session_factory


def is_postgres_available() -> bool:
    """Check whether active database connection is using PostgreSQL."""
    global _is_postgres_active
    get_engine()
    return _is_postgres_active


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency yielding a database session per request.
    Automatically commits or rolls back on exceptions.
    """
    factory = get_session_factory()
    db = factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# Initialize on import
engine = get_engine()
SessionLocal = get_session_factory()
