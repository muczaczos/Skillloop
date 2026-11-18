import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health, profile
from app.api.errors import register_error_handlers
from app.core.config import settings
from app.db.pool import STARTUP_WAIT_SECONDS, create_pool, wait_until_ready

API_PREFIX = "/api/v1"

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the database pool on start-up and close it on shutdown.

    Degraded mode: the application starts even if the database cannot be reached within
    STARTUP_WAIT_SECONDS. The pool stays open and keeps reconnecting in the background;
    requests that need the database answer 503 until it works. /health is a liveness
    check and says nothing about the database.
    """
    pool = create_pool(settings)
    pool.open(wait=False)
    app.state.db_pool = pool
    try:
        # Both calls block, so they run in a worker thread, not on the event loop.
        if await asyncio.to_thread(wait_until_ready, pool, STARTUP_WAIT_SECONDS):
            logger.info("Database connection pool is ready")
        else:
            logger.warning(
                "Database is not reachable yet; starting in degraded mode "
                "(requests that need the database will return 503)"
            )
        yield
    finally:
        await asyncio.to_thread(pool.close)


app = FastAPI(title="SkillLoop API", version="0.1.0", lifespan=lifespan)

# Every error response of the API goes through these handlers (docs/API-DESIGN.md, section 13).
register_error_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(health.router, prefix=API_PREFIX)
app.include_router(profile.router, prefix=API_PREFIX)
