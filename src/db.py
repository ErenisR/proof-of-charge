import os
import sys
from functools import lru_cache
from pathlib import Path

from alembic import command as alembic_command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


ROOT_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT_DIR / ".env"


def _load_env_file() -> None:
    if os.getenv("PROOF_OF_CHARGE_SKIP_DOTENV"):
        return
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file()


def _truthy(value: str | None) -> bool:
    return value is not None and value.lower() in {"1", "true", "yes", "on"}


def database_url() -> str | None:
    return os.getenv("DATABASE_URL")


def database_required() -> bool:
    return _truthy(os.getenv("REQUIRE_DATABASE"))


def database_enabled() -> bool:
    if database_url():
        return True
    if database_required():
        raise RuntimeError(
            "Database is required but DATABASE_URL is not set. "
            "Start Postgres with `docker compose up -d postgres` and set DATABASE_URL."
        )
    return False


def require_database() -> None:
    if not database_url():
        raise RuntimeError(
            "DATABASE_URL is not set. Start Postgres with `docker compose up -d postgres` "
            "and set DATABASE_URL."
        )


@lru_cache(maxsize=1)
def get_engine():
    url = database_url()
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return create_engine(url, future=True)


@lru_cache(maxsize=1)
def get_session_factory():
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False, future=True)


def drop_db_schema() -> None:
    from .models import Base

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
    elif cli_command == "drop":
        drop_db_schema()
        print("[OK] Database schema dropped")
    else:
        print("Usage: python3 -m src.db [init|drop]")
        sys.exit(1)
