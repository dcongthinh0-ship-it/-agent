"""Checksum-locked migrations; the exact target and backup are explicit CLI arguments."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
from pathlib import Path

import asyncpg

from chemo_agent_product.config import Settings


async def migrate(dsn: str, directory: Path) -> list[str]:
    connection = await asyncpg.connect(dsn)
    applied = []
    try:
        async with connection.transaction():
            await connection.execute("SELECT pg_advisory_xact_lock(20261007,1)")
            await connection.execute("""CREATE TABLE IF NOT EXISTS ops.product_migration (
              version text PRIMARY KEY, checksum char(64) NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())""")
            for path in sorted(directory.glob("V*.sql")):
                content = path.read_text()
                checksum = hashlib.sha256(content.encode()).hexdigest()
                old = await connection.fetchval(
                    "SELECT checksum FROM ops.product_migration WHERE version=$1", path.stem
                )
                if old is not None:
                    if old != checksum:
                        raise RuntimeError(f"migration checksum changed: {path.name}")
                    continue
                await connection.execute(content)
                await connection.execute(
                    "INSERT INTO ops.product_migration(version,checksum) VALUES($1,$2)",
                    path.stem,
                    checksum,
                )
                applied.append(path.stem)
    finally:
        await connection.close()
    return applied


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--target-database", required=True)
    parser.add_argument("--backup-file", type=Path, required=True)
    parser.add_argument(
        "--directory", type=Path, default=Path(__file__).resolve().parents[2] / "migrations"
    )
    args = parser.parse_args()
    if not args.backup_file.is_file() or args.backup_file.stat().st_size == 0:
        parser.error("a nonempty saved backup is required")
    settings = Settings(_env_file=args.env_file)
    if not settings.database_url:
        parser.error("database is not configured")
    dsn = settings.database_url.get_secret_value()
    from urllib.parse import urlsplit

    if urlsplit(dsn).path.lstrip("/") != args.target_database:
        parser.error("configured database differs from explicit target")
    print({"database": args.target_database, "applied": asyncio.run(migrate(dsn, args.directory))})


if __name__ == "__main__":
    main()
