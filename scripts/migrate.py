#!/usr/bin/env python3
"""
Script khởi tạo / migrate database.
Chạy một lần trước khi start app (hoặc để auto-run khi startup).

Cách dùng:
  python scripts/migrate.py
  # hoặc từ Docker:
  docker compose run --rm backend python /app/../scripts/migrate.py
"""

import asyncio
import sys
import os

# Thêm backend vào path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# Load config
from app.config import settings
from app.models.db import Base


async def migrate():
    print(f"Connecting to: {settings.DATABASE_URL}")
    engine = create_async_engine(settings.DATABASE_URL, echo=True)

    async with engine.begin() as conn:
        print("→ Enabling pgvector extension...")
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

        print("→ Creating tables...")
        await conn.run_sync(Base.metadata.create_all)

    await engine.dispose()
    print("✓ Migration xong!")


if __name__ == "__main__":
    asyncio.run(migrate())
