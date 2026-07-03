"""Local Kanban apply engine for Phase 4B.

Builds apply plans, checks hash integrity, creates backups,
applies approved/edited drafts to local Kanban, and appends audit records.
Team ESMI writes are forbidden. Smoke-test drafts are ignored by default.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from .apply_models import (
    ApplyEvidence,
    ApplyPlan,
    ApplyResult,
    ApplyRunSummary,
    AuditEntry,
    PlanItem,
)
from .kanban_reader import file_hash, find_projects_json, read_projects_json
from .logging_setup import setup_logging
from .path_safety import assert_not_forbidden, is_forbidden_path

logger = setup_logging(Path("runtime/email_recall/logs"))

# Fields we allow applying
ALLOWED_FIELDS = {"currentState", "nextAction", "status", "risk"}

# Field mapping from draft field names to projects.json field names
FIELD_MAP = {
    "currentState": "context",
    "nextAction": "nextAction",
    "status": "status",
    "risk": "riskColour",
}


def _build_card_hash(card: dict[str, Any]) -> str:
    """Build a reproducible hash from a card for conflict detection."""
    fields = [
        str(card.get("id", "")),
        str(card.get("title", "")),
        str(card.get("status", "")),
        str(card.get("riskColour", "")),
        str(card.get("context", "")),
        str(card.get("nextAction", "")),
        str(card.get("projectLead", "")),
        str(card.get("owner", "")),
    ]
    raw = "|".join(fields)
    return hashlib.sha256(raw.encode()).hexdigest()


def _load_review_records(review_root: Path, filename: str) -> list[dict[str, Any]]:
    """Load approved or edited drafts from review data."""
    path = review_root / "data" / filename
    if not path.exists():
        return []
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _load_projects_json(kanban_root: Path) -> tuple[list[dict[str, Any]], str]:
    """Load current projects.json and return (projects_list, hash)."""
    pj_path, _ = find_projects_json(kanban_root)
    if not pj_path or not pj_path.exists():
        return [], ""
    projects, _, pj_hash, _ = read_projects_json(pj_path)
    return projects, pj_hash


def _find_card_by_id(projects: list[dict[str, Any]], project_id: str) -> dict[str, Any] | None:
    """Find a card in projects.json by id/projectId field."""
    for p in projects:
        if p.get("id") == project_id or p.get("projectId") == project_id:
            return p
    return None


def _load_card_updates_path(kanban_root: Path) -> Path:
    """Get path to card_updates.jsonl."""
    return kanban_root / "data" / "card_updates.jsonl"


def _load_card_updates_hash(kanban_root: Path) -> str:
    """Get hash of card_updates.jsonl."""
    path = _load_card_updates_path(kanban_root)
    return file_hash(path)


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------
def _create_backup(kanban_root: Path, backup_dir: Path) -> tuple[bool, str, dict[str, Any]]:
    """Create a timestamped backup of Kanban source files.

    Returns (success, backup_path_str, manifest_dict).
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bdir = backup_dir / ts
    bdir.mkdir(parents=True, exist_ok=True)
    assert_not_forbidden(bdir)

    manifest: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "backupPath": str(bdir),
        "command": "apply-approved-local",
        "files": [],
    }

    try:
        # Backup projects.json
        pj_path, _ = find_projects_json(kanban_root)
        if pj_path and pj_path.exists():
            dest = bdir / "projects.json"
            shutil.copy2(str(pj_path), str(dest))
            h = file_hash(pj_path)
            manifest["files"].append({
                "original": str(pj_path), "backup": str(dest),
                "hash": h, "size": pj_path.stat().st_size,
            })
            manifest["projectsJsonHash"] = h

        # Backup card_updates.jsonl
        cu_path = _load_card_updates_path(kanban_root)
        if cu_path.exists():
            dest = bdir / "card_updates.jsonl"
            shutil.copy2(str(cu_path), str(dest))
            h = file_hash(cu_path)
            manifest["files"].append({
                "original": str(cu_path), "backup": str(dest),
                "hash": h, "size": cu_path.stat().st_size,
            })
            manifest["cardUpdatesHash"] = h

        # Write manifest
        manifest_path = bdir / "backup_manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

        logger.info("Backup created: %s (%d files)", bdir, len(manifest["files"]))
        return True, str(bdir), manifest

    except Exception as e:
        logger.error("Backup failed: %s", e)
        return False, "", manifest


# ---------------------------------------------------------------------------
# Build apply plan
# ---------------------------------------------------------------------------
def build_apply_plan(settings: Any) -> ApplyPlan:
    """Build a plan from approved/edited drafts vs current kanban state.

    Does not modify anything. Reads-only.
    """
    review_root = settings.review_path()
    kanban_root = settings.kanban_local_path()
    apply_root = settings.apply_path()
    apply_root.mkdir(parents=True, exist_ok=True)
    (apply_root / "data").mkdir(parents=True, exist_ok=True)

    # Load current state
    projects, pj_hash = _load_projects_json(kanban_root)
    cu_hash = _load_card_updates_hash(kanban_root)
    pj_path, _ = find_projects_json(kanban_root)

    # Load approved and edited drafts
    approved = _load_review_records(review_root, "approved_drafts.jsonl")
    edited = _load_review_records(review_root, "edited_drafts.jsonl")

    # Merge: edited overrides approved for same draftId
    drafts_by_id: dict[str, dict[str, Any]] = {}
    for d in approved:
        if d.get("readyForApply") and not d.get("appliedToKanban"):
            drafts_by_id[d["draftId"]] = {**d, "sourceType": "approved"}
    for d in edited:
        if d.get("readyForApply") and not d.get("appliedToKanban"):
            existing = drafts_by_id.get(d["draftId"], {})
            # edited has different field names
            mapped = {
                "draftId": d.get("draftId"),
                "projectId": d.get("projectId"),
                "title": d.get("title"),
                "sourceType": "edited",
                "approvedCurrentState": d.get("approvedCurrentState", d.get("approvedCurrentState", "")),
                "approvedNextAction": d.get("approvedNextAction", d.get("approvedNextAction", "")),
                "approvedStatus": d.get("approvedStatus", d.get("approvedStatus", "")),
                "approvedRisk": d.get("approvedRisk", d.get("approvedRisk", "")),
                "sourceCardHash": d.get("sourceCardHash", existing.get("sourceCardHash", "")),
                "sourceEmailKeys": d.get("sourceEmailKeys", existing.get("sourceEmailKeys", [])),
                "evidence": d.get("evidence", existing.get("evidence", [])),
                "generatedBy": d.get("generatedBy", existing.get("generatedBy", "")),
            }
            drafts_by_id[d["draftId"]] = {**existing, **mapped}

    plan_items: list[PlanItem] = []
    counts = {"totalEligible": 0, "readyToApply": 0, "conflicts": 0, "skipped": 0, "smokeSkipped": 0}

    for draft_id, draft in drafts_by_id.items():
        counts["totalEligible"] += 1
        pid = draft.get("projectId", "")
        title = draft.get("title", "")
        source_type = draft.get("sourceType", "approved")
        card_hash = draft.get("sourceCardHash", "")
        gen_by = draft.get("generatedBy", "")

        # Skip smoke test drafts
        is_smoke = is_smoke_item(draft)
        if is_smoke and settings.ignore_smoke_test_drafts:
            counts["smokeSkipped"] += 1
            plan_items.append(PlanItem(
                applyId=f"apply-{hashlib.sha256(draft_id.encode()).hexdigest()[:12]}",
                draftId=draft_id, projectId=pid, title=title,
                sourceType=source_type, sourceCardHash=card_hash,
                readyToApply=False, skipReason="Smoke test draft — ignored by default",
            ))
            continue

        # Find card in current projects.json
        card = _find_card_by_id(projects, pid)
        if card is None:
            counts["skipped"] += 1
            plan_items.append(PlanItem(
                applyId=f"apply-{hashlib.sha256(draft_id.encode()).hexdigest()[:12]}",
                draftId=draft_id, projectId=pid, title=title,
                sourceType=source_type, sourceCardHash=card_hash,
                readyToApply=False, skipReason="Project not found in kanban",
            ))
            continue

        # Compute current live card content hash
        live_content_hash = _build_card_hash(card)

        # Hash status: match if sourceCardHash matches either the current
        # file-level hash (Phase 1 sourceHash) OR the per-card content hash.
        # sourceCardHash stored in approved/edited drafts is the Phase 1
        # projects.json file-level hash (same across all cards).
        projects_file_hash = pj_hash
        hash_status = "match" if (
            card_hash and (card_hash == projects_file_hash or card_hash == live_content_hash)
        ) else "conflict"

        if not card_hash:
            hash_status = "missing"

        # Handle conflicts and missing hashes
        if hash_status in ("conflict", "missing") or not card_hash:
            counts["conflicts"] += 1 if hash_status == "conflict" else 0
            counts["skipped"] += 1 if hash_status == "missing" else 0
            plan_items.append(PlanItem(
                applyId=f"apply-{hashlib.sha256(draft_id.encode()).hexdigest()[:12]}",
                draftId=draft_id, projectId=pid, title=title,
                sourceType=source_type, sourceCardHash=card_hash,
                currentLiveCardHash=live_content_hash, hashStatus=hash_status,
                readyToApply=False,
                skipReason=("Card hash conflict — card changed since review" if hash_status == "conflict"
                            else "No sourceCardHash in draft"),
            ))
            continue

        # Ready to apply
        counts["readyToApply"] += 1
        evidence_list = []
        for ev in (draft.get("evidence", []) or []):
            evidence_list.append(ApplyEvidence(
                type=ev.get("type", "email"),
                messageKey=ev.get("messageKey", ""),
                subject=ev.get("subject", ""),
                from_=ev.get("from") or ev.get("from_", ""),
                receivedAt=ev.get("receivedAt", ""),
                summary=ev.get("summary", ""),
            ))

        plan_items.append(PlanItem(
            applyId=f"apply-{hashlib.sha256((draft_id+live_content_hash).encode()).hexdigest()[:12]}",
            draftId=draft_id, projectId=pid, title=title,
            sourceType=source_type, sourceCardHash=card_hash,
            currentLiveCardHash=live_content_hash, hashStatus="match",
            approvedCurrentState=draft.get("approvedCurrentState", "") or "",
            approvedNextAction=draft.get("approvedNextAction", "") or "",
            approvedStatus=draft.get("approvedStatus", "") or "",
            approvedRisk=draft.get("approvedRisk", "") or "",
            sourceEmailKeys=draft.get("sourceEmailKeys", []) or [],
            evidence=evidence_list,
            readyToApply=True,
        ))

    plan = ApplyPlan(
        kanbanRoot=str(kanban_root),
        projectsJsonPath=str(pj_path) if pj_path else "",
        cardUpdatesPath=str(_load_card_updates_path(kanban_root)),
        projectsJsonHashBefore=pj_hash,
        cardUpdatesHashBefore=cu_hash,
        planItems=plan_items,
        counts=counts,
    )

    # Write plan
    plan_path = apply_root / "data" / "apply_plan.json"
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan.model_dump(exclude_none=True), f, indent=2, ensure_ascii=False)

    logger.info("Apply plan built: %d items (%d ready, %d conflicts, %d skipped, %d smoke)",
                counts["totalEligible"], counts["readyToApply"],
                counts["conflicts"], counts["skipped"], counts["smokeSkipped"])
    return plan


# ---------------------------------------------------------------------------
# Apply (dry-run and real)
# ---------------------------------------------------------------------------
def apply_approved_drafts(
    settings: Any,
    dry_run: bool = True,
    allow_smoke_test: bool = False,
) -> ApplyRunSummary:
    """Apply approved/edited drafts to the local Kanban repo.

    Args:
        settings: App settings.
        dry_run: If True, read-only — no writes to Kanban source.
        allow_smoke_test: If True, allow smoke test drafts.

    Returns:
        ApplyRunSummary with results.
    """
    kanban_root = settings.kanban_local_path()
    apply_root = settings.apply_path()
    review_root = settings.review_path()
    (apply_root / "data").mkdir(parents=True, exist_ok=True)
    (apply_root / "backups").mkdir(parents=True, exist_ok=True)

    # Guards
    if not dry_run:
        if not settings.local_kanban_apply_enabled and not settings.allow_kanban_apply:
            raise RuntimeError(
                "Local Kanban apply is disabled in settings.json. "
                "Set local_kanban_apply_enabled=true only when ready."
            )
        if not settings.apply_requires_explicit_flag:
            logger.warning("apply_requires_explicit_flag=false -- proceeding without confirmed flags.")

    # Build or load plan
    plan = build_apply_plan(settings)

    mode = "dry_run" if dry_run else "real_write"
    results: list[ApplyResult] = []
    before_pj_hash = plan.projectsJsonHashBefore
    before_cu_hash = plan.cardUpdatesHashBefore
    backup_path_str = ""
    backup_ok = False

    if not dry_run:
        # Backup
        if settings.backup_before_apply:
            success, bp, manifest = _create_backup(kanban_root, apply_root / "backups")
            backup_path_str = bp
            backup_ok = success
            if not success:
                raise RuntimeError("Backup failed — apply aborted.")
            logger.info("Backup at: %s", bp)

        # Load projects.json for editing
        projects, current_pj_hash = _load_projects_json(kanban_root)
        cu_path = _load_card_updates_path(kanban_root)

        # Apply each ready item
        for item in plan.planItems:
            if not item.readyToApply:
                results.append(ApplyResult(
                    applyId=item.applyId, draftId=item.draftId,
                    projectId=item.projectId, title=item.title,
                    status="skipped", message=item.skipReason or "Not ready",
                ))
                continue

            try:
                # Re-find card (re-read in case plan was stale)
                card = _find_card_by_id(projects, item.projectId)
                if card is None:
                    results.append(ApplyResult(
                        applyId=item.applyId, draftId=item.draftId,
                        projectId=item.projectId, title=item.title,
                        status="skipped", message="Card no longer in projects.json",
                    ))
                    continue

                # Re-verify hash
                live_hash = _build_card_hash(card)
                if live_hash != item.sourceCardHash:
                    results.append(ApplyResult(
                        applyId=item.applyId, draftId=item.draftId,
                        projectId=item.projectId, title=item.title,
                        status="conflict",
                        message="Card hash changed between plan build and apply",
                        sourceCardHash=item.sourceCardHash,
                        appliedCardHashBefore=live_hash,
                    ))
                    continue

                # Record previous values
                prev = {
                    "context": card.get("context", ""),
                    "nextAction": card.get("nextAction", ""),
                    "status": card.get("status", ""),
                    "riskColour": card.get("riskColour", ""),
                }
                new_vals = {}

                # Apply allowed fields
                if item.approvedCurrentState:
                    card["context"] = item.approvedCurrentState
                    new_vals["context"] = item.approvedCurrentState
                if item.approvedNextAction:
                    card["nextAction"] = item.approvedNextAction
                    new_vals["nextAction"] = item.approvedNextAction
                if item.approvedStatus:
                    card["status"] = item.approvedStatus
                    new_vals["status"] = item.approvedStatus
                if item.approvedRisk:
                    card["riskColour"] = item.approvedRisk
                    new_vals["riskColour"] = item.approvedRisk

                # Update lastUpdated
                card["lastUpdated"] = datetime.now().isoformat()

                changed_fields = [k for k in new_vals if prev.get(k) != new_vals.get(k)]

                # Compute new card hash
                new_hash = _build_card_hash(card)

                # --- Write projects.json atomically ---
                pj_path, _ = find_projects_json(kanban_root)
                with open(pj_path, "r", encoding="utf-8-sig") as f:
                    raw_data = json.load(f)

                # Update the card in the raw data
                if isinstance(raw_data, dict) and "projects" in raw_data:
                    for i, p in enumerate(raw_data["projects"]):
                        if p.get("id") == item.projectId:
                            raw_data["projects"][i] = card
                            break
                elif isinstance(raw_data, list):
                    for i, p in enumerate(raw_data):
                        if p.get("id") == item.projectId:
                            raw_data[i] = card
                            break

                # Atomic write
                assert_not_forbidden(pj_path)
                import tempfile, os
                fd, tmp = tempfile.mkstemp(suffix=".tmp", prefix="projects_", dir=pj_path.parent)
                with os.fdopen(fd, "w", encoding="utf-8") as tf:
                    json.dump(raw_data, tf, indent=4, ensure_ascii=False)
                    tf.flush()
                    os.fsync(fd)
                os.replace(tmp, pj_path)

                # --- Append audit record ---
                audit = AuditEntry(
                    projectId=item.projectId,
                    title=item.title,
                    draftId=item.draftId,
                    applyId=item.applyId,
                    fieldsChanged=changed_fields,
                    previous={
                        "currentState": prev.get("context", ""),
                        "nextAction": prev.get("nextAction", ""),
                        "status": prev.get("status", ""),
                        "risk": prev.get("riskColour", ""),
                    },
                    new={
                        "currentState": new_vals.get("context", ""),
                        "nextAction": new_vals.get("nextAction", ""),
                        "status": new_vals.get("status", ""),
                        "risk": new_vals.get("riskColour", ""),
                    },
                    sourceEmailKeys=item.sourceEmailKeys,
                    evidence=item.evidence,
                    sourceCardHash=item.sourceCardHash,
                    appliedCardHashBefore=live_hash,
                    appliedCardHashAfter=new_hash,
                    humanApproved=True,
                )

                with open(cu_path, "a", encoding="utf-8") as f:
                    f.write(audit.model_dump_json(exclude_none=True) + "\n")
                    f.flush()

                results.append(ApplyResult(
                    applyId=item.applyId, draftId=item.draftId,
                    projectId=item.projectId, title=item.title,
                    status="applied", message="Applied successfully",
                    sourceCardHash=item.sourceCardHash,
                    appliedCardHashBefore=live_hash,
                    appliedCardHashAfter=new_hash,
                ))
                logger.info("Applied: %s — %s", item.projectId, item.title)

            except Exception as e:
                logger.error("Apply error for %s: %s", item.projectId, e)
                results.append(ApplyResult(
                    applyId=item.applyId, draftId=item.draftId,
                    projectId=item.projectId, title=item.title,
                    status="error", message=str(e),
                ))

    # Write results
    after_pj_hash = _load_projects_json(kanban_root)[1] if not dry_run else before_pj_hash
    after_cu_hash = _load_card_updates_hash(kanban_root) if not dry_run else before_cu_hash

    summary = ApplyRunSummary(
        mode=mode,
        planItemsTotal=plan.counts.get("totalEligible", 0),
        applied=len([r for r in results if r.status == "applied"]),
        conflicts=len([r for r in results if r.status == "conflict"]),
        skipped=len([r for r in results if r.status == "skipped"]),
        errors=len([r for r in results if r.status == "error"]),
        backupPath=backup_path_str,
        backupSuccess=backup_ok,
        projectsJsonHashBefore=before_pj_hash,
        projectsJsonHashAfter=after_pj_hash,
        cardUpdatesHashBefore=before_cu_hash,
        cardUpdatesHashAfter=after_cu_hash,
        localKanbanApplyEnabled=settings.local_kanban_apply_enabled,
    )

    # Write results JSONL
    results_path = apply_root / "data" / "apply_results.jsonl"
    with open(results_path, "w", encoding="utf-8") as f:
        for r in results:
            f.write(r.model_dump_json(exclude_none=True) + "\n")

    # Write summary
    summary_path = apply_root / "data" / "apply_run_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary.model_dump(exclude_none=True), f, indent=2, ensure_ascii=False)

    # Write conflicts separately
    conflicts = [r for r in results if r.status == "conflict"]
    if conflicts:
        conf_path = apply_root / "data" / "apply_conflicts.jsonl"
        with open(conf_path, "w", encoding="utf-8") as f:
            for r in conflicts:
                f.write(r.model_dump_json(exclude_none=True) + "\n")

    # Write skipped separately
    skipped = [r for r in results if r.status == "skipped"]
    if skipped:
        skip_path = apply_root / "data" / "apply_skipped.jsonl"
        with open(skip_path, "w", encoding="utf-8") as f:
            for r in skipped:
                f.write(r.model_dump_json(exclude_none=True) + "\n")

    logger.info(
        "Apply %s complete: %d applied, %d conflicts, %d skipped, %d errors",
        mode, summary.applied, summary.conflicts, summary.skipped, summary.errors,
    )
    return summary


# ---------------------------------------------------------------------------
# Loaders for CLI
# ---------------------------------------------------------------------------
def load_apply_plan(apply_root: Path) -> dict[str, Any] | None:
    path = apply_root / "data" / "apply_plan.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_apply_summary(apply_root: Path) -> dict[str, Any] | None:
    path = apply_root / "data" / "apply_run_summary.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_apply_results(apply_root: Path) -> list[dict[str, Any]]:
    path = apply_root / "data" / "apply_results.jsonl"
    if not path.exists():
        return []
    results = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                results.append(json.loads(line))
    return results


# ---------------------------------------------------------------------------
# Phase 4C — Operator review decisions
# ---------------------------------------------------------------------------
DECISIONS_FILE = "apply_review_decisions.jsonl"


def is_smoke_item(item: dict[str, Any]) -> bool:
    """Check if an apply plan item is a smoke/test/demo draft.

    Returns True if the item was generated by a smoke test fixture.
    Detection patterns:
      - generatedBy=phase4a_smoke_test
      - SMOKE_TEST_EMAIL_KEY in sourceEmailKeys
      - Card text containing SMOKE or EDITED SMOKE
      - Operator note/reason containing 'smoke draft'
      - Card change values containing 'EDITED SMOKE'
    """
    gen_by = str(item.get("generatedBy", "") or "")
    if gen_by == "phase4a_smoke_test":
        return True

    keys = item.get("sourceEmailKeys", []) or []
    if "SMOKE_TEST_EMAIL_KEY" in keys:
        return True

    for field in ["title", "approvedCurrentState", "approvedNextAction",
                   "approvedStatus", "approvedRisk"]:
        val = str(item.get(field, "") or "")
        if "SMOKE" in val.upper() or "EDITED SMOKE" in val.upper():
            return True

    # Check evidence
    for ev in (item.get("evidence", []) or []):
        for ev_field in ["subject", "summary", "messageKey"]:
            ev_val = str(ev.get(ev_field, "") or "")
            if "SMOKE" in ev_val.upper():
                return True

    return False


def is_smoke_decision(dec: dict[str, Any]) -> bool:
    """Check if a decision record references a smoke/test/demo item."""
    reason = str(dec.get("reason", "") or "")
    if "smoke draft" in reason.lower():
        return True
    return False


def _decisions_path(apply_root: Path) -> Path:
    return apply_root / "data" / DECISIONS_FILE


def load_apply_decisions(apply_root: Path) -> dict[str, dict[str, Any]]:
    """Load operator decisions keyed by applyId."""
    path = _decisions_path(apply_root)
    if not path.exists():
        return {}
    decisions: dict[str, dict[str, Any]] = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                d = json.loads(line)
                aid = d.get("applyId", "")
                if aid:
                    decisions[aid] = d
    return decisions


def save_apply_decision(
    apply_root: Path,
    apply_id: str,
    decision: str,
    reason: str = "",
    approved_by: str = "Brian",
) -> dict[str, Any]:
    """Save an operator decision for one plan item.

    Decision values: approved_for_apply, skipped, needs_edit.
    Append-only — never overwrites existing decisions.
    """
    record = {
        "schemaVersion": 1,
        "timestamp": datetime.now().isoformat(),
        "applyId": apply_id,
        "decision": decision,
        "reason": reason,
        "approvedBy": approved_by,
    }
    path = _decisions_path(apply_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()
    logger.info("Decision saved: %s → %s (%s)", apply_id, decision, reason)
    return record


def get_approved_items(
    apply_root: Path,
    plan: dict[str, Any],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Get plan items that have operator approval and are ready to apply.

    Returns list of (plan_item, decision_record) tuples.
    """
    decisions = load_apply_decisions(apply_root)
    approved: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for item in plan.get("planItems", []):
        aid = item.get("applyId", "")
        if aid in decisions and decisions[aid].get("decision") == "approved_for_apply":
            if item.get("readyToApply"):
                approved.append((item, decisions[aid]))
    return approved


def apply_operator_approved_plan(
    settings: Any,
    dry_run: bool = True,
    confirm_string: str = "",
) -> ApplyRunSummary:
    """Apply only operator-approved items from the current apply plan.

    This is the Phase 4C safe apply entry point.
    Real apply requires both config gates + correct confirmation string.
    """
    apply_root = settings.apply_path()
    plan = load_apply_plan(apply_root)
    if not plan:
        raise RuntimeError("No apply plan found. Run 'build-apply-plan' first.")

    if not dry_run:
        # Gate 1: config enabled
        if not settings.local_kanban_apply_enabled or not settings.allow_kanban_apply:
            raise RuntimeError(
                "Local Kanban apply is disabled in settings.json. "
                "Set local_kanban_apply_enabled=true AND allow_kanban_apply=true."
            )
        # Gate 2: confirmation string
        expected = "APPLY LOCAL KANBAN PLAN"
        if confirm_string != expected:
            raise RuntimeError(
                f"Confirmation mismatch. Expected: '{expected}'"
            )
        # Gate 3: path guard
        from .path_safety import assert_not_forbidden
        assert_not_forbidden(str(settings.kanban_local_path()))

    # Get operator-approved items
    approved = get_approved_items(apply_root, plan)
    if not approved:
        logger.info("No operator-approved items found.")
        empty = ApplyRunSummary(
            mode="dry_run" if dry_run else "real_write",
            planItemsTotal=len(plan.get("planItems", [])),
            applied=0,
            localKanbanApplyEnabled=settings.local_kanban_apply_enabled,
        )
        return empty

    # Build a filtered plan with only approved items
    filtered_plan = dict(plan)
    filtered_plan["planItems"] = [item for item, _ in approved]

    # Delegate to the existing apply function with the filtered plan
    # but we need to bypass its plan build. Use the existing apply_approved_drafts
    # which re-builds the plan. Instead, let's just run the apply logic directly
    # on the approved items.

    return _apply_filtered_plan(settings, filtered_plan, dry_run)


def _apply_filtered_plan(
    settings: Any,
    plan: dict[str, Any],
    dry_run: bool,
) -> ApplyRunSummary:
    """Apply a pre-filtered plan (only operator-approved items)."""
    from .apply_models import ApplyPlan
    from .kanban_reader import file_hash, find_projects_json, read_projects_json

    kanban_root = settings.kanban_local_path()
    apply_root = settings.apply_path()
    (apply_root / "data").mkdir(parents=True, exist_ok=True)
    (apply_root / "backups").mkdir(parents=True, exist_ok=True)

    # Re-read current state
    projects, pj_hash = _load_projects_json(kanban_root)
    pj_path, _ = find_projects_json(kanban_root)
    cu_path = _load_card_updates_path(kanban_root)
    before_hash = pj_hash

    backup_path_str = ""
    backup_ok = False
    results: list[ApplyResult] = []

    if not dry_run:
        # Backup
        if settings.backup_before_apply:
            success, bp, _ = _create_backup(kanban_root, apply_root / "backups")
            backup_path_str = bp
            backup_ok = success
            if not success:
                raise RuntimeError("Backup failed — apply aborted.")

    for item in plan.get("planItems", []):
        aid = item.get("applyId", "")
        pid = item.get("projectId", "")
        title = item.get("title", "")
        card_hash = item.get("sourceCardHash", "")

        if not item.get("readyToApply"):
            results.append(ApplyResult(
                applyId=aid, projectId=pid, title=title,
                status="skipped", message="Not ready to apply (hash conflict or missing)",
            ))
            continue

        # Find card
        card = _find_card_by_id(projects, pid)
        if card is None:
            results.append(ApplyResult(
                applyId=aid, projectId=pid, title=title,
                status="skipped", message="Card not found in projects.json",
            ))
            continue

        # Re-verify hash
        live_hash = _build_card_hash(card)
        if not card_hash or live_hash != card_hash:
            results.append(ApplyResult(
                applyId=aid, projectId=pid, title=title,
                status="conflict", message=f"Hash mismatch: expected {card_hash[:16] if card_hash else '(none)'}, got {live_hash[:16]}",
                sourceCardHash=card_hash, appliedCardHashBefore=live_hash,
            ))
            continue

        if dry_run:
            results.append(ApplyResult(
                applyId=aid, projectId=pid, title=title,
                status="skipped", message="Dry-run — would apply",
            ))
            continue

        # --- Real apply ---
        try:
            prev = {
                "context": card.get("context", ""),
                "nextAction": card.get("nextAction", ""),
                "status": card.get("status", ""),
                "riskColour": card.get("riskColour", ""),
            }
            new_vals = {}

            if item.get("approvedCurrentState"):
                card["context"] = item["approvedCurrentState"]
                new_vals["context"] = item["approvedCurrentState"]
            if item.get("approvedNextAction"):
                card["nextAction"] = item["approvedNextAction"]
                new_vals["nextAction"] = item["approvedNextAction"]
            if item.get("approvedStatus"):
                card["status"] = item["approvedStatus"]
                new_vals["status"] = item["approvedStatus"]
            if item.get("approvedRisk"):
                card["riskColour"] = item["approvedRisk"]
                new_vals["riskColour"] = item["approvedRisk"]

            card["lastUpdated"] = datetime.now().isoformat()
            changed = [k for k in new_vals if prev.get(k) != new_vals.get(k)]
            new_hash = _build_card_hash(card)

            # Write projects.json atomically
            with open(pj_path, "r", encoding="utf-8-sig") as f:
                raw = json.load(f)
            if isinstance(raw, dict) and "projects" in raw:
                for i, p in enumerate(raw["projects"]):
                    if p.get("id") == pid:
                        raw["projects"][i] = card
                        break
            elif isinstance(raw, list):
                for i, p in enumerate(raw):
                    if p.get("id") == pid:
                        raw[i] = card
                        break
            import tempfile
            fd, tmp = tempfile.mkstemp(suffix=".tmp", prefix="projects_", dir=pj_path.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as tf:
                json.dump(raw, tf, indent=4, ensure_ascii=False)
                tf.flush()
                os.fsync(fd)
            os.replace(tmp, pj_path)

            # Append audit
            audit = AuditEntry(
                projectId=pid, title=title, draftId=item.get("draftId", ""),
                applyId=aid, fieldsChanged=changed,
                previous={"currentState": prev.get("context",""), "nextAction": prev.get("nextAction",""),
                         "status": prev.get("status",""), "risk": prev.get("riskColour","")},
                new={"currentState": new_vals.get("context",""), "nextAction": new_vals.get("nextAction",""),
                    "status": new_vals.get("status",""), "risk": new_vals.get("riskColour","")},
                sourceEmailKeys=item.get("sourceEmailKeys", []),
                sourceCardHash=card_hash, appliedCardHashBefore=live_hash,
                appliedCardHashAfter=new_hash, humanApproved=True,
            )
            with open(cu_path, "a", encoding="utf-8") as f:
                f.write(audit.model_dump_json(exclude_none=True) + "\n")

            results.append(ApplyResult(
                applyId=aid, projectId=pid, title=title, status="applied",
                message="Applied successfully",
                sourceCardHash=card_hash, appliedCardHashBefore=live_hash,
                appliedCardHashAfter=new_hash,
            ))

        except Exception as e:
            logger.error("Apply error for %s: %s", pid, e)
            results.append(ApplyResult(
                applyId=aid, projectId=pid, title=title, status="error", message=str(e),
            ))

    after_hash = _load_projects_json(kanban_root)[1] if not dry_run else before_hash

    summary = ApplyRunSummary(
        mode="dry_run" if dry_run else "real_write",
        planItemsTotal=len(plan.get("planItems", [])),
        applied=len([r for r in results if r.status == "applied"]),
        conflicts=len([r for r in results if r.status == "conflict"]),
        skipped=len([r for r in results if r.status == "skipped"]),
        errors=len([r for r in results if r.status == "error"]),
        backupPath=backup_path_str,
        backupSuccess=backup_ok,
        projectsJsonHashBefore=before_hash,
        projectsJsonHashAfter=after_hash,
        localKanbanApplyEnabled=settings.local_kanban_apply_enabled,
    )

    # Write results
    rpath = apply_root / "data" / "apply_results.jsonl"
    with open(rpath, "w", encoding="utf-8") as f:
        for r in results:
            f.write(r.model_dump_json(exclude_none=True) + "\n")

    spath = apply_root / "data" / "apply_run_summary.json"
    with open(spath, "w", encoding="utf-8") as f:
        json.dump(summary.model_dump(exclude_none=True), f, indent=2, ensure_ascii=False)

    return summary


# ---------------------------------------------------------------------------
# Phase 4E/4F — Apply workspace reset
# ---------------------------------------------------------------------------

def reset_apply_workspace(settings: Any) -> dict[str, Any]:
    """Archive apply workspace files to a timestamped archive directory.

    Archives:
      - apply_plan.json
      - apply_review_decisions.jsonl
      - apply_results.jsonl
      - apply_run_summary.json
      - apply_conflicts.jsonl (if present)
      - apply_skipped.jsonl (if present)
      - kanban_coach_pilot_report_*.md files
      - apply_review_report_*.md files
      - hermes-verify-*.md files

    Does NOT touch:
      - Kanban source data
      - Team ESMI UNC data
      - Review drafts
      - card_updates.jsonl or audit trail

    Returns a dict with archive info.
    """
    apply_root = settings.apply_path()
    data_dir = apply_root / "data"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_dir = apply_root / "archive" / ts
    archive_dir.mkdir(parents=True, exist_ok=True)

    files_to_archive: list[str] = [
        "apply_plan.json",
        "apply_review_decisions.jsonl",
        "apply_results.jsonl",
        "apply_run_summary.json",
        "apply_conflicts.jsonl",
        "apply_skipped.jsonl",
    ]

    archived: list[str] = []
    for fname in files_to_archive:
        src = data_dir / fname
        if src.exists():
            dest = archive_dir / fname
            import shutil
            shutil.copy2(str(src), str(dest))
            src.unlink()
            archived.append(fname)

    # Archive generated reports
    for pattern in ["kanban_coach_pilot_report_*.md", "apply_review_report_*.md",
                     "hermes-verify-*.md", "vfy-report*.md"]:
        for f in data_dir.glob(pattern):
            dest = archive_dir / f.name
            import shutil
            shutil.copy2(str(f), str(dest))
            f.unlink()
            archived.append(f.name)

    result = {
        "timestamp": ts,
        "archive_path": str(archive_dir),
        "archived_count": len(archived),
        "archived_files": archived,
    }

    logger = setup_logging(Path("runtime/email_recall/logs"))
    logger.info("Apply workspace reset: archived %d files to %s", len(archived), archive_dir)

    # Create fresh empty decisions file
    fresh_dec = data_dir / DECISIONS_FILE
    fresh_dec.parent.mkdir(parents=True, exist_ok=True)
    if not fresh_dec.exists():
        fresh_dec.write_text("", encoding="utf-8")

    return result
