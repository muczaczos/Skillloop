import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import health
from app.core.config import settings
from app.db.errors import DatabaseUnavailableError
from app.db.pool import STARTUP_WAIT_SECONDS, create_pool, wait_until_ready

API_PREFIX = "/api/v1"

DATABASE_UNAVAILABLE_DETAIL = "The database is temporarily unavailable. Please try again later."

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(DatabaseUnavailableError)
async def database_unavailable_handler(
    request: Request, exc: DatabaseUnavailableError
) -> JSONResponse:
    # str(exc) holds only the cause's class name / SQLSTATE: safe to log.
    logger.error("%s", exc)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": DATABASE_UNAVAILABLE_DETAIL},
    )


app.include_router(health.router, prefix=API_PREFIX)
