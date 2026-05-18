from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ai_crypto_index.shared.settings import ServiceSettings, get_settings

from .db import get_sessionmaker
from .service import AccountService


async def get_db_session(
    settings: ServiceSettings = Depends(get_settings),
) -> AsyncIterator[AsyncSession]:
    session_factory = await get_sessionmaker(settings)
    async with session_factory() as session:
        yield session


def get_account_service(
    settings: ServiceSettings = Depends(get_settings),
) -> AccountService:
    return AccountService(settings)
