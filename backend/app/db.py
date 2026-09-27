"""Supabase Postgres access. Rows come back as dicts."""

from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from . import config

MIGRATIONS_DIR = config.ROOT / "supabase" / "migrations"

_pool: ConnectionPool | None = None


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        if not config.DATABASE_URL:
            raise RuntimeError("DATABASE_URL is not set")
        _pool = ConnectionPool(
            config.DATABASE_URL,
            min_size=1,
            max_size=10,
            # prepare_threshold=None keeps us compatible with Supabase's
            # transaction pooler, which does not support prepared statements.
            kwargs={"row_factory": dict_row, "prepare_threshold": None},
            open=True,
        )
    return _pool


def close() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def session():
    """A connection inside one transaction: committed on success, rolled back on error."""
    with pool().connection() as conn:
        yield conn


def get_db():
    """FastAPI dependency."""
    with session() as conn:
        yield conn


def migrate() -> list[str]:
    applied = []
    with session() as conn:
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            conn.execute(path.read_text())
            applied.append(path.name)
    return applied


def one(conn, sql: str, params=()) -> dict | None:
    return conn.execute(sql, params).fetchone()


def all_rows(conn, sql: str, params=()) -> list[dict]:
    return conn.execute(sql, params).fetchall()


__all__ = ["Jsonb", "all_rows", "get_db", "migrate", "one", "session"]
