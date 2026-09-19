from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Role(str, Enum):
    ANALYST = "analyst"
    SALES_REP = "sales_rep"


class QueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    role: Role
    user_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,40}$")


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class Trace(BaseModel):
    schema_documents: list[dict[str, Any]]
    generation_ms: int
    cache_hit: bool
    safety: dict[str, Any]


class QueryResponse(BaseModel):
    answer_summary: str
    sql: str
    columns: list[str]
    rows: list[list[Any]]
    trace: Trace


class FeedbackRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=1000)
