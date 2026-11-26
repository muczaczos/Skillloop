import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import career_goals, competencies, health, profile
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


API_DESCRIPTION = """
REST API of SkillLoop, a personal career knowledge coach.

**Authentication.** Endpoints other than `/health` require a Supabase Auth access token:
`Authorization: Bearer <token>`. The user is always taken from the verified token; no request
field can name another user. A missing, invalid or expired token gives `401` with a
`WWW-Authenticate: Bearer` header.

**Errors.** Every error response, including those of unknown paths and wrong methods, has the
same shape: `{"error": {"code", "message", "request_id"}}`. Request validation errors (`422`)
add `details`, a list of `{"field", "message"}`. Branch on `code`, not on `message`.

**Methods.** A path that exists but does not serve the requested method answers `405` with the
unified error (`METHOD_NOT_ALLOWED`) and an `Allow` header listing every method the path serves.
An unknown path answers `404` (`NOT_FOUND`).
""".strip()

OPENAPI_TAGS = [
    {"name": "health", "description": "Liveness check of the application."},
    {"name": "profile", "description": "The profile of the authenticated user."},
    {
        "name": "career-goals",
        "description": "The professional goal of the user. A user has at most one active goal.",
    },
    {
        "name": "competencies",
        "description": "The competencies of a career goal: listed, added, edited and archived manually.",
    },
]

app = FastAPI(
    title="SkillLoop API",
    version="0.1.0",
    description=API_DESCRIPTION,
    openapi_tags=OPENAPI_TAGS,
    lifespan=lifespan,
)

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
app.include_router(career_goals.router, prefix=API_PREFIX)
app.include_router(competencies.router, prefix=API_PREFIX)
app.include_router(competencies.item_router, prefix=API_PREFIX)
