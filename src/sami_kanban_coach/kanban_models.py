"""Pydantic models for Phase 1 Kanban state indexing."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CardIndex(BaseModel):
    """Normalised card entry stored in card_index.jsonl."""

    schemaVersion: int = Field(default=1)
    projectId: str = ""
    title: str = ""
    status: str = ""
    risk: str = ""
    lead: str = ""
    owner: str = ""
    reviewDate: str = ""
    lastUpdated: str = ""
    currentState: str = ""
    nextAction: str = ""
    notes: str = ""
    column: str = ""
    tags: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    activityCount: int = 0
    latestActivityAt: str = ""
    sourceHash: str = ""
    summaryForAi: str = ""


class ActivityIndex(BaseModel):
    """Activity/update entry stored in card_activity_index.jsonl."""

    schemaVersion: int = Field(default=1)
    projectId: str = ""
    timestamp: str = ""
    actor: str = ""
    action: str = ""
    summary: str = ""
    source: str = "card_updates.jsonl"


class GeneratedAt(BaseModel):
    """Mixin-style timestamp."""
    generatedAt: str = Field(default_factory=lambda: datetime.now().isoformat())


class KanbanStateSnapshot(BaseModel):
    """Full snapshot of Kanban state written to kanban_state_snapshot.json."""

    schemaVersion: int = Field(default=1)
    generatedAt: str = Field(default_factory=lambda: datetime.now().isoformat())
    sourceMode: str = "local"
    sourceRoot: str = ""
    projectsJsonPath: str = ""
    cardUpdatesPath: str = ""
    projectsJsonHash: str = ""
    cardUpdatesHash: str = ""
    cardCount: int = 0
    countsByStatus: dict[str, int] = Field(default_factory=dict)
    countsByRisk: dict[str, int] = Field(default_factory=dict)
    cards: list[dict[str, Any]] = Field(default_factory=list)


class SourceStatus(BaseModel):
    """Status of one Kanban source (local or team)."""

    root: str = ""
    accessible: bool = False
    projectsJsonExists: bool = False
    cardUpdatesExists: bool = False
    projectsJsonHash: str = ""
    cardUpdatesHash: str = ""
    projectsJsonMtime: str = ""
    cardUpdatesMtime: str = ""


class TeamSourceStatus(SourceStatus):
    """Extended source status with optional network-context info."""
    status: str = ""
    requiredForValidation: bool = False


class KanbanSourceStatus(BaseModel):
    """Persistent status file written to kanban_source_status.json."""

    schemaVersion: int = Field(default=1)
    generatedAt: str = Field(default_factory=lambda: datetime.now().isoformat())
    local: SourceStatus = Field(default_factory=SourceStatus)
    team: TeamSourceStatus = Field(default_factory=TeamSourceStatus)
    selectedSource: str = "local"
