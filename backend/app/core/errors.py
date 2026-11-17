"""Domain errors raised by the service layer.

They are framework-independent on purpose (no FastAPI, no HTTPException). `status_code`
and `code` are the contract for the central exception handler (stage 6.4), which turns
them into the API error structure described in docs/API-DESIGN.md.

Messages are safe to show to the client: they never contain user data or internals.
"""


class AppError(Exception):
    """Base class of expected, client-visible business errors."""

    status_code: int = 500
    code: str = "INTERNAL_ERROR"
    default_message: str = "An unexpected error occurred."

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.default_message
        super().__init__(self.message)


class ProfileNotFoundError(AppError):
    status_code = 404
    code = "PROFILE_NOT_FOUND"
    default_message = "Profile not found."


class GoalNotFoundError(AppError):
    status_code = 404
    code = "GOAL_NOT_FOUND"
    default_message = "Career goal not found."


class NoActiveGoalError(AppError):
    """The user has no active career goal."""

    status_code = 404
    code = "NO_ACTIVE_GOAL"
    default_message = "No active career goal."


class ActiveGoalExistsError(AppError):
    """The user already has an active career goal (one_active_goal_per_user)."""

    status_code = 409
    code = "ACTIVE_GOAL_EXISTS"
    default_message = "An active career goal already exists."


class GoalArchivedError(AppError):
    """The goal exists but is archived, so it can no longer be changed."""

    status_code = 409
    code = "GOAL_ARCHIVED"
    default_message = "The career goal is archived and can no longer be changed."
