"""
Database Initialization & Table Seeding
───────────────────────────────────────
Creates all required tables and seeds default enterprise user accounts
if they do not already exist.
"""

import logging
from passlib.context import CryptContext

from app.db.base import Base
from app.db.session import get_engine, get_session_factory, is_postgres_available
from app.db.models import User

logger = logging.getLogger(__name__)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

DEFAULT_USERS = [
    {
        "username": "admin",
        "email": "admin@enterprise.local",
        "password": "admin123",
        "role": "admin",
    },
    {
        "username": "analyst",
        "email": "analyst@enterprise.local",
        "password": "analyst123",
        "role": "analyst",
    },
    {
        "username": "user",
        "email": "user@enterprise.local",
        "password": "user123",
        "role": "user",
    },
]


def init_database() -> None:
    """
    Creates all database tables defined in Base and seeds default accounts.
    """
    engine = get_engine()
    backend = "PostgreSQL" if is_postgres_available() else "SQLite (fallback)"
    logger.info("Initializing database schema on %s...", backend)

    try:
        # Create all tables if they don't exist
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables verified/created successfully.")

        # Seed default users
        session_factory = get_session_factory()
        with session_factory() as session:
            for u in DEFAULT_USERS:
                existing = session.query(User).filter(User.username == u["username"]).first()
                if not existing:
                    user_record = User(
                        username=u["username"],
                        email=u["email"],
                        hashed_password=pwd_context.hash(u["password"]),
                        role=u["role"],
                        is_active=True,
                    )
                    session.add(user_record)
                    logger.info("Seeded default user: %s (%s)", u["username"], u["role"])
            session.commit()

    except Exception as exc:
        logger.error("Error during database initialization: %s", exc)
