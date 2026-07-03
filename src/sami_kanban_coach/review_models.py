"""Pydantic models for Phase 4A draft review queue."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ReviewEvidence(BaseModel):
    """Evidence item in a review record."""

    type: str = "email"
    messageKey: str = ""
    subject: str = ""
    from_: str = Field(default="", alias="from")
    receivedAt: str = ""
    summary: str = ""


class ReviewItem(BaseModel):
    """A single item in the review queue."""

    schemaVersion: int = Field(default=1)
    draftId: str = ""
    createdAt: str = Field(default_factory=lambda: datetime.now().isoformat())
    sourceDraftPath: str = ""
    projectId: str = ""
    title: str = ""
    decision: str = ""
    confidence: float = 0.0
    currentCardState: str = ""
    currentNextAction: str = ""
    suggestedCurrentState: str = ""
    suggestedNextAction: str = ""
    suggestedStatus: str = ""
    suggestedRisk: str = ""
    reasonForDecision: str = ""
    evidence: list[ReviewEvidence] = Field(default_factory=list)
    sourceCardHash: str = ""
    sourceEmailKeys: list[str] = Field(default_factory=list)
    reviewStatus: str = "pending"
    reviewedAt: str | None = None
    reviewedBy: str | None = None
    reviewNotes: list[str] = Field(default_factory=list)


class ApprovedDraft(BaseModel):
    """An approved draft ready for eventual apply."""

    schemaVersion: int = Field(default=1)
    draftId: str = ""
    approvedAt: str = Field(default_factory=lambda: datetime.now().isoformat())
    approvedBy: str = "Brian"
    projectId: str = ""
    title: str = ""
    approvedCurrentState: str = ""
    approvedNextAction: str = ""
    approvedStatus: str = ""
    approvedRisk: str = ""
    sourceCardHash: str = ""
    sourceEmailKeys: list[str] = Field(default_factory=list)
    evidence: list[ReviewEvidence] = Field(default_factory=list)
    readyForApply: bool = True
    appliedToKanban: bool = False


class EditedDraft(BaseModel):
    """A draft that was edited before approval."""

    schemaVersion: int = Field(default=1)
    draftId: str = ""
    editedAt: str = Field(default_factory=lambda: datetime.now().isoformat())
    editedBy: str = "Brian"
    projectId: str = ""
    title: str = ""
    approvedCurrentState: str = ""
    approvedNextAction: str = ""
    approvedStatus: str = ""
    approvedRisk: str = ""
    originalSuggestedCurrentState: str = ""
    originalSuggestedNextAction: str = ""
    originalSuggestedStatus: str = ""
    originalSuggestedRisk: str = ""
    editReason: str = ""
    sourceCardHash: str = ""
    sourceEmailKeys: list[str] = Field(default_factory=list)
    evidence: list[ReviewEvidence] = Field(default_factory=list)
    readyForApply: bool = True
    appliedToKanban: bool = False


class SkippedDraft(BaseModel):
    """A draft that was skipped (not ready for Kanban update)."""

    schemaVersion: int = Field(default=1)
    draftId: str = ""
    skippedAt: str = Field(default_factory=lambda: datetime.now().isoformat())
    skippedBy: str = "Brian"
    projectId: str = ""
    title: str = ""
    reason: str = ""
    sourceEmailKeys: list[str] = Field(default_factory=list)


class ReviewRunSummary(BaseModel):
    """Summary of review queue build."""

    schemaVersion: int = Field(default=1)
    generatedAt: str = Field(default_factory=lambda: datetime.now().isoformat())
    totalQueueItems: int = 0
    pending: int = 0
    approved: int = 0
    edited: int = 0
    skipped: int = 0
    needsReview: int = 0
    allowKanbanApply: bool = False
