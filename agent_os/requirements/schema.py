"""Pydantic models for requirements YAML structure."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AcceptanceCriteriaYAML(BaseModel):
    id: str
    title: str
    description: str = ""


class StoryYAML(BaseModel):
    id: str
    title: str
    description: str = ""
    acceptance_criteria: list[AcceptanceCriteriaYAML] = Field(default_factory=list)


class FeatureYAML(BaseModel):
    id: str
    title: str
    description: str = ""
    stories: list[StoryYAML] = Field(default_factory=list)


class EpicYAML(BaseModel):
    id: str
    title: str
    description: str = ""
    features: list[FeatureYAML] = Field(default_factory=list)


class RequirementsDocument(BaseModel):
    epics: list[EpicYAML] = Field(default_factory=list)
