"""Local Qwen adviser for review-apply-tui (Phase 5, Part D).

Provides Ollama/Qwen-powered card advice using:
- Selected Kanban card (from apply plan)
- Proposed apply-plan recommendation
- Latest Team ESMI card context (from polling)
- Linked email evidence/source context

Logs every interaction to local_ai_advice.jsonl and accepted
updates to local_ai_update_log.jsonl.

Core principle: Qwen is an adviser and review-workspace assistant,
not a Kanban apply engine.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .ollama_client import check_ollama, check_model_available, generate

# ---------------------------------------------------------------------------
# Log file names
# ---------------------------------------------------------------------------

ADVICE_LOG = "local_ai_advice.jsonl"
UPDATE_LOG = "local_ai_update_log.jsonl"

# ---------------------------------------------------------------------------
# Fallback email body reader
# ---------------------------------------------------------------------------


def _read_body_text(path: Path) -> str:
    """Read body.txt or body.html from an email evidence directory."""
    body_txt = path / "body.txt"
    if body_txt.exists():
        return body_txt.read_text(encoding="utf-8", errors="replace")[:5000]
    body_html = path / "body.html"
    if body_html.exists():
        import re
        html = body_html.read_text(encoding="utf-8", errors="replace")
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:5000]
    return ""


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------


def _apply_data_dir(settings: Any) -> Path:
    return getattr(settings, "apply_path", lambda: Path())().resolve() / "data"


def _log_advice(settings: Any, entry: dict[str, Any]) -> None:
    """Append a raw adviser interaction to local_ai_advice.jsonl."""
    if not getattr(settings, "local_ai_update_log_enabled", True):
        return
    data_dir = _apply_data_dir(settings)
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / ADVICE_LOG
    if "timestamp" not in entry:
        entry["timestamp"] = datetime.now().isoformat()
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _log_update(settings: Any, entry: dict[str, Any]) -> None:
    """Append an accepted adviser update to local_ai_update_log.jsonl."""
    if not getattr(settings, "local_ai_update_log_enabled", True):
        return
    data_dir = _apply_data_dir(settings)
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / UPDATE_LOG
    if "timestamp" not in entry:
        entry["timestamp"] = datetime.now().isoformat()
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Ollama readiness
# ---------------------------------------------------------------------------


def adviser_is_available(settings: Any) -> tuple[bool, str, list[str]]:
    """Check if the local Qwen adviser is available.

    Returns (available, message, models_list).
    """
    if not getattr(settings, "ollama_enabled", True):
        return False, "Local Qwen adviser is disabled in settings (ollama_enabled=false).", []
    base_url = settings.ollama_base_url
    ok, msg, models = check_ollama(base_url, timeout=5)
    if not ok:
        return False, f"Local Qwen adviser unavailable. {msg}", models
    model = settings.ollama_model
    model_ok, model_msg = check_model_available(base_url, model, timeout=5)
    if not model_ok:
        return False, f"Model '{model}' not available. {model_msg}", models
    return True, f"Ready (model={model}, {len(models)} models available)", models


# ---------------------------------------------------------------------------
# Comparison builder
# ---------------------------------------------------------------------------


def _build_comparison_object(
    item: dict[str, Any],
    current_card: dict[str, Any] | None,
    team_card: dict[str, Any] | None,
    email_context: list[dict[str, Any]] | dict[str, Any],
    team_context_hash: str,
    team_context_mtime: str,
    mailbox_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the structured comparison object for the Qwen prompt."""
    # Extract emails list from either format
    if isinstance(email_context, dict):
        email_list = email_context.get("emails", [])
    else:
        email_list = list(email_context)
    mailbox_info = mailbox_info or {}
    apply_snapshot = {
        "lead": (current_card or {}).get("projectLead", "") or "",
        "status": (current_card or {}).get("status", "") or "",
        "risk": (current_card or {}).get("riskColour", "") or "",
        "current_state": (current_card or {}).get("context", "") or "",
        "next_action": (current_card or {}).get("nextAction", "") or "",
        "last_updated": (current_card or {}).get("lastUpdated", "") or "",
    }

    # Latest Team ESMI card
    latest_team = None
    if team_card:
        latest_team = {
            "lead": (team_card.get("projectLead", "") or ""),
            "status": (team_card.get("status", "") or ""),
            "risk": (team_card.get("riskColour", "") or ""),
            "current_state": (team_card.get("context", "") or ""),
            "next_action": (team_card.get("nextAction", "") or ""),
            "last_updated": (team_card.get("lastUpdated", "") or ""),
            "sourceHash": team_context_hash,
        }

    # Proposed recommendation
    proposed = {
        "lead": item.get("approvedLead", "") or "",
        "status": item.get("approvedStatus", "") or "",
        "risk": item.get("approvedRisk", "") or "",
        "current_state": item.get("approvedCurrentState", "") or "",
        "next_action": item.get("approvedNextAction", "") or "",
        "confidence": item.get("confidence", 0) or 0,
        "reason": item.get("reasonForDecision", "") or "",
    }

    # Email evidence
    email_evidence_list = []
    for ev in email_list:
        email_evidence_list.append({
            "email_key": ev.get("email_key", ""),
            "subject": ev.get("subject", ""),
            "date": ev.get("date", ""),
            "snippet": ev.get("snippet", "")[:600],
            "matched_reason": ev.get("matched_reason", ""),
        })

    # Attachment evidence (stronger signal than vague body matches)
    attachment_evidence_list = []
    for att in (mailbox_info.get("attachments", []) or []):
        attachment_evidence_list.append({
            "email_subject": att.get("email_subject", ""),
            "filename": att.get("filename", ""),
            "size": att.get("size", 0),
            "sender": att.get("sender", ""),
        })

    return {
        "card_title": item.get("title", ""),
        "apply_plan_snapshot": apply_snapshot,
        "latest_team_esmi_card": latest_team,
        "proposed_recommendation": proposed,
        "email_evidence": email_evidence_list,
        "attachment_evidence": attachment_evidence_list,
        "comparison_questions": [
            "Do the emails show a newer status than the card?",
            "Does latest Team ESMI context differ from the apply-plan snapshot?",
            "Do the emails confirm or contradict the proposed recommendation?",
            "Is the next action clear and assigned?",
            "Does risk/status/lead need changing?",
        ],
        "mailbox_search_used": mailbox_info.get("used", False),
        "mailbox_provider": mailbox_info.get("provider", "disabled"),
        "mailbox_match_count": mailbox_info.get("match_count", 0),
        "search_terms_used": mailbox_info.get("search_terms", []),
    }


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _build_adviser_prompt(
    operator_request: str,
    comparison: dict[str, Any],
) -> tuple[str, str]:
    """Build system and user prompts for the Qwen adviser.

    Returns (system_prompt, user_prompt).
    """
    system = (
        "You are a knowledgeable Kanban adviser assisting a SAMI Medical Imaging "
        "program delivery operator. Your role is to compare the current Kanban card "
        "state, the latest Team ESMI card context, email evidence, and the proposed "
        "apply-plan recommendation, then provide evidence-based suggestions.\n\n"
        "You are an ADVISER only. You do NOT write to Kanban. You do NOT approve items. "
        "You do NOT make apply decisions. You provide suggestions that the operator "
        "may accept, edit, or discard.\n\n"
        "IMPORTANT RULES:\n"
        "- Compare emails vs card. Tell the operator if emails suggest changes.\n"
        "- Compare latest Team ESMI context vs the apply-plan snapshot.\n"
        "- Classify the recommendation as: update, unchanged, needs_more_info, or low_confidence.\n"
        "- Suggest clearer current-state or next-action wording if the emails support it.\n"
        "- Only suggest status/risk/lead changes if the evidence clearly justifies them.\n"
        "- Set confidence between 0.0 and 1.0 based on how strong the evidence is.\n"
        "- If evidence is weak or conflicting, classify as low_confidence or needs_more_info.\n"
        "- Include a concise operator-facing review comment.\n"
        "- List clarification questions if anything is unclear.\n"
        "- Use only the email evidence provided below. Do not invent unsupported updates.\\n"
        "- ATTACHMENT EVIDENCE is stronger than vague email body matches. If an attachment\\n"
        "  contains technical details (IPs, SRV/REQ numbers, firewall rules, host names),\\n"
        "  treat those as confirmed facts. If only the email body vaguely mentions a topic\\n"
        "  without specific details, treat that as weak evidence.\\n"
        "- If email evidence is weak or missing, say so explicitly.\\n"
        "- Return ONLY valid JSON. No markdown fences, no extra text.\n\n"
        "REQUIRED JSON OUTPUT:\n"
        "{\n"
        '  "summary": "Short explanation of what changed and why.",\n'
        '  "email_vs_card_assessment": "What the emails show compared with the current card.",\n'
        '  "team_context_assessment": "Whether latest Team ESMI context differs from the apply-plan snapshot.",\n'
        '  "classification": "update | unchanged | needs_more_info | low_confidence",\n'
        '  "suggested_current_state": "optional improved current-state wording",\n'
        '  "suggested_next_action": "optional improved next-action wording",\n'
        '  "suggested_status": "optional status if justified",\n'
        '  "suggested_risk": "optional risk if justified",\n'
        '  "suggested_lead_owner": "optional lead/owner if justified",\n'
        '  "review_comment": "operator-facing comment explaining the reasoning",\n'
        '  "questions": ["optional clarification questions"],\n'
        '  "evidence_used": ["email key or source reference"],\n'
        '  "confidence": 0.0\n'
        "}"
    )

    # Build the comparison context
    parts: list[str] = [f"Operator Request: {operator_request}\n"]

    parts.append("=== Card Title ===")
    parts.append(comparison.get("card_title", ""))

    parts.append("\n=== Apply-Plan Snapshot (from review workspace) ===")
    snap = comparison.get("apply_plan_snapshot", {})
    parts.append(json.dumps(snap, indent=2))

    team_card = comparison.get("latest_team_esmi_card")
    if team_card:
        parts.append("\n=== Latest Team ESMI Card Context ===")
        parts.append(json.dumps(team_card, indent=2))
    else:
        parts.append("\n=== Latest Team ESMI Card Context ===")
        parts.append("(Not available — using apply-plan snapshot only)")

    parts.append("\n=== Proposed Recommendation ===")
    proposed = comparison.get("proposed_recommendation", {})
    parts.append(json.dumps(proposed, indent=2))

    emails = comparison.get("email_evidence", [])
    if emails:
        parts.append(f"\n=== Email Evidence ({len(emails)} emails) ===")
        for i, ev in enumerate(emails, 1):
            parts.append(
                f"\nEmail {i}:\n"
                f"  Key: {ev.get('email_key', '')}\n"
                f"  Subject: {ev.get('subject', '')}\n"
                f"  Date: {ev.get('date', '')}\n"
                f"  Matched by: {ev.get('matched_reason', '')}\n"
                f"  Snippet: {ev.get('snippet', '')[:800]}"
            )
    else:
        parts.append("\n=== Email Evidence ===")
        parts.append("(No linked email context found for this card.)")

    # Attachment evidence
    attachments = comparison.get("attachment_evidence", [])
    if attachments:
        parts.append(f"\n=== Attachment Evidence ({len(attachments)} files) ===")
        for i, att in enumerate(attachments, 1):
            parts.append(
                f"\nAttachment {i}:\n"
                f"  File: {att.get('filename', '')} ({att.get('size', 0)} bytes)\n"
                f"  From email: {att.get('email_subject', '')}\n"
                f"  Sender: {att.get('sender', '')}"
            )
        parts.append("\nNOTE: Attachment content is stronger evidence than email body text. "
                      "If an attachment contains IPs, SRV/REQ numbers, firewall rules, or "
                      "technical requirements, treat those as confirmed facts.")

    parts.append("\n=== Comparison Questions ===")
    for q in comparison.get("comparison_questions", []):
        parts.append(f"- {q}")

    user_prompt = "\n".join(parts)
    return system, user_prompt


# ---------------------------------------------------------------------------
# Main adviser function
# ---------------------------------------------------------------------------


def ask_adviser(
    settings: Any,
    item: dict[str, Any],
    current_card: dict[str, Any] | None,
    team_card: dict[str, Any] | None,
    email_context: list[dict[str, Any]] | dict[str, Any],
    team_context_hash: str,
    team_context_mtime: str,
    operator_request: str = "Review this card and recommendation.",
    mailbox_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ask the local Qwen adviser for suggestions on an apply-plan item.

    Builds the comparison object, calls Ollama, parses the response,
    logs the interaction, and returns the parsed result.

    Returns a dict with keys:
        success: bool
        message: str
        advice: dict | None — parsed Qwen response
        raw_response: str | None — raw text from Qwen
    """
    if not getattr(settings, "ollama_enabled", True):
        return {
            "success": False,
            "message": "Local Qwen adviser is disabled in settings.",
            "advice": None,
            "raw_response": None,
        }

    base_url = str(settings.ollama_base_url).rstrip("/")
    model = str(settings.ollama_model)
    timeout = getattr(settings, "ollama_timeout_seconds", 60)

    # Extract email count from either format
    if isinstance(email_context, dict):
        email_list = email_context.get("emails", [])
        email_count = len(email_list)
        mailbox_info = mailbox_info or {
            "used": email_context.get("mailboxSearchUsed", False),
            "provider": email_context.get("mailboxProvider", "disabled"),
            "match_count": email_context.get("mailboxPreExportMatches", 0),
            "search_terms": email_context.get("searchTermsUsed", []),
            "attachments": email_context.get("attachment_evidence", []),
        }
    else:
        email_list = list(email_context)
        email_count = len(email_list)
        mailbox_info = mailbox_info or {}

    # Build comparison
    comparison = _build_comparison_object(
        item, current_card, team_card, email_list,
        team_context_hash, team_context_mtime, mailbox_info,
    )

    # Build prompts
    system_prompt, user_prompt = _build_adviser_prompt(operator_request, comparison)

    # Call Ollama
    success, msg, parsed = generate(
        base_url=base_url,
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        timeout=timeout,
        max_retries=2,
    )

    raw_response = None

    if not success:
        # Log failed interaction
        advice_entry: dict[str, Any] = {
            "eventType": "qwen_advice_failed",
            "applyId": item.get("applyId", ""),
            "projectId": item.get("projectId", ""),
            "cardTitle": item.get("title", ""),
            "operatorRequest": operator_request,
            "model": model,
            "ollamaBaseUrl": base_url,
            "teamContextHash": team_context_hash,
            "teamContextMtime": team_context_mtime,
            "emailContextCount": email_count,
            "error": msg,
            "mailboxSearchEnabled": mailbox_info.get("used", False) if mailbox_info else False,
            "mailboxSearchProvider": mailbox_info.get("provider", "disabled") if mailbox_info else "disabled",
            "mailboxSearchUsed": mailbox_info.get("used", False) if mailbox_info else False,
            "mailboxMutated": False,
            "kanbanWritePerformed": False,
        }
        _log_advice(settings, advice_entry)
        return {
            "success": False,
            "message": f"Ollama call failed: {msg}",
            "advice": None,
            "raw_response": None,
        }

    # Parse response
    if isinstance(parsed, dict):
        advice: dict[str, Any] = dict(parsed)
    else:
        record: dict[str, Any] = {
            "eventType": "qwen_malformed_response",
            "applyId": item.get("applyId", ""),
            "projectId": item.get("projectId", ""),
            "cardTitle": item.get("title", ""),
            "operatorRequest": operator_request,
            "model": model,
            "ollamaBaseUrl": base_url,
            "teamContextHash": team_context_hash,
            "teamContextMtime": team_context_mtime,
            "rawResponse": str(parsed),
            "kanbanWritePerformed": False,
        }
        _log_advice(settings, record)
        return {
            "success": False,
            "message": "Malformed response from Qwen.",
            "advice": None,
            "raw_response": str(parsed),
        }

    # Ensure classification field
    advice.setdefault("classification", "needs_more_info")
    advice.setdefault("summary", "")
    advice.setdefault("email_vs_card_assessment", "")
    advice.setdefault("team_context_assessment", "")
    advice.setdefault("suggested_current_state", "")
    advice.setdefault("suggested_next_action", "")
    advice.setdefault("suggested_status", "")
    advice.setdefault("suggested_risk", "")
    advice.setdefault("suggested_lead_owner", "")
    advice.setdefault("review_comment", "")
    advice.setdefault("questions", [])
    advice.setdefault("evidence_used", [])
    advice.setdefault("confidence", 0.0)

    # Log successful interaction
    success_entry: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "eventType": "qwen_advice_generated",
        "applyId": item.get("applyId", ""),
        "projectId": item.get("projectId", ""),
        "cardTitle": item.get("title", ""),
        "operatorRequest": operator_request,
        "model": model,
        "ollamaBaseUrl": base_url,
        "teamContextHash": team_context_hash,
        "teamContextMtime": team_context_mtime,
        "emailContextCount": email_count,
        "emailKeysUsed": [ev.get("email_key", "") for ev in email_list],
        "mailboxSearchEnabled": mailbox_info.get("used", False) if mailbox_info else False,
        "mailboxSearchProvider": mailbox_info.get("provider", "disabled") if mailbox_info else "disabled",
        "mailboxSearchUsed": mailbox_info.get("used", False) if mailbox_info else False,
        "mailboxPreExportMatches": mailbox_info.get("match_count", 0) if mailbox_info else 0,
        "mailboxSearchTermsUsed": mailbox_info.get("search_terms", []) if mailbox_info else [],
        "mailboxMutated": False,
        "emailSnapshotCount": mailbox_info.get("match_count", 0) if mailbox_info else 0,
        "inboxFolderPath": email_context.get("inbox_info", {}).get("folderPath", "") if isinstance(email_context, dict) else "",
        "inboxFolderName": email_context.get("inbox_info", {}).get("folderName", "") if isinstance(email_context, dict) else "",
        "attachmentCount": len(email_context.get("attachment_evidence", [])) if isinstance(email_context, dict) else 0,
        "classification": advice.get("classification", "needs_more_info"),
        "summary": advice.get("summary", ""),
        "suggestedCurrentState": advice.get("suggested_current_state", ""),
        "suggestedNextAction": advice.get("suggested_next_action", ""),
        "suggestedStatus": advice.get("suggested_status", ""),
        "suggestedRisk": advice.get("suggested_risk", ""),
        "suggestedLeadOwner": advice.get("suggested_lead_owner", ""),
        "reviewComment": advice.get("review_comment", ""),
        "confidence": advice.get("confidence", 0.0),
        "kanbanWritePerformed": False,
    }
    _log_advice(settings, success_entry)

    return {
        "success": True,
        "message": "Adviser response received.",
        "advice": advice,
        "raw_response": None,
    }


# ---------------------------------------------------------------------------
# Accept / copy / discard helpers
# ---------------------------------------------------------------------------


def accept_advice(
    settings: Any,
    item: dict[str, Any],
    advice: dict[str, Any],
    accepted_fields: dict[str, str] | None = None,
    operator_note: str = "",
) -> dict[str, Any]:
    """Record an accepted adviser suggestion to local_ai_update_log.jsonl.

    Args:
        settings: Application settings.
        item: The apply-plan item dict.
        advice: The parsed Qwen advice dict.
        accepted_fields: Optional subset of fields the operator accepted.
                         If None, all suggested fields are recorded.
        operator_note: Optional operator note.

    Returns the logged entry dict.
    """
    if accepted_fields is None:
        accepted_fields = {}
        if advice.get("suggested_current_state"):
            accepted_fields["suggestedCurrentState"] = advice["suggested_current_state"]
        if advice.get("suggested_next_action"):
            accepted_fields["suggestedNextAction"] = advice["suggested_next_action"]
        if advice.get("suggested_status"):
            accepted_fields["suggestedStatus"] = advice["suggested_status"]
        if advice.get("suggested_risk"):
            accepted_fields["suggestedRisk"] = advice["suggested_risk"]
        if advice.get("review_comment"):
            accepted_fields["reviewComment"] = advice["review_comment"]

    entry = {
        "timestamp": datetime.now().isoformat(),
        "eventType": "qwen_suggestion_accepted",
        "applyId": item.get("applyId", ""),
        "projectId": item.get("projectId", ""),
        "cardTitle": item.get("title", ""),
        "acceptedByOperator": True,
        "acceptedFields": accepted_fields,
        "workspaceFilesTouched": [
            f"runtime/apply/data/{ADVICE_LOG}",
            f"runtime/apply/data/{UPDATE_LOG}",
        ],
        "kanbanFilesTouched": [],
        "teamEsmiFilesTouched": [],
        "kanbanWritePerformed": False,
        "teamEsmiWritePerformed": False,
        "operatorNote": operator_note,
    }
    _log_update(settings, entry)
    return entry


def discard_advice(
    settings: Any,
    item: dict[str, Any],
    advice: dict[str, Any],
    reason: str = "",
) -> dict[str, Any]:
    """Record a discarded adviser suggestion.

    Args:
        settings: Application settings.
        item: The apply-plan item dict.
        advice: The parsed Qwen advice dict.
        reason: Optional reason for discarding.

    Returns the logged entry dict.
    """
    entry = {
        "timestamp": datetime.now().isoformat(),
        "eventType": "qwen_suggestion_discarded",
        "applyId": item.get("applyId", ""),
        "projectId": item.get("projectId", ""),
        "cardTitle": item.get("title", ""),
        "acceptedByOperator": False,
        "discardReason": reason or "Operator discarded the suggestion.",
        "workspaceFilesTouched": [
            f"runtime/apply/data/{ADVICE_LOG}",
        ],
        "kanbanFilesTouched": [],
        "teamEsmiFilesTouched": [],
        "kanbanWritePerformed": False,
        "teamEsmiWritePerformed": False,
    }
    _log_update(settings, entry)
    return entry


# ---------------------------------------------------------------------------
# Summary / status helpers
# ---------------------------------------------------------------------------


def get_advice_log_summary(settings: Any) -> dict[str, Any]:
    """Get a summary of adviser activity from the logs.

    Returns dict with counts and log paths.
    """
    data_dir = _apply_data_dir(settings)
    advice_path = data_dir / ADVICE_LOG
    update_path = data_dir / UPDATE_LOG

    advice_count = 0
    update_count = 0

    if advice_path.exists():
        try:
            with open(advice_path, "r", encoding="utf-8") as f:
                advice_count = sum(1 for line in f if line.strip())
        except OSError:
            pass

    if update_path.exists():
        try:
            with open(update_path, "r", encoding="utf-8") as f:
                update_count = sum(1 for line in f if line.strip())
        except OSError:
            pass

    return {
        "adviceLogPath": str(advice_path),
        "updateLogPath": str(update_path),
        "adviceLogCount": advice_count,
        "updateLogCount": update_count,
        "adviserEnabled": True,
    }


# ---------------------------------------------------------------------------
# Structured evidence schema — phase 5 local-model output
# ---------------------------------------------------------------------------

LOCAL_MODEL_OUTPUT_SCHEMA = {
    "type": "object",
    "required": [
        "card_id_or_title", "search_status", "evidence_strength", "confidence",
        "current_state_draft", "next_action_draft", "status_recommendation",
        "risk_recommendation", "evidence_items", "missing_evidence",
        "conflicts_or_cautions", "apply_recommendation",
        "requires_human_approval", "mailboxMutated", "kanbanWritePerformed",
        "teamEsmiWritePerformed",
    ],
    "properties": {
        "card_id_or_title": {"type": "string"},
        "search_status": {"type": "string"},
        "evidence_strength": {"type": "string", "enum": ["strong", "moderate", "weak", "inconclusive", "none"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "current_state_draft": {"type": "string"},
        "next_action_draft": {"type": "string"},
        "status_recommendation": {"type": "string", "enum": ["running", "backlog", "blocked", "completed", ""]},
        "risk_recommendation": {"type": "string", "enum": ["red", "amber", "green", ""]},
        "evidence_items": {"type": "array", "items": {"type": "string"}},
        "missing_evidence": {"type": "array", "items": {"type": "string"}},
        "conflicts_or_cautions": {"type": "array", "items": {"type": "string"}},
        "apply_recommendation": {"type": "string", "enum": ["review_draft", "more_evidence_needed", "do_not_apply"]},
        "requires_human_approval": {"type": "boolean", "const": True},
        "mailboxMutated": {"type": "boolean", "const": False},
        "kanbanWritePerformed": {"type": "boolean", "const": False},
        "teamEsmiWritePerformed": {"type": "boolean", "const": False},
    },
}


def _validate_model_output(output: dict) -> tuple[bool, list[str]]:
    """Validate model output against the schema."""
    errors = []
    for field in LOCAL_MODEL_OUTPUT_SCHEMA["required"]:
        if field not in output:
            errors.append(f"missing required field: {field}")

    # Type checks
    type_map = {
        "card_id_or_title": str, "search_status": str, "evidence_strength": str,
        "confidence": (int, float), "current_state_draft": str, "next_action_draft": str,
        "status_recommendation": str, "risk_recommendation": str,
        "evidence_items": list, "missing_evidence": list, "conflicts_or_cautions": list,
        "apply_recommendation": str, "requires_human_approval": bool,
        "mailboxMutated": bool, "kanbanWritePerformed": bool, "teamEsmiWritePerformed": bool,
    }
    for field, expected_type in type_map.items():
        if field in output:
            if not isinstance(output[field], expected_type):
                errors.append(f"{field}: expected {expected_type.__name__}, got {type(output[field]).__name__}")

    # Const checks
    const_fields = [
        ("requires_human_approval", True),
        ("mailboxMutated", False),
        ("kanbanWritePerformed", False),
        ("teamEsmiWritePerformed", False),
    ]
    for field, expected_val in const_fields:
        if field in output and output[field] != expected_val:
            errors.append(f"{field}: expected {expected_val}, got {output[field]}")

    # Enum checks
    enum_checks = [
        ("evidence_strength", ["strong", "moderate", "weak", "inconclusive", "none"]),
        ("status_recommendation", ["running", "backlog", "blocked", "completed", ""]),
        ("risk_recommendation", ["red", "amber", "green", ""]),
        ("apply_recommendation", ["review_draft", "more_evidence_needed", "do_not_apply"]),
    ]
    for field, valid_values in enum_checks:
        if field in output and output[field] not in valid_values:
            errors.append(f"{field}: '{output[field]}' not in {valid_values}")

    return len(errors) == 0, errors


def generate_structured_advice(
    model_input_path: str | Path,
    settings: Any,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Generate structured advice from a local model input manifest.

    Args:
        model_input_path: Path to local_model_input.json from evidence pipeline.
        settings: App settings object (for Ollama config).
        output_path: Optional path to write the output JSON.

    Returns:
        Dict with the structured advice output.
    """
    model_input_path = Path(model_input_path)
    if not model_input_path.exists():
        return {"error": f"Model input not found: {model_input_path}"}

    with open(model_input_path, "r", encoding="utf-8") as f:
        model_input = json.load(f)

    # Build the structured prompt
    base_url = getattr(settings, "ollama_base_url", "http://127.0.0.1:11434")
    model = getattr(settings, "ollama_model", "qwen3:8b")
    timeout = getattr(settings, "ollama_timeout_seconds", 120)

    evidence_summary = model_input.get("evidence_summary", {})
    att_evidence = model_input.get("attachment_evidence", [])
    inbox_count = evidence_summary.get("inbox_messages", 0)
    sent_count = evidence_summary.get("sent_messages", 0)
    att_count = evidence_summary.get("attachments", 0)

    # Build prompt
    prompt_parts = [
        "You are a SAMI Kanban adviser. Analyse the following structured evidence and produce a human-reviewed draft.",
        "",
        f"Card: {model_input.get('card_title', '?')}",
        f"Project ID: {model_input.get('card_id', '?')}",
        f"Search status: {model_input.get('search_status', '?')}",
        f"Evidence strength: {model_input.get('evidence_strength', '?')}",
        f"Evidence sources: {inbox_count} inbox, {sent_count} sent messages, {att_count} attachments",
        "",
        "Attachment evidence:",
    ]

    for a in att_evidence[:10]:
        fn = a.get("filename", "?")
        ext = a.get("ext", "?")
        status = a.get("parse_status", "?")
        terms = a.get("all_terms", [])
        prompt_parts.append(f"  - {fn} ({ext}) [{status}]")
        if terms:
            prompt_parts.append(f"    Terms: {', '.join(terms[:10])}")
        if a.get("text_preview"):
            prompt_parts.append(f"    Content: {a['text_preview'][:200]}")

    prompt_parts.append("")
    prompt_parts.append("""Return a JSON object with EXACTLY these fields:
{
  "card_id_or_title": "",
  "search_status": "",
  "evidence_strength": "strong|moderate|weak|inconclusive|none",
  "confidence": 0.0,
  "current_state_draft": "",
  "next_action_draft": "",
  "status_recommendation": "running|backlog|blocked|completed|",
  "risk_recommendation": "red|amber|green|",
  "evidence_items": [],
  "missing_evidence": [],
  "conflicts_or_cautions": [],
  "apply_recommendation": "review_draft|more_evidence_needed|do_not_apply",
  "requires_human_approval": true,
  "mailboxMutated": false,
  "kanbanWritePerformed": false,
  "teamEsmiWritePerformed": false
}""")

    full_prompt = "\n".join(prompt_parts)
    system = "You are a SAMI Kanban adviser producing structured card update drafts. Always return valid JSON."

    try:
        ok, msg, parsed = generate(
            base_url=base_url,
            model=model,
            system_prompt=system,
            user_prompt=full_prompt,
            timeout=timeout,
        )

        if ok and isinstance(parsed, dict):
            # Validate
            valid, errors = _validate_model_output(parsed)
            if not valid:
                # Fix const fields
                for field, val in [("requires_human_approval", True),
                                   ("mailboxMutated", False),
                                   ("kanbanWritePerformed", False),
                                   ("teamEsmiWritePerformed", False)]:
                    parsed[field] = val
                parsed["_validation_errors"] = errors

            # Fill defaults for any missing fields
            for field in LOCAL_MODEL_OUTPUT_SCHEMA["required"]:
                if field not in parsed:
                    parsed[field] = "" if isinstance("", str) else []

            result = {
                "success": True,
                "model": model,
                "model_input_path": str(model_input_path),
                "output": parsed,
                "validation_errors": errors if not valid else [],
            }
        else:
            # Fallback: build a minimal valid output
            result = {
                "success": True,
                "model": model,
                "model_input_path": str(model_input_path),
                "output": {
                    "card_id_or_title": model_input.get("card_title", ""),
                    "search_status": model_input.get("search_status", "unknown"),
                    "evidence_strength": model_input.get("evidence_strength", "none"),
                    "confidence": 0.3,
                    "current_state_draft": "[Awaiting human review — model parse failed]",
                    "next_action_draft": "",
                    "status_recommendation": "",
                    "risk_recommendation": "",
                    "evidence_items": [],
                    "missing_evidence": [],
                    "conflicts_or_cautions": [f"Model output could not be parsed: {msg[:200]}"],
                    "apply_recommendation": "more_evidence_needed",
                    "requires_human_approval": True,
                    "mailboxMutated": False,
                    "kanbanWritePerformed": False,
                    "teamEsmiWritePerformed": False,
                    "_fallback": True,
                },
                "_raw": msg,
                "validation_errors": [],
            }

        # Write output
        if output_path:
            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            with open(out, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False, default=str)
            print(f"  Structured advice written to {out}")

        # Log to advice log
        _log_advice(settings, {
            "eventType": "structured_advice",
            "cardId": model_input.get("card_id", ""),
            "cardTitle": model_input.get("card_title", ""),
            "runId": model_input.get("run_id", ""),
            "model": model,
            "searchStatus": model_input.get("search_status", ""),
            "evidenceStrength": model_input.get("evidence_strength", ""),
            "outputSummary": result.get("output", {}).get("current_state_draft", "")[:200],
            "confidence": result.get("output", {}).get("confidence", 0),
            "mailboxMutated": False,
            "kanbanWritePerformed": False,
        })

        return result

    except Exception as e:
        result = {
            "success": False,
            "error": str(e),
            "model": model,
            "model_input_path": str(model_input_path),
        }
        return result


def check_local_ai_status(settings: Any) -> dict[str, Any]:
    """Check local AI backend status: Ollama reachable, model available, JSON mode."""
    base_url = getattr(settings, "ollama_base_url", "http://127.0.0.1:11434")
    model = getattr(settings, "ollama_model", "qwen3:8b")

    # Check Ollama reachable
    reachable, msg, models = check_ollama(base_url)

    # Check specific model
    model_ok = False
    model_msg = ""
    if reachable:
        model_ok, model_msg = check_model_available(base_url, model)

    # Test JSON mode with a simple prompt
    json_mode = False
    test_msg = ""
    if reachable and model_ok:
        try:
            ok, m, parsed = generate(
                base_url=base_url,
                model=model,
                system_prompt="You are a helpful assistant. Return only JSON.",
                user_prompt='{"test": "hello"}',
                timeout=10,
            )
            json_mode = ok
            test_msg = "JSON mode test passed" if ok else f"JSON test: {m[:100]}"
        except Exception as e:
            test_msg = f"Test error: {e}"

    return {
        "ollama_reachable": reachable,
        "ollama_msg": msg,
        "models_available": models,
        "model_configured": model,
        "model_available": model_ok,
        "model_msg": model_msg,
        "json_mode_available": json_mode,
        "test_response": test_msg,
        "base_url": base_url,
    }
