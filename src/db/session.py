from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import event
from src.core.metrics import metrics
from sqlalchemy import text
from contextlib import asynccontextmanager
from typing import AsyncGenerator
import structlog

from src.config import get_settings

settings = get_settings()
logger = structlog.get_logger()

engine = create_async_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=settings.app_debug,
)

# Add connection pool metrics
@event.listens_for(engine.sync_engine.pool, 'checkout')
def receive_checkout(dbapi_conn, conn_record, conn_proxy):
    metrics.record_db_connection_checkout()

@event.listens_for(engine.sync_engine.pool, 'checkin')
def receive_checkin(dbapi_conn, conn_record):
    metrics.record_db_connection_checkin()

@event.listens_for(engine.sync_engine.pool, 'connect')
def receive_connect(dbapi_conn, conn_record):
    metrics.record_db_connection_created()

def get_pool_status():
    """Return current connection pool status."""
    pool = engine.sync_engine.pool
    return {
        "size": pool.size(),
        "checked_in": pool.checkedin(),
        "overflow": pool.overflow(),
        "total": pool.total(),
    }

async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db():
    logger.info("Initializing database")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("ALTER TABLE IF EXISTS knowledge_documents ADD COLUMN IF NOT EXISTS file_url VARCHAR(500)"))
        await conn.execute(text("ALTER TABLE IF EXISTS knowledge_documents ADD COLUMN IF NOT EXISTS extra_metadata JSONB DEFAULT '{}'::jsonb"))
        await conn.execute(text("ALTER TABLE IF EXISTS interaction_logs ADD COLUMN IF NOT EXISTS traffic_class VARCHAR(32)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_interaction_logs_traffic_class ON interaction_logs(traffic_class)"))
    logger.info("Database initialized successfully")


async def close_db():
    logger.info("Closing database connections")
    await engine.dispose()
    logger.info("Database connections closed")


async def check_db_health() -> bool:
    try:
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
            return True
    except Exception as e:
        logger.error("Database health check failed", error=str(e))
        return False
