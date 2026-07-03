"""Local Qwen/Ollama comparison engine for Phase 3.

Reads card state + matched email evidence and produces draft
card-state comparison records using Ollama, with deterministic fallback.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .draft_models import CardUpdateDraft, DraftEvidence, DraftRunSummary
from .kanban_indexer import load_card_index
from .logging_setup import setup_logging
from .matching_engine import load_matches
from .ollama_client import check_ollama, check_model_available, generate
from .path_safety import assert_not_forbidden

logger = setup_logging(Path("runtime/email_recall/logs"))


# ---------------------------------------------------------------------------
# System prompt for Qwen
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a cautious governance assistant for a project Kanban board.
You do NOT update the Kanban. You only compare current card state against new evidence.
Only suggest an update if the evidence materially changes the current state, next action, risk, or status.
If the email repeats what the card already says, return no_change.
If evidence is weak or ambiguous, return needs_review.
Do not invent facts. Do not infer beyond supplied evidence.
Use concise professional wording.
Keep currentState and nextAction suitable for a Kanban card.
Return strict JSON only.

Respond with a JSON object containing these fields:
{
  "decision": "material_update|possible_update|no_change|needs_review|possible_new_project",
  "confidence": 0.0,
  "newEvidenceState": "brief summary of what the new evidence says",
  "suggestedCurrentState": "updated state text or empty string if no change",
  "suggestedNextAction": "updated next action or empty string if no change",
  "suggestedStatus": "running|blocked|ready|done or empty if no change",
  "suggestedRisk": "green|amber|red or empty if no change",
  "reasonForDecision": "one sentence explaining the reasoning"
}"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_card_dict(card_index_root: Path) -> dict[str, dict[str, Any]]:
    """Load cards as a dict keyed by projectId."""
    cards = load_card_index(card_index_root)
    return {c.projectId: c.model_dump() for c in cards}


def _load_matched_emails(matching_root: Path) -> list[dict[str, Any]]:
    """Load matched emails from matching output."""
    return load_matches(matching_root)


def _load_email_body(evidence_folder_rel: str, email_recall_root: Path) -> str:
    """Load body.txt from an email evidence folder."""
    if not evidence_folder_rel:
        return ""
    try:
        path = email_recall_root / evidence_folder_rel
        body_path = path / "body.txt"
        if body_path.exists():
            return body_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    return ""


def _build_evidence_list(
    match: dict[str, Any],
    email_recall_root: Path,
    max_chars: int,
) -> list[DraftEvidence]:
    """Build evidence list from a match record."""
    evidence = DraftEvidence(
        type="email",
        messageKey=match.get("emailMessageKey", ""),
        subject=match.get("emailSubject", ""),
        from_=match.get("emailFrom", ""),
        receivedAt=match.get("emailReceivedAt", ""),
        summary=(match.get("emailPreview", "") or "")[:max_chars],
    )
    return [evidence]


def _build_card_context_bundle(
    card: dict[str, Any],
    match: dict[str, Any],
    evidence: list[DraftEvidence],
    max_body_chars: int,
) -> tuple[str, str]:
    """Build a compact context bundle for the prompt.

    Returns (user_prompt, new_evidence_state).
    """
    # Card state summary
    card_lines = [
        f"CARD: {card.get('title', '')}",
        f"Status: {card.get('status', '')}",
        f"Risk: {card.get('risk', '')}",
        f"Lead: {card.get('lead', '')}",
        f"Owner: {card.get('owner', '')}",
        f"Last updated: {card.get('lastUpdated', '')}",
        f"Current state: {card.get('currentState', '')}",
        f"Next action: {card.get('nextAction', '')}",
    ]

    # Evidence summary
    evidence_lines = ["NEW EVIDENCE:"]
    for i, ev in enumerate(evidence, 1):
        body = (ev.summary or "")[:max_body_chars]
        evidence_lines.append(
            f"[Email {i}] Subject: {ev.subject}"
        )
        evidence_lines.append(f"  From: {ev.from_}")
        evidence_lines.append(f"  Received: {ev.receivedAt}")
        evidence_lines.append(f"  Body preview: {body[:500]}")

    prompt = "\n".join(card_lines + [""] + evidence_lines)

    # Build new evidence state summary string
    new_state = ""
    if evidence:
        first = evidence[0]
        new_state = f"Email from {first.from_} re: {first.subject}. "
        new_state += f"Match confidence: {match.get('confidence', 0):.0%}. "

    return prompt, new_state


def _build_fallback_draft(
    card: dict[str, Any],
    match: dict[str, Any],
    evidence: list[DraftEvidence],
    match_signals: list[dict[str, Any]],
    card_hash: str,
) -> CardUpdateDraft:
    """Build a needs_review fallback draft from deterministic match data only."""
    email_keys = [e.messageKey for e in evidence if e.messageKey]
    new_state = ""
    if evidence:
        first = evidence[0]
        new_state = f"Email from {first.from_} re: {first.subject}."

    return CardUpdateDraft(
        projectId=card.get("projectId", ""),
        title=card.get("title", ""),
        decision="needs_review",
        confidence=match.get("confidence", 0) or 0.0,
        currentCardState=card.get("currentState", "") or "",
        currentNextAction=card.get("nextAction", "") or "",
        newEvidenceState=new_state,
        suggestedCurrentState="",
        suggestedNextAction="",
        suggestedStatus="",
        suggestedRisk="",
        reasonForDecision=(
            f"Match found (confidence {match.get('confidence', 0):.0%}) "
            f"but Ollama unavailable. Manual review required."
        ),
        evidence=evidence,
        matchedSignals=match_signals,
        requiresHumanApproval=True,
        sourceCardHash=card_hash,
        sourceEmailKeys=email_keys,
        generatedBy="fallback",
    )


# ---------------------------------------------------------------------------
# Main generation function
# ---------------------------------------------------------------------------
def generate_drafts(
    settings: Any,
    since_hours: int = 72,
) -> dict[str, Any]:
    """Generate card-update drafts from matched email evidence.

    Uses Ollama if available and enabled; falls back to deterministic
    needs_review drafts if Ollama unavailable and allow_draft_without_ollama=True.

    Returns summary dict with counts.
    """
    email_root = settings.output_path()
    kanban_root = settings.kanban_index_path()
    matching_root = email_root.parent / "matching"
    draft_root = settings.drafts_path()
    draft_root.mkdir(parents=True, exist_ok=True)
    (draft_root / "data").mkdir(parents=True, exist_ok=True)
    assert_not_forbidden(draft_root)

    # Config
    base_url = settings.ollama_base_url
    model = settings.ollama_model
    min_conf = settings.draft_min_match_confidence
    max_chars = settings.max_email_body_chars_for_draft
    max_emails = settings.max_evidence_emails_per_card
    enable_ollama = settings.enable_ollama_drafts
    allow_fallback = settings.allow_draft_without_ollama

    # Check Ollama
    ollama_ok, ollama_msg, available_models = check_ollama(base_url)
    model_ok = False
    if ollama_ok:
        model_ok, model_msg = check_model_available(base_url, model)
        logger.info("Ollama: %s | Model: %s", ollama_msg, model_msg if ollama_ok else "N/A")
    else:
        logger.info("Ollama: %s — will use fallback if allowed", ollama_msg)

    if not ollama_ok and not allow_fallback:
        logger.error("Ollama unavailable and allow_draft_without_ollama=False. Aborting.")
        raise RuntimeError(
            "Ollama unavailable and allow_draft_without_ollama=False. "
            "Set allow_draft_without_ollama=true or start Ollama."
        )

    t_start = time.time()

    # Load data
    card_dict = _load_card_dict(kanban_root)
    matches = _load_matched_emails(matching_root)
    logger.info("Loaded %d cards, %d matches", len(card_dict), len(matches))

    # Filter matches by confidence and decision
    filtered = [
        m for m in matches
        if m.get("decision") in ("matched", "possible_match")
        and (m.get("confidence") or 0) >= min_conf
    ]
    logger.info(
        "Matches meeting criteria (conf>=%.0f%%, decision=matched|possible_match): %d",
        min_conf * 100, len(filtered),
    )

    if not filtered:
        logger.info("No matches to process.")
        elapsed = time.time() - t_start
        summary = DraftRunSummary(
            scannedMatches=len(matches),
            cardsConsidered=0,
            ollamaAvailable=ollama_ok,
            modelUsed=model,
            runtimeSeconds=round(elapsed, 1),
        )
        _write_summary(draft_root, summary)
        return summary.model_dump()

    # Group matches by projectId
    matches_by_card: dict[str, list[dict[str, Any]]] = {}
    for m in filtered:
        pid = m.get("matchedProjectId", "")
        if pid and pid in card_dict:
            matches_by_card.setdefault(pid, []).append(m)

    logger.info("Cards with matched evidence: %d", len(matches_by_card))

    # Process each card
    drafts: list[CardUpdateDraft] = []
    no_changes: list[CardUpdateDraft] = []
    needs_reviews: list[CardUpdateDraft] = []

    for pid, card_matches in matches_by_card.items():
        card = card_dict.get(pid, {})
        if not card:
            continue

        # Limit evidence emails per card
        card_matches = card_matches[:max_emails]

        # Build combined evidence
        all_evidence: list[DraftEvidence] = []
        all_keys: list[str] = []
        all_signals: list[dict] = []

        for cm in card_matches:
            ev_list = _build_evidence_list(cm, email_root, max_chars)
            all_evidence.extend(ev_list)
            if cm.get("emailMessageKey"):
                all_keys.append(cm["emailMessageKey"])
            all_signals.extend(cm.get("matchedSignals", []))

        # Build prompt context
        context, new_state = _build_card_context_bundle(
            card, card_matches[0], all_evidence, max_chars,
        )

        # Card hash
        card_hash = card.get("sourceHash", "") or ""

        # Try Ollama
        ollama_draft = None
        if enable_ollama and ollama_ok and model_ok:
            success, msg, result = generate(
                base_url, model,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=context,
                timeout=60,
            )
            if success and result:
                logger.info("Ollama OK for card %s: %s", pid, msg)
                ollama_draft = result
            else:
                logger.warning("Ollama failed for card %s: %s", pid, msg)

        if ollama_draft:
            # Use Ollama result
            decision = ollama_draft.get("decision", "needs_review")
            raw_conf = ollama_draft.get("confidence", 0.5)
            if isinstance(raw_conf, (int, float)):
                conf_val = float(raw_conf)
            else:
                conf_val = 0.5

            draft = CardUpdateDraft(
                projectId=pid,
                title=card.get("title", ""),
                decision=decision,
                confidence=min(1.0, max(0.0, conf_val)),
                currentCardState=card.get("currentState", "") or "",
                currentNextAction=card.get("nextAction", "") or "",
                newEvidenceState=ollama_draft.get("newEvidenceState", new_state),
                suggestedCurrentState=ollama_draft.get("suggestedCurrentState", "") or "",
                suggestedNextAction=ollama_draft.get("suggestedNextAction", "") or "",
                suggestedStatus=ollama_draft.get("suggestedStatus", "") or "",
                suggestedRisk=ollama_draft.get("suggestedRisk", "") or "",
                reasonForDecision=ollama_draft.get("reasonForDecision", "") or "",
                evidence=all_evidence,
                matchedSignals=all_signals,
                requiresHumanApproval=True,
                sourceCardHash=card_hash,
                sourceEmailKeys=all_keys,
                generatedBy="ollama",
            )
        elif allow_fallback:
            draft = _build_fallback_draft(
                card, card_matches[0], all_evidence, all_signals, card_hash,
            )
        else:
            logger.warning("Skipping card %s — no Ollama and no fallback.", pid)
            continue

        # Route by decision
        if draft.decision in ("material_update", "possible_update"):
            drafts.append(draft)
        elif draft.decision == "no_change":
            no_changes.append(draft)
        else:
            needs_reviews.append(draft)

    # Write outputs
    _write_drafts(draft_root / "data" / "card_update_drafts.jsonl", drafts)
    _write_drafts(draft_root / "data" / "no_change_decisions.jsonl", no_changes)
    _write_drafts(draft_root / "data" / "needs_review_decisions.jsonl", needs_reviews)

    elapsed = time.time() - t_start
    summary = DraftRunSummary(
        scannedMatches=len(matches),
        cardsConsidered=len(matches_by_card),
        materialUpdates=len([d for d in drafts if d.decision == "material_update"]),
        possibleUpdates=len([d for d in drafts if d.decision == "possible_update"]),
        noChange=len(no_changes),
        needsReview=len(needs_reviews),
        possibleNewProject=len([d for d in drafts if d.decision == "possible_new_project"]),
        ollamaAvailable=ollama_ok,
        modelUsed=model if ollama_ok and model_ok else "fallback",
        runtimeSeconds=round(elapsed, 1),
    )
    _write_summary(draft_root, summary)

    logger.info(
        "Draft generation complete: %d material, %d possible, %d no-change, %d needs-review "
        "(Ollama=%s, %.1fs)",
        summary.materialUpdates, summary.possibleUpdates,
        summary.noChange, summary.needsReview,
        "yes" if ollama_ok else "no (fallback)" if allow_fallback else "no",
        elapsed,
    )

    return summary.model_dump()


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------
def _write_drafts(path: Path, drafts: list[CardUpdateDraft]) -> None:
    """Write a list of drafts to a JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for d in drafts:
            f.write(d.model_dump_json(exclude_none=True) + "\n")


def _write_summary(root: Path, summary: DraftRunSummary) -> None:
    """Write the draft run summary JSON."""
    path = root / "data" / "draft_run_summary.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary.model_dump(exclude_none=True), f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Readers for CLI
# ---------------------------------------------------------------------------
def load_draft_records(root: Path, filename: str) -> list[dict[str, Any]]:
    """Load a draft JSONL file."""
    path = root / "data" / filename
    if not path.exists():
        return []
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def load_draft_summary(root: Path) -> dict[str, Any] | None:
    """Load draft_run_summary.json."""
    path = root / "data" / "draft_run_summary.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
