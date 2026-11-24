"""API schemas of competencies (docs/API-DESIGN.md, section 7).

The owner and the goal come from the token and the path, and the status is set by the database:
a competency is created active and is archived through its own endpoint (Stage 9.3). Therefore
`id`, `career_goal_id`, `user_id`, `status`, `created_at` and `updated_at` are rejected as unknown
fields, and the response never repeats `user_id`.
"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.competency import CompetencyStatus, ExpectedLevel, Importance, KnowledgeType
from app.schemas.common import (
    CompetencyCategory,
    CompetencyName,
    CompetencyText,
    DisplayOrder,
    UtcDatetime,
)


class CompetencyCreate(BaseModel):
    """POST /career-goals/{goal_id}/competencies."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "name": "Terraform",
                    "description": "Infrastructure as Code.",
                    "rationale": "Infrastructure is usually defined as code.",
                    "category": "IaC",
                    "knowledge_type": "application",
                    "importance": "high",
                    "expected_level": "intermediate",
                    "display_order": 4,
                }
            ]
        },
    )

    name: CompetencyName
    description: CompetencyText = None
    rationale: CompetencyText = None
    category: CompetencyCategory = None
    knowledge_type: KnowledgeType
    importance: Importance
    expected_level: ExpectedLevel
    display_order: DisplayOrder = 0


class CompetencyResponse(BaseModel):
    """A competency as returned by the API (user_id is not repeated: it is the caller)."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "5b2c8a14-3f0e-4d6a-9c1b-7e8f2a4d6b10",
                    "career_goal_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
                    "name": "Docker",
                    "description": "Understanding containerization.",
                    "rationale": "Docker is commonly used to package and deploy applications.",
                    "category": "Containers",
                    "knowledge_type": "application",
                    "importance": "high",
                    "expected_level": "intermediate",
                    "display_order": 0,
                    "status": "active",
                    "created_at": "2026-11-23T10:00:00Z",
                    "updated_at": "2026-11-23T10:00:00Z",
                }
            ]
        },
    )

    id: UUID
    career_goal_id: UUID
    name: str
    description: str | None
    rationale: str | None
    category: str | None
    knowledge_type: KnowledgeType
    importance: Importance
    expected_level: ExpectedLevel
    display_order: int
    status: CompetencyStatus
    created_at: UtcDatetime
    updated_at: UtcDatetime


class CompetencyListResponse(BaseModel):
    """GET /career-goals/{goal_id}/competencies: every competency of the goal, active and
    archived, in the order the repository returns them."""

    competencies: list[CompetencyResponse]
