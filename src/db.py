import os
import sys
from functools import lru_cache
from pathlib import Path

from alembic import command as alembic_command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker


ROOT_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT_DIR / ".env"
ALEMBIC_INI = ROOT_DIR / "alembic.ini"


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
    alembic_command.upgrade(Config(str(ALEMBIC_INI)), "head")


def check_database() -> dict[str, object]:
    from .models import Base

    require_database()
    engine = get_engine()
    expected_tables = set(Base.metadata.tables.keys())

    with engine.connect() as connection:
        identity = connection.execute(
            text("SELECT current_user, current_database()")
        ).one()
        inspector = inspect(connection)
        existing_tables = set(inspector.get_table_names())
        missing_tables = sorted(expected_tables - existing_tables)

        current_revision = None
        if "alembic_version" in existing_tables:
            current_revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar()

    script = ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))
    head_revision = script.get_current_head()

    return {
        "user": identity[0],
        "database": identity[1],
        "current_revision": current_revision,
        "head_revision": head_revision,
        "missing_tables": missing_tables,
        "ok": not missing_tables and current_revision == head_revision,
    }


def session_scope() -> Session:
    return get_session_factory()()


if __name__ == "__main__":
    cli_command = sys.argv[1] if len(sys.argv) > 1 else "init"
    if cli_command == "init":
        run_migrations()
        print("[OK] Database schema migrated to head")
    elif cli_command == "check":
        result = check_database()
        print(f"[OK] connected to {result['database']} as {result['user']}")
        if result["current_revision"] == result["head_revision"]:
            print(f"[OK] alembic head applied: {result['head_revision']}")
        else:
            print(
                "[FAIL] alembic revision mismatch: "
                f"current={result['current_revision']} head={result['head_revision']}"
            )
        if result["missing_tables"]:
            print("[FAIL] missing tables: " + ", ".join(result["missing_tables"]))
        else:
            print("[OK] tables present")
        if not result["ok"]:
            sys.exit(1)
    elif cli_command == "drop":
        drop_db_schema()
        print("[OK] Database schema dropped")
    else:
        print("Usage: python3 -m src.db [init|check|drop]")
        sys.exit(1)
