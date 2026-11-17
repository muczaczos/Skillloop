"""Business logic for the user's profile.

Each function owns one user_transaction(), so the repository calls run as the
authenticated user and Row Level Security applies. Database errors are not translated here.
"""

from psycopg_pool import ConnectionPool

from app.core.errors import ProfileNotFoundError
from app.db.session import user_transaction
from app.models.auth import AuthenticatedUser
from app.models.profile import ProfileRecord
from app.repositories import profiles
from app.schemas.profile import ProfileUpdate


def get_profile(pool: ConnectionPool, user: AuthenticatedUser) -> ProfileRecord:
    with user_transaction(pool, user) as conn:
        profile = profiles.get(conn, user.id)
    if profile is None:
        raise ProfileNotFoundError
    return profile


def update_profile(
    pool: ConnectionPool, user: AuthenticatedUser, data: ProfileUpdate
) -> ProfileRecord:
    with user_transaction(pool, user) as conn:
        profile = profiles.update(conn, user.id, data.changes())
    if profile is None:
        raise ProfileNotFoundError
    return profile
