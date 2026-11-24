"""Row model of public.competencies: what the database returns. It is not an API schema.

The record carries `user_id` (the owner) because the table does; the API schemas never accept it
from a client and never return it.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

# Mirror the CHECK constraints competencies_knowledge_type_check, competencies_importance_check,
# competencies_expected_level_check and competencies_status_check.
KnowledgeType = Literal["memory", "application", "documentation"]
Importance = Literal["critical", "high", "medium", "low"]
# not_assessed is a valid CURRENT level elsewhere, but never an expected level.
ExpectedLevel = Literal["beginner", "intermediate", "advanced"]
CompetencyStatus = Literal["active", "archived"]


class CompetencyRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    career_goal_id: UUID
    user_id: UUID
    name: str
    description: str | None
    rationale: str | None
    category: str | None
    knowledge_type: KnowledgeType
    importance: Importance
    expected_level: ExpectedLevel
    display_order: int
    status: CompetencyStatus
    created_at: datetime
    updated_at: datetime
