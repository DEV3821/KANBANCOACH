"""Pydantic models for Phase 4B Kanban apply engine."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ApplyEvidence(BaseModel):
    type: str = "email"
    messageKey: str = ""
    subject: str = ""
    from_: str = Field(default="", alias="from")
    receivedAt: str = ""
    summary: str = ""


class PlanItem(BaseModel):
    """An item in the apply plan (one approved/edited draft ready to consider)."""

    schemaVersion: int = Field(default=1)
    applyId: str = ""
    draftId: str = ""
    projectId: str = ""
    title: str = ""
    sourceType: str = "approved"
    sourceCardHash: str = ""
    currentLiveCardHash: str = ""
    hashStatus: str = "missing"
    approvedCurrentState: str = ""
    approvedNextAction: str = ""
    approvedStatus: str = ""
    approvedRisk: str = ""
    sourceEmailKeys: list[str] = Field(default_factory=list)
    evidence: list[ApplyEvidence] = Field(default_factory=list)
    readyToApply: bool = False
    skipReason: str | None = None


class ApplyPlan(BaseModel):
    """Full apply plan."""

    schemaVersion: int = Field(default=1)
    generatedAt: str = Field(default_factory=lambda: datetime.now().isoformat())
    source: str = "local"
    kanbanRoot: str = ""
    projectsJsonPath: str = ""
    cardUpdatesPath: str = ""
    projectsJsonHashBefore: str = ""
    cardUpdatesHashBefore: str = ""
    planItems: list[PlanItem] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=lambda: {
        "totalEligible": 0, "readyToApply": 0,
        "conflicts": 0, "skipped": 0, "smokeSkipped": 0,
    })


class AuditEntry(BaseModel):
    """Audit entry appended to card_updates.jsonl after each applied item."""

    schemaVersion: int = Field(default=1)
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    source: str = "SAMI Kanban Coach"
    action: str = "approved_draft_applied"
    projectId: str = ""
    title: str = ""
    draftId: str = ""
    applyId: str = ""
    approvedBy: str = "Brian"
    fieldsChanged: list[str] = Field(default_factory=list)
    previous: dict[str, Any] = Field(default_factory=dict)
    new: dict[str, Any] = Field(default_factory=dict)
    sourceEmailKeys: list[str] = Field(default_factory=list)
    evidence: list[ApplyEvidence] = Field(default_factory=list)
    sourceCardHash: str = ""
    appliedCardHashBefore: str = ""
    appliedCardHashAfter: str = ""
    humanApproved: bool = True


class ApplyResult(BaseModel):
    """Result of one applied item."""

    schemaVersion: int = Field(default=1)
    applyId: str = ""
    draftId: str = ""
    projectId: str = ""
    title: str = ""
    status: str = "applied|conflict|skipped|error"
    message: str = ""
    sourceCardHash: str = ""
    appliedCardHashBefore: str = ""
    appliedCardHashAfter: str = ""


class ApplyRunSummary(BaseModel):
    """Summary of a full apply run."""

    schemaVersion: int = Field(default=1)
    generatedAt: str = Field(default_factory=lambda: datetime.now().isoformat())
    mode: str = "dry_run|real_write"
    planItemsTotal: int = 0
    applied: int = 0
    conflicts: int = 0
    skipped: int = 0
    errors: int = 0
    backupPath: str = ""
    backupSuccess: bool = False
    projectsJsonHashBefore: str = ""
    projectsJsonHashAfter: str = ""
    cardUpdatesHashBefore: str = ""
    cardUpdatesHashAfter: str = ""
    localKanbanApplyEnabled: bool = False
