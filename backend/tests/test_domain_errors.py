from pathlib import Path

import pytest

from app.core import errors
from app.core.errors import (
    ActiveGoalExistsError,
    AppError,
    CompetencyLimitReachedError,
    CompetencyNameExistsError,
    GoalArchivedError,
    GoalNotFoundError,
    NoActiveGoalError,
    ProfileNotFoundError,
)


@pytest.mark.parametrize(
    ("error_class", "status_code", "code"),
    [
        (ProfileNotFoundError, 404, "PROFILE_NOT_FOUND"),
        (GoalNotFoundError, 404, "GOAL_NOT_FOUND"),
        (NoActiveGoalError, 404, "NO_ACTIVE_GOAL"),
        (ActiveGoalExistsError, 409, "ACTIVE_GOAL_EXISTS"),
        (GoalArchivedError, 409, "GOAL_ARCHIVED"),
        (CompetencyLimitReachedError, 409, "COMPETENCY_LIMIT_REACHED"),
        (CompetencyNameExistsError, 409, "COMPETENCY_NAME_EXISTS"),
    ],
)
def test_error_contract(error_class: type[AppError], status_code: int, code: str) -> None:
    error = error_class()

    assert isinstance(error, AppError)
    assert error.status_code == status_code
    assert error.code == code
    assert error.message == str(error) == error_class.default_message


def test_custom_message_overrides_default() -> None:
    assert AppError("custom").message == "custom"


def test_codes_are_unique() -> None:
    subclasses = AppError.__subclasses__()
    codes = [cls.code for cls in subclasses]

    assert len(codes) == len(set(codes)) == 7


def test_errors_module_does_not_depend_on_fastapi() -> None:
    assert not hasattr(errors, "HTTPException")
    assert "fastapi" not in Path(errors.__file__).read_text(encoding="utf-8")
