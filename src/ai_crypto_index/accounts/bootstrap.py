from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Role, RoleSlug

DEFAULT_ROLES: Sequence[dict[str, object]] = (
    {
        "slug": RoleSlug.ADMIN.value,
        "name": "Administrator",
        "description": "Full access to moderation tools and billing.",
        "is_default": False,
        "priority": 1,
    },
    {
        "slug": RoleSlug.MODERATOR.value,
        "name": "Moderator",
        "description": "Manages user approvals, limits, and manual reviews.",
        "is_default": False,
        "priority": 5,
    },
    {
        "slug": RoleSlug.MEMBER.value,
        "name": "Member",
        "description": "Default self-serve user with access to dashboard and API.",
        "is_default": True,
        "priority": 10,
    },
)


async def ensure_default_roles(session: AsyncSession) -> None:
    """Idempotently insert baseline roles required by the auth system."""

    existing = await session.execute(select(Role.slug))
    existing_slugs = {slug for (slug,) in existing.all()}

    created = False
    for payload in DEFAULT_ROLES:
        slug = payload["slug"]
        if slug in existing_slugs:
            continue
        role = Role(
            slug=str(payload["slug"]),
            name=str(payload["name"]),
            description=str(payload["description"]),
            is_default=bool(payload["is_default"]),
            priority=int(payload["priority"]),
        )
        session.add(role)
        created = True

    if created:
        await session.commit()
