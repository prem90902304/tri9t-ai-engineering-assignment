from pydantic import BaseModel, Field, ConfigDict, field_validator
from typing import Literal

class NodeOut(BaseModel):
    id: int
    version_id: int
    logical_key: str
    heading: str
    level: int
    body_text: str
    content_hash: str
    parent_id: int | None
    order_index: int
    node_type: str
    model_config = ConfigDict(from_attributes=True)

class SelectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=250)
    document_id: int
    version_id: int
    node_ids: list[int] = Field(min_length=1)

    @field_validator("node_ids")
    @classmethod
    def node_ids_must_be_unique(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("node_ids must not contain duplicates")
        return value

class GenerationRequest(BaseModel):
    selection_id: int
    force_new: bool = False

class TestCase(BaseModel):
    title: str = Field(min_length=5, max_length=180)
    preconditions: str = Field(min_length=3)
    steps: list[str] = Field(min_length=1, max_length=12)
    expected_result: str = Field(min_length=3)
    requirement_refs: list[str] = Field(min_length=1)
    risk: Literal["low", "medium", "high"]

    @field_validator("title", "preconditions", "expected_result", mode="before")
    @classmethod
    def trim_required_text(cls, value):
        if isinstance(value, str):
            value = value.strip()
        return value

    @field_validator("steps", "requirement_refs")
    @classmethod
    def trim_nonempty_items(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if isinstance(item, str) and item.strip()]
        if len(cleaned) != len(value):
            raise ValueError("list items must be non-empty strings")
        return cleaned

class GeneratedPayload(BaseModel):
    test_cases: list[TestCase] = Field(min_length=3, max_length=5)
    assumptions: list[str] = Field(default_factory=list)

    @field_validator("assumptions")
    @classmethod
    def trim_assumptions(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if isinstance(item, str) and item.strip()]
        if len(cleaned) != len(value):
            raise ValueError("assumptions must contain non-empty strings")
        return cleaned
