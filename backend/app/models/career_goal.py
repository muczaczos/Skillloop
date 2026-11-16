"""Row model of public.career_goals: what the database returns. It is not an API schema."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

# Mirror the CHECK constraints career_goals_seniority_check and career_goals_status_check.
Seniority = Literal["junior", "mid", "senior"]
GoalStatus = Literal["active", "archived"]


class CareerGoalRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    user_id: UUID
    target_role: str
    seniority: Seniority
    description: str | None
    status: GoalStatus
    created_at: datetime
    updated_at: datetime
