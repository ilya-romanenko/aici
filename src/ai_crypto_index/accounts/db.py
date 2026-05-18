from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from sqlalchemy import MetaData, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from ai_crypto_index.shared.settings import ServiceSettings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


_ENGINE_CACHE: dict[str, AsyncEngine] = {}
_SESSION_CACHE: dict[str, async_sessionmaker[AsyncSession]] = {}
_CACHE_LOCK = asyncio.Lock()


async def _get_or_create_engine(settings: ServiceSettings) -> AsyncEngine:
    db_url = settings.auth.database_url
    existing = _ENGINE_CACHE.get(db_url)
    if existing is not None:
        return existing

    async with _CACHE_LOCK:
        existing = _ENGINE_CACHE.get(db_url)
        if existing is not None:
            return existing

        connect_args: dict[str, Any] = {}
        if "sqlite" in db_url:
            connect_args["check_same_thread"] = False
            url = make_url(db_url)
            db_path = url.database
            if db_path and db_path != ":memory:":
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        engine = create_async_engine(
            db_url,
            echo=settings.auth.echo_sql,
            future=True,
            pool_pre_ping=True,
            connect_args=connect_args,
        )
        _ENGINE_CACHE[db_url] = engine
        return engine


async def get_sessionmaker(settings: ServiceSettings) -> async_sessionmaker[AsyncSession]:
    db_url = settings.auth.database_url
    existing = _SESSION_CACHE.get(db_url)
    if existing is not None:
        return existing

    async with _CACHE_LOCK:
        existing = _SESSION_CACHE.get(db_url)
        if existing is not None:
            return existing

        engine = await _get_or_create_engine(settings)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        _SESSION_CACHE[db_url] = session_factory
        return session_factory


async def ensure_schema(settings: ServiceSettings) -> None:
    engine = await _get_or_create_engine(settings)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await _ensure_timezone_columns(connection)


async def _ensure_timezone_columns(connection) -> None:
    if connection.dialect.name != "postgresql":
        return

    targets = (
        ("auth_email_tokens", "consumed_at"),
        ("auth_password_reset_tokens", "consumed_at"),
    )

    for table_name, column_name in targets:
        result = await connection.execute(
            text(
                """
                SELECT data_type
                FROM information_schema.columns
                WHERE table_name = :table_name AND column_name = :column_name
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        )
        data_type = result.scalar_one_or_none()
        if data_type != "timestamp without time zone":
            continue

        alter_statement = text(
            f"""
            ALTER TABLE {table_name}
            ALTER COLUMN {column_name}
            TYPE TIMESTAMP WITH TIME ZONE
            USING {column_name} AT TIME ZONE 'UTC'
            """
        )
        await connection.execute(alter_statement)
