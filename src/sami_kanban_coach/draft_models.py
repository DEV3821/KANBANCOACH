"""Pydantic models for Phase 3 draft generation."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class DraftEvidence(BaseModel):
    """One piece of evidence used in a draft decision."""

    model_config = {"populate_by_name": True}

    type: str = "email"
    messageKey: str = ""
    subject: str = ""
    from_: str = Field(default="", alias="from")
    receivedAt: str = ""
    summary: str = ""


class CardUpdateDraft(BaseModel):
    """A draft card-update suggestion produced by the comparison engine."""

    schemaVersion: int = Field(default=1)
    generatedAt: str = Field(default_factory=lambda: datetime.now().isoformat())
    projectId: str = ""
    title: str = ""
    decision: str = "needs_review"
    confidence: float = 0.0
    currentCardState: str = ""
    currentNextAction: str = ""
    newEvidenceState: str = ""
    suggestedCurrentState: str = ""
    suggestedNextAction: str = ""
    suggestedStatus: str = ""
    suggestedRisk: str = ""
    reasonForDecision: str = ""
    evidence: list[DraftEvidence] = Field(default_factory=list)
    matchedSignals: list[dict[str, Any]] = Field(default_factory=list)
    requiresHumanApproval: bool = True
    sourceCardHash: str = ""
    sourceEmailKeys: list[str] = Field(default_factory=list)
    generatedBy: str = "ollama|fallback"


class DraftRunSummary(BaseModel):
    """Summary of a draft generation run."""

    schemaVersion: int = Field(default=1)
    generatedAt: str = Field(default_factory=lambda: datetime.now().isoformat())
    scannedMatches: int = 0
    cardsConsidered: int = 0
    materialUpdates: int = 0
    possibleUpdates: int = 0
    noChange: int = 0
    needsReview: int = 0
    possibleNewProject: int = 0
    ollamaAvailable: bool = False
    modelUsed: str = ""
    runtimeSeconds: float = 0.0
