import os
import sys
from functools import lru_cache

from alembic import command as alembic_command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

DEFAULT_DATABASE_URL = "postgresql+psycopg://proof:proof@localhost:5432/proof_of_charge"


def database_url() -> str | None:
    return os.getenv("DATABASE_URL")


def database_enabled() -> bool:
    return bool(database_url())


@lru_cache(maxsize=1)
def get_engine():
    url = database_url()
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return create_engine(url, future=True)


@lru_cache(maxsize=1)
def get_session_factory():
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False, future=True)


def create_db_schema() -> None:
    Base.metadata.create_all(get_engine())


def drop_db_schema() -> None:
    Base.metadata.drop_all(get_engine())


def run_migrations() -> None:
    alembic_command.upgrade(Config("alembic.ini"), "head")


def session_scope() -> Session:
    return get_session_factory()()


if __name__ == "__main__":
    cli_command = sys.argv[1] if len(sys.argv) > 1 else "init"
    if cli_command == "init":
        run_migrations()
        print("[OK] Database schema migrated to head")
    elif cli_command == "create-all":
        create_db_schema()
        print("[OK] Database schema created from SQLAlchemy metadata")
    elif cli_command == "drop":
        drop_db_schema()
        print("[OK] Database schema dropped")
    else:
        print("Usage: python3 -m src.db [init|create-all|drop]")
        sys.exit(1)
