"""Pydantic models for email capture data in SAMI Kanban Coach Phase 0."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AttachmentInfo(BaseModel):
    """Metadata about an email attachment."""

    index: int
    name: str
    extension: str
    size: int
    savedPath: str | None = None


class DedupeKeys(BaseModel):
    """All deduplication keys for an email."""

    messageKey: str
    internetMessageId: str | None = None
    entryId: str | None = None
    contentFingerprint: str


class CapturedEmail(BaseModel):
    """Schema for an email captured from Outlook and stored in JSONL."""

    schemaVersion: int = Field(default=1)
    source: str = Field(default="outlook_com")
    captureMode: str = Field(
        default="selected",
        description="One of: live, poll, selected, folder",
    )
    messageKey: str = Field(
        description="Primary dedup key — InternetMessageID, EntryID, or SHA256 hash.",
    )
    contentFingerprint: str = Field(
        default="",
        description="SHA256 of normalized subject+sender+timestamps+body+attachments. "
        "Stable across folder moves/copies where EntryID changes.",
    )
    fingerprintInputs: str = Field(
        default="",
        description="The normalized input string used to compute contentFingerprint "
        "(for debugging transparency, stored as empty in JSONL via exclude_none).",
    )
    dedupeKeys: DedupeKeys | None = None
    internetMessageId: str | None = None
    entryId: str | None = None
    conversationId: str | None = None
    subject: str = ""
    senderName: str = ""
    senderEmail: str = ""
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    receivedAt: str | None = None
    sentOn: str | None = None
    capturedAt: str = Field(default_factory=lambda: datetime.now().isoformat())
    sourceFolder: str = ""
    bodyTextPath: str | None = None
    bodyHtmlPath: str | None = None
    headersPath: str | None = None
    msgPath: str | None = None
    attachments: list[AttachmentInfo] = Field(default_factory=list)
    bodyPreview: str = ""
    bodyCharCount: int = 0
    evidenceFolder: str | None = None
    processed: bool = False
    kanbanLinked: bool = False
    notes: list[str] = Field(default_factory=list)


class ProcessedIDs(BaseModel):
    """Persistent store of processed message keys and content fingerprints for deduplication.

    Supports backward compatibility: if only ``processed`` exists (old schema),
    ``fingerprints`` defaults to empty.
    """

    processed: list[str] = Field(default_factory=list)
    fingerprints: list[str] = Field(default_factory=list)

    def has(self, key: str) -> bool:
        """Check if a messageKey has been processed."""
        return key in self.processed

    def has_fingerprint(self, fp: str) -> bool:
        """Check if a contentFingerprint has been processed."""
        return fp in self.fingerprints

    def add(self, key: str) -> None:
        """Add a messageKey (idempotent)."""
        if key not in self.processed:
            self.processed.append(key)

    def add_fingerprint(self, fp: str) -> None:
        """Add a contentFingerprint (idempotent)."""
        if fp not in self.fingerprints:
            self.fingerprints.append(fp)
