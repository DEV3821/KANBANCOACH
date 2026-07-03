"""Review queue engine for Phase 4A.

Builds and manages the local draft review queue.
Never writes to Kanban source, Team ESMI, or Outlook.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .draft_engine import load_draft_records
from .logging_setup import setup_logging
from .path_safety import assert_not_forbidden
from .review_models import (
    ApprovedDraft,
    EditedDraft,
    ReviewEvidence,
    ReviewItem,
    ReviewRunSummary,
    SkippedDraft,
)

logger = setup_logging(Path("runtime/email_recall/logs"))


def _make_draft_id(project_id: str, card_hash: str, decision: str) -> str:
    """Create a deterministic draftId from projectId + hash + decision."""
    raw = f"{project_id}|{card_hash}|{decision}"
    h = hashlib.sha256(raw.encode()).hexdigest()[:12]
    return f"draft-{h}"


def _evidence_from_draft(draft: dict[str, Any]) -> list[ReviewEvidence]:
    """Convert draft evidence to ReviewEvidence list."""
    results = []
    for ev in draft.get("evidence", []) or []:
        results.append(ReviewEvidence(
            type=ev.get("type", "email"),
            messageKey=ev.get("messageKey", ""),
            subject=ev.get("subject", ""),
            from_=ev.get("from_", "") or ev.get("from", ""),
            receivedAt=ev.get("receivedAt", ""),
            summary=ev.get("summary", ""),
        ))
    return results


def _load_jsonl_list(path: Path) -> list[dict[str, Any]]:
    """Load a JSONL file into a list of dicts."""
    if not path.exists():
        return []
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _save_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    """Save a list of dicts as JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    assert_not_forbidden(path)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


def _load_records_as_dict(path: Path, key_field: str = "draftId") -> dict[str, dict[str, Any]]:
    """Load JSONL into a dict keyed by key_field."""
    return {r.get(key_field, ""): r for r in _load_jsonl_list(path) if r.get(key_field)}


# ---------------------------------------------------------------------------
# Build review queue
# ---------------------------------------------------------------------------
def build_review_queue(
    drafts_root: Path,
    review_root: Path,
    settings: Any,
) -> ReviewRunSummary:
    """Build the review queue from Phase 3 draft outputs.

    Reads card_update_drafts.jsonl and needs_review_decisions.jsonl.
    Skips no_change decisions.
    Does not duplicate existing review decisions.
    """
    review_data = review_root / "data"
    review_data.mkdir(parents=True, exist_ok=True)
    assert_not_forbidden(review_data)

    # Source draft files
    updates_path = drafts_root / "data" / "card_update_drafts.jsonl"
    needs_path = drafts_root / "data" / "needs_review_decisions.jsonl"

    # Load existing review decisions (for dedupe)
    existing_approved = _load_records_as_dict(review_data / "approved_drafts.jsonl", "draftId")
    existing_edited = _load_records_as_dict(review_data / "edited_drafts.jsonl", "draftId")
    existing_skipped = _load_records_as_dict(review_data / "skipped_drafts.jsonl", "draftId")
    existing_queue = _load_records_as_dict(review_data / "review_queue.jsonl", "draftId")
    existing_needs_review = _load_records_as_dict(review_data / "needs_review_queue.jsonl", "draftId")

    # Collect all existing processed draftIds
    all_existing: set[str] = set()
    for d in [existing_approved, existing_edited, existing_skipped, existing_queue, existing_needs_review]:
        all_existing.update(d.keys())

    # Track new items
    queue_items: list[ReviewItem] = []
    needs_review_items: list[ReviewItem] = []

    # Process card_update_drafts.jsonl (material_update + possible_update)
    for draft in _load_jsonl_list(updates_path):
        decision = draft.get("decision", "")
        if decision not in ("material_update", "possible_update"):
            continue

        draft_id = _make_draft_id(
            draft.get("projectId", ""),
            draft.get("sourceCardHash", ""),
            decision,
        )
        if draft_id in all_existing:
            continue

        item = ReviewItem(
            draftId=draft_id,
            sourceDraftPath=str(updates_path),
            projectId=draft.get("projectId", ""),
            title=draft.get("title", ""),
            decision=decision,
            confidence=draft.get("confidence", 0.0) or 0.0,
            currentCardState=draft.get("currentCardState", "") or "",
            currentNextAction=draft.get("currentNextAction", "") or "",
            suggestedCurrentState=draft.get("suggestedCurrentState", "") or "",
            suggestedNextAction=draft.get("suggestedNextAction", "") or "",
            suggestedStatus=draft.get("suggestedStatus", "") or "",
            suggestedRisk=draft.get("suggestedRisk", "") or "",
            reasonForDecision=draft.get("reasonForDecision", "") or "",
            evidence=_evidence_from_draft(draft),
            sourceCardHash=draft.get("sourceCardHash", ""),
            sourceEmailKeys=draft.get("sourceEmailKeys", []) or [],
            reviewStatus="pending",
        )
        queue_items.append(item)

    # Process needs_review_decisions.jsonl
    for draft in _load_jsonl_list(needs_path):
        draft_id = _make_draft_id(
            draft.get("projectId", ""),
            draft.get("sourceCardHash", ""),
            "needs_review",
        )
        if draft_id in all_existing:
            continue

        item = ReviewItem(
            draftId=draft_id,
            sourceDraftPath=str(needs_path),
            projectId=draft.get("projectId", ""),
            title=draft.get("title", ""),
            decision="needs_review",
            confidence=draft.get("confidence", 0.0) or 0.0,
            currentCardState=draft.get("currentCardState", "") or "",
            currentNextAction=draft.get("currentNextAction", "") or "",
            suggestedCurrentState=draft.get("suggestedCurrentState", "") or "",
            suggestedNextAction=draft.get("suggestedNextAction", "") or "",
            suggestedStatus=draft.get("suggestedStatus", "") or "",
            suggestedRisk=draft.get("suggestedRisk", "") or "",
            reasonForDecision=draft.get("reasonForDecision", "") or "",
            evidence=_evidence_from_draft(draft),
            sourceCardHash=draft.get("sourceCardHash", ""),
            sourceEmailKeys=draft.get("sourceEmailKeys", []) or [],
            reviewStatus="pending",
        )
        needs_review_items.append(item)

    # Merge with existing queue (preserve old items)
    all_queue: list[ReviewItem] = []
    reviewed_ids = set()

    # Re-load existing queue items (with their review statuses)
    for old in existing_queue.values():
        item = ReviewItem(**old)
        all_queue.append(item)
        reviewed_ids.add(item.draftId)
    for old in existing_needs_review.values():
        item = ReviewItem(**old)
        # Add to needs_review_queue
        pass

    # Add new items
    all_queue.extend(queue_items)

    # Needs review
    all_needs = list(existing_needs_review.values())
    all_needs.extend([i.model_dump(exclude_none=True) for i in needs_review_items])

    # Write outputs
    _save_jsonl(review_data / "review_queue.jsonl", [i.model_dump(exclude_none=True) for i in all_queue])
    _save_jsonl(review_data / "needs_review_queue.jsonl", all_needs)

    # Summary
    approved = len(existing_approved)
    edited = len(existing_edited)
    skipped = len(existing_skipped)
    pending = len([i for i in all_queue if i.reviewStatus == "pending"])
    reviewed_count = approved + edited + skipped

    summary = ReviewRunSummary(
        totalQueueItems=len(all_queue),
        pending=pending,
        approved=approved,
        edited=edited,
        skipped=skipped,
        needsReview=len(all_needs),
        allowKanbanApply=settings.allow_kanban_apply,
    )

    summary_path = review_data / "review_run_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary.model_dump(exclude_none=True), f, indent=2, ensure_ascii=False)

    logger.info(
        "Review queue built: %d total (%d pending, %d approved, %d edited, %d skipped, %d needs-review)",
        summary.totalQueueItems, pending, approved, edited, skipped, summary.needsReview,
    )

    return summary


# ---------------------------------------------------------------------------
# Queue readers
# ---------------------------------------------------------------------------

def load_review_queue(review_root: Path) -> list[dict[str, Any]]:
    return _load_jsonl_list(review_root / "data" / "review_queue.jsonl")


def load_needs_review_queue(review_root: Path) -> list[dict[str, Any]]:
    return _load_jsonl_list(review_root / "data" / "needs_review_queue.jsonl")


def load_approved_drafts(review_root: Path) -> list[dict[str, Any]]:
    return _load_jsonl_list(review_root / "data" / "approved_drafts.jsonl")


def load_edited_drafts(review_root: Path) -> list[dict[str, Any]]:
    return _load_jsonl_list(review_root / "data" / "edited_drafts.jsonl")


def load_skipped_drafts(review_root: Path) -> list[dict[str, Any]]:
    return _load_jsonl_list(review_root / "data" / "skipped_drafts.jsonl")


def load_review_summary(review_root: Path) -> dict[str, Any] | None:
    path = review_root / "data" / "review_run_summary.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Review actions
# ---------------------------------------------------------------------------

def _find_queue_item(draft_id: str, queue: list[dict[str, Any]]) -> dict[str, Any] | None:
    for item in queue:
        if item.get("draftId") == draft_id:
            return item
    return None


def _update_queue_status(review_root: Path, draft_id: str, status: str) -> None:
    """Update the reviewStatus of an item in the queue by draftId."""
    queue = load_review_queue(review_root)
    needs_queue = load_needs_review_queue(review_root)
    updated = False

    for q in [queue, needs_queue]:
        for item in q:
            if item.get("draftId") == draft_id:
                item["reviewStatus"] = status
                item["reviewedAt"] = datetime.now().isoformat()
                item["reviewedBy"] = "Brian"
                updated = True
                break

    if updated:
        _save_jsonl(review_root / "data" / "review_queue.jsonl", queue)
        _save_jsonl(review_root / "data" / "needs_review_queue.jsonl", needs_queue)


def approve_draft(
    review_root: Path,
    draft_id: str,
    approved_by: str = "Brian",
) -> ApprovedDraft | None:
    """Approve a draft. Writes approved_drafts.jsonl. Does NOT write Kanban."""
    queue = load_review_queue(review_root)
    item = _find_queue_item(draft_id, queue)

    if not item:
        # Also check needs_review queue
        nq = load_needs_review_queue(review_root)
        item = _find_queue_item(draft_id, nq)

    if not item:
        logger.warning("Draft not found in any queue: %s", draft_id)
        return None

    approved = ApprovedDraft(
        draftId=draft_id,
        approvedBy=approved_by,
        projectId=item.get("projectId", ""),
        title=item.get("title", ""),
        approvedCurrentState=item.get("suggestedCurrentState", "") or "",
        approvedNextAction=item.get("suggestedNextAction", "") or "",
        approvedStatus=item.get("suggestedStatus", "") or "",
        approvedRisk=item.get("suggestedRisk", "") or "",
        sourceCardHash=item.get("sourceCardHash", ""),
        sourceEmailKeys=item.get("sourceEmailKeys", []) or [],
        evidence=[ReviewEvidence(**ev) for ev in (item.get("evidence", []) or [])],
        readyForApply=True,
        appliedToKanban=False,
    )

    # Append to approved_drafts.jsonl
    path = review_root / "data" / "approved_drafts.jsonl"
    existing = _load_jsonl_list(path)
    existing.append(approved.model_dump(exclude_none=True))
    _save_jsonl(path, existing)

    # Update queue status
    _update_queue_status(review_root, draft_id, "approved")
    logger.info("Draft approved: %s — %s", draft_id, item.get("title", ""))
    return approved


def skip_draft(
    review_root: Path,
    draft_id: str,
    reason: str = "",
    skipped_by: str = "Brian",
) -> SkippedDraft | None:
    """Skip a draft. Writes skipped_drafts.jsonl. Does NOT write Kanban."""
    queue = load_review_queue(review_root)
    item = _find_queue_item(draft_id, queue)

    if not item:
        nq = load_needs_review_queue(review_root)
        item = _find_queue_item(draft_id, nq)

    if not item:
        logger.warning("Draft not found: %s", draft_id)
        return None

    skipped = SkippedDraft(
        draftId=draft_id,
        skippedBy=skipped_by,
        projectId=item.get("projectId", ""),
        title=item.get("title", ""),
        reason=reason,
        sourceEmailKeys=item.get("sourceEmailKeys", []) or [],
    )

    path = review_root / "data" / "skipped_drafts.jsonl"
    existing = _load_jsonl_list(path)
    existing.append(skipped.model_dump(exclude_none=True))
    _save_jsonl(path, existing)

    _update_queue_status(review_root, draft_id, "skipped")
    logger.info("Draft skipped: %s — %s", draft_id, reason)
    return skipped


def edit_draft(
    review_root: Path,
    draft_id: str,
    edited_by: str = "Brian",
    **kwargs: Any,
) -> EditedDraft | None:
    """Edit a draft's suggested fields. Writes edited_drafts.jsonl. Does NOT write Kanban."""
    queue = load_review_queue(review_root)
    item = _find_queue_item(draft_id, queue)

    if not item:
        nq = load_needs_review_queue(review_root)
        item = _find_queue_item(draft_id, nq)

    if not item:
        logger.warning("Draft not found: %s", draft_id)
        return None

    edited = EditedDraft(
        draftId=draft_id,
        editedBy=edited_by,
        projectId=item.get("projectId", ""),
        title=item.get("title", ""),
        approvedCurrentState=kwargs.get("currentState", item.get("suggestedCurrentState", "") or ""),
        approvedNextAction=kwargs.get("nextAction", item.get("suggestedNextAction", "") or ""),
        approvedStatus=kwargs.get("status", item.get("suggestedStatus", "") or ""),
        approvedRisk=kwargs.get("risk", item.get("suggestedRisk", "") or ""),
        originalSuggestedCurrentState=item.get("suggestedCurrentState", "") or "",
        originalSuggestedNextAction=item.get("suggestedNextAction", "") or "",
        originalSuggestedStatus=item.get("suggestedStatus", "") or "",
        originalSuggestedRisk=item.get("suggestedRisk", "") or "",
        editReason=kwargs.get("editReason", ""),
        sourceCardHash=item.get("sourceCardHash", ""),
        sourceEmailKeys=item.get("sourceEmailKeys", []) or [],
        evidence=[ReviewEvidence(**ev) for ev in (item.get("evidence", []) or [])],
        readyForApply=True,
        appliedToKanban=False,
    )

    path = review_root / "data" / "edited_drafts.jsonl"
    existing = _load_jsonl_list(path)
    existing.append(edited.model_dump(exclude_none=True))
    _save_jsonl(path, existing)

    _update_queue_status(review_root, draft_id, "edited")
    logger.info("Draft edited: %s — %s", draft_id, item.get("title", ""))
    return edited
