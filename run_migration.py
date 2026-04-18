#!/usr/bin/env python3
"""
Migration Runner Script
Run database migrations for supervisor-api

Usage:
    python run_migration.py                      # Run all pending migrations
    python run_migration.py --check               # Check migration status
    python run_migration.py --file migrations/xxx.sql  # Run specific migration
"""
import asyncio
import sys
import argparse
from pathlib import Path
import structlog

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from sqlalchemy import text
from src.db import async_session
from src.config import get_settings

logger = structlog.get_logger()
settings = get_settings()


async def check_migrations_table():
    """Check if migrations tracking table exists, create if not."""
    async with async_session() as session:
        result = await session.execute(text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'schema_migrations'
            )
        """))
        exists = result.scalar()
        if not exists:
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    id SERIAL PRIMARY KEY,
                    version VARCHAR(50) NOT NULL UNIQUE,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    description TEXT
                )
            """))
            await session.commit()
            logger.info("Created schema_migrations table")
        return exists


async def get_applied_migrations() -> set:
    """Get list of applied migration versions."""
    await check_migrations_table()
    async with async_session() as session:
        result = await session.execute(text("SELECT version FROM schema_migrations"))
        return {row[0] for row in result.fetchall()}


async def run_migration_file(file_path: str, dry_run: bool = False):
    """Run a single migration file."""
    file_path = Path(file_path)
    if not file_path.exists():
        logger.error("Migration file not found", path=str(file_path))
        return False
    
    # Extract version from filename (e.g., 20260418_xxx.sql -> 20260418)
    version = file_path.stem.split('_')[0]
    
    # Read SQL content
    sql = file_path.read_text()
    
    # Check if already applied
    applied = await get_applied_migrations()
    if version in applied:
        logger.info("Migration already applied", version=version)
        return True
    
    if dry_run:
        logger.info("DRY RUN - Would execute:", version=version)
        logger.info("SQL:", sql=sql[:500] + "..." if len(sql) > 500 else sql)
        return True
    
    # Execute migration
    async with async_session() as session:
        try:
            # Execute entire SQL file as single statement 
            # (handles DO $$ blocks and BEGIN...COMMIT correctly)
            await session.execute(text(sql))
            
            # Record migration
            await session.execute(text(
                "INSERT INTO schema_migrations (version, description) VALUES (:version, :desc)"
            ), {"version": version, "desc": file_path.name})
            
            await session.commit()
            logger.info("Migration applied successfully", version=version)
            return True
        except Exception as e:
            await session.rollback()
            logger.error("Migration failed", version=version, error=str(e))
            return False


async def run_all_migrations(dry_run: bool = False):
    """Run all pending migrations in order."""
    migrations_dir = Path(__file__).parent / "migrations"
    
    # Get all SQL files sorted by name
    migration_files = sorted(migrations_dir.glob("*.sql"))
    
    # Filter out down migrations for regular runs
    migration_files = [f for f in migration_files if "down.sql" not in f.name]
    
    applied = await get_applied_migrations()
    
    logger.info("Checking migrations", total=len(migration_files), applied=len(applied))
    
    success_count = 0
    for mf in migration_files:
        version = mf.stem.split('_')[0]
        if version in applied:
            logger.info("Skipping already applied", version=version, file=mf.name)
            continue
        
        logger.info("Running migration", version=version, file=mf.name)
        if await run_migration_file(str(mf), dry_run):
            success_count += 1
        else:
            logger.error("Migration failed, stopping", file=mf.name)
            break
    
    logger.info("Migration complete", successful=success_count)
    return success_count


async def check_status():
    """Check migration status."""
    migrations_dir = Path(__file__).parent / "migrations"
    migration_files = sorted(migrations_dir.glob("*.sql"))
    migration_files = [f for f in migration_files if "down.sql" not in f.name]
    
    applied = await get_applied_migrations()
    
    print("\n=== Migration Status ===\n")
    print(f"{'File':<50} {'Status':<15}")
    print("-" * 65)
    
    for mf in migration_files:
        version = mf.stem.split('_')[0]
        status = "APPLIED" if version in applied else "PENDING"
        print(f"{mf.name:<50} {status:<15}")
    
    print(f"\nTotal: {len(migration_files)} migrations, {len(applied)} applied, {len(migration_files) - len(applied)} pending")
    
    # Show applied migrations
    if applied:
        print("\nApplied versions:", ", ".join(sorted(applied)))


async def main():
    parser = argparse.ArgumentParser(description="Run database migrations")
    parser.add_argument("--check", action="store_true", help="Check migration status")
    parser.add_argument("--dry-run", action="store_true", help="Preview migrations without applying")
    parser.add_argument("--file", type=str, help="Run specific migration file")
    
    args = parser.parse_args()
    
    if args.check:
        await check_status()
    elif args.file:
        success = await run_migration_file(args.file, dry_run=args.dry_run)
        sys.exit(0 if success else 1)
    else:
        await run_all_migrations(dry_run=args.dry_run)


if __name__ == "__main__":
    asyncio.run(main())