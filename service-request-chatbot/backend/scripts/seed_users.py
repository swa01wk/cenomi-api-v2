#!/usr/bin/env python3
"""Seed one user per role for testing the Helper Agent pipeline.

Usage:
    cd backend
    python scripts/seed_users.py

Requires DATABASE_URL to be set (or .env present).
Safe to run multiple times — upserts roles/properties for existing users.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Make sure the backend package is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.auth import hash_password
from app.core.config import settings
from app.db.models import User

# ---------------------------------------------------------------------------
# Seed data — one user per role
# ---------------------------------------------------------------------------

SEED_USERS = [
    {
        "username": "aisha@cenomi.com",
        "email": "aisha@cenomi.com",
        "password": "test1234",
        "role": "MALL_MANAGER",
        # property_id 3041 = Jawharat Jeddah (matches mock lease t0105712)
        "unique_property_ids": [3041],
        "mall_names": ["Jawharat Jeddah"],
        "is_global_admin": False,
    },
    {
        "username": "khalid@cenomi.com",
        "email": "khalid@cenomi.com",
        "password": "test1234",
        "role": "FM_MANAGER",
        # property_id 3041 = Jawharat Jeddah (matches mock lease t0105712)
        "unique_property_ids": [3041],
        "mall_names": ["Jawharat Jeddah"],
        "is_global_admin": False,
    },
    {
        "username": "omar@cenomi.com",
        "email": "omar@cenomi.com",
        "password": "test1234",
        "role": "OPERATIONS",
        # property_id 3041 = Jawharat Jeddah (matches mock lease t0105712)
        "unique_property_ids": [3041],
        "mall_names": ["Jawharat Jeddah"],
        "is_global_admin": False,
    },
    {
        "username": "sara@cenomi.com",
        "email": "sara@cenomi.com",
        "password": "test1234",
        "role": "DD_ENGINEER",
        "unique_property_ids": [],
        "mall_names": [],
        "is_global_admin": False,
    },
    {
        "username": "admin@cenomi.com",
        "email": "admin@cenomi.com",
        "password": "test1234",
        "role": "ADMIN",
        "unique_property_ids": [],
        "mall_names": [],
        "is_global_admin": True,
    },
    # ── Sales Data demo users ──────────────────────────────────────────────────
    {
        "username": "tenant.csv@cenomi.com",
        "email": "tenant.csv@cenomi.com",
        "password": "test1234",
        "role": "TENANT_CSV_UPLOADER",
        "unique_property_ids": [],
        "mall_names": [],
        "is_global_admin": False,
    },
    {
        "username": "tenant.doc@cenomi.com",
        "email": "tenant.doc@cenomi.com",
        "password": "test1234",
        "role": "TENANT_DOC_UPLOADER",
        "unique_property_ids": [],
        "mall_names": [],
        "is_global_admin": False,
    },
    {
        "username": "sales.approver@cenomi.com",
        "email": "sales.approver@cenomi.com",
        "password": "test1234",
        "role": "SALES_DATA_APPROVER",
        "unique_property_ids": [],
        "mall_names": [],
        "is_global_admin": False,
    },
]


async def seed() -> None:
    engine = create_async_engine(settings.database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        created = 0
        skipped = 0

        for data in SEED_USERS:
            result = await session.execute(
                select(User).where(User.username == data["username"])
            )
            existing = result.scalar_one_or_none()

            if existing is not None:
                # Upsert: always sync role, properties, and admin flag
                changed: list[str] = []
                if existing.role != data["role"]:
                    existing.role = data["role"]
                    changed.append("role")
                if existing.unique_property_ids != data["unique_property_ids"]:
                    existing.unique_property_ids = data["unique_property_ids"]
                    changed.append("unique_property_ids")
                if existing.mall_names != data["mall_names"]:
                    existing.mall_names = data["mall_names"]
                    changed.append("mall_names")
                if existing.is_global_admin != data["is_global_admin"]:
                    existing.is_global_admin = data["is_global_admin"]
                    changed.append("is_global_admin")
                if changed:
                    print(f"  UPDATE {data['username']} (updated: {', '.join(changed)})")
                else:
                    print(f"  OK     {data['username']} (role={existing.role}, no changes)")
                skipped += 1
                continue

            user = User(
                username=data["username"],
                email=data["email"],
                password_hash=hash_password(data["password"]),
                role=data["role"],
                unique_property_ids=data["unique_property_ids"],
                mall_names=data["mall_names"],
                is_global_admin=data["is_global_admin"],
                is_active=True,
            )
            session.add(user)
            print(f"  CREATE {data['username']} (role={data['role']})")
            created += 1

        await session.commit()
        print(f"\nDone. Created: {created}  Skipped: {skipped}")

    await engine.dispose()


if __name__ == "__main__":
    print("Seeding users...")
    asyncio.run(seed())
