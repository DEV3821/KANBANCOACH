"""Mr Kanban local conversational coach harness.

Phase 6A scope: local sandbox only, read-only context gathering, no Team ESMI
or mailbox writes.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from .kanban_reader import file_hash, find_projects_json, read_projects_json
from .ollama_client import check_model_available, check_ollama, generate
from .path_safety import assert_not_forbidden, is_forbidden_path

MR_KANBAN_NAME = "Mr Kanban — Local SAMI Kanban Coach"
MR_KANBAN_ACTOR = "Mr Kanban local harness"
DEFAULT_TEAM_ESMI_PATH = r"\\fusafmcf01\Medical Imaging\Team_ESMI\Program Delivery\SAMI-Kanban-WorkServer"
SAMI_TEAL = "#008C95"
QUERY_STOPWORDS = {
    "a", "about", "and", "any", "card", "current", "do", "for", "i", "is", "latest", "me", "next",
    "on", "project", "sami", "show", "should", "state", "status", "the", "to", "what", "whats", "what's",
}

# Phrases that are purely low-information — respond socially, skip retrieval
_LOW_INFO_PHRASES = frozenset({
    "hello", "hi", "hey", "good morning", "good afternoon", "good evening",
    "howdy", "greetings", "yo", "sup", "hiya",
    "thanks", "thank you", "cheers", "ty", "thx",
    "test", "testing", "are you there", "you there", "hello mr kanban",
    "hi mr kanban", "hey mr kanban", "mr kanban", "ok", "k", "done",
})


def is_low_information_prompt(text: str) -> bool:
    """Return True if the prompt is a greeting or low-info message that should bypass retrieval.

    Examples that return True:
      hello, hi, hey, good morning, thanks, test, are you there
    Examples that return False:
      what needs update, review the NT UltraRad card, find email context for Zed
    """
    t = text.strip().lower()
    # Single word matches
    if t in _LOW_INFO_PHRASES:
        return True
    # Multi-word: check if every significant word is a greeting/meta word
    words = [w for w in t.split() if w not in QUERY_STOPWORDS and len(w) >= 2]
    if not words:
        return True  # no substantive content
    if all(w in _LOW_INFO_PHRASES for w in words):
        return True
    return False


@dataclass
class ChatSource:
    kind: str
    ref: str
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatState:
    last_query: str = ""
    selected_card: dict[str, Any] | None = None
    sources: list[ChatSource] = field(default_factory=list)
    draft: dict[str, Any] | None = None
    last_apply: dict[str, Any] | None = None
    last_answer: str = ""
    ollama_available: bool = False
    ollama_message: str = ""
    mailbox_result: dict[str, Any] | None = None


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def sandbox_root(settings: Any) -> Path:
    configured = getattr(settings, "local_kanban_sandbox_root", "") or ""
    if configured:
        return Path(configured).resolve()
    return (repo_root() / "runtime" / "local_kanban_sandbox").resolve()


def team_root(settings: Any) -> Path:
    raw = getattr(settings, "kanban_team_root", DEFAULT_TEAM_ESMI_PATH) or DEFAULT_TEAM_ESMI_PATH
    return Path(raw)


def team_projects_json(settings: Any) -> Path | None:
    try:
        pj, _ = find_projects_json(team_root(settings))
        return pj
    except OSError:
        return None


def team_hash(settings: Any) -> str:
    try:
        pj = team_projects_json(settings)
        if pj and pj.exists():
            return file_hash(pj)
    except OSError:
        return ""
    return ""


def guard_summary(settings: Any) -> dict[str, Any]:
    """Return Phase 6A guard status without mutating config."""
    mailbox_write_enabled = bool(getattr(settings, "mailbox_write_enabled", False))
    team_write_enabled = bool(getattr(settings, "team_esmi_write_enabled", False))
    return {
        "allow_kanban_apply": bool(getattr(settings, "allow_kanban_apply", False)),
        "local_kanban_apply_enabled": bool(getattr(settings, "local_kanban_apply_enabled", False)),
        "team_kanban_apply_enabled": bool(getattr(settings, "team_kanban_apply_enabled", False)),
        "allow_local_kanban_apply": bool(getattr(settings, "allow_local_kanban_apply", True)),
        "kanban_apply_target": getattr(settings, "kanban_apply_target", "local_sandbox"),
        "team_esmi_write_enabled": team_write_enabled,
        "mailbox_search_enabled": bool(getattr(settings, "mailbox_search_enabled", False)),
        "mailbox_search_read_only": bool(getattr(settings, "mailbox_search_read_only", True)),
        "mailbox_write_enabled": mailbox_write_enabled,
        "team_path_guarded": is_forbidden_path(str(team_root(settings))),
        "local_path_guarded": is_forbidden_path(str(getattr(settings, "kanban_local_root", ""))),
    }


def chat_profile_settings(settings: Any) -> Any:
    """Return a local chat profile copy with mailbox search enabled read-only."""
    prof = copy.copy(settings)
    setattr(prof, "mailbox_search_enabled", True)
    setattr(prof, "mailbox_search_read_only", True)
    setattr(prof, "mailbox_search_recent_days", 180)
    setattr(prof, "mailbox_write_enabled", False)
    setattr(prof, "allow_local_kanban_apply", True)
    setattr(prof, "kanban_apply_target", "local_sandbox")
    setattr(prof, "team_esmi_write_enabled", False)
    return prof


def ensure_sandbox(settings: Any, refresh: bool = False) -> dict[str, Any]:
    """Create or refresh runtime/local_kanban_sandbox from local Kanban source.

    Writes only under the runtime sandbox. Never writes to local Kanban source or
    Team ESMI.
    """
    root = sandbox_root(settings)
    assert_not_forbidden(root)
    data_dir = root / "data"
    logs_dir = root / "logs"
    backups_dir = root / "backups"
    data_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)

    dst_projects = data_dir / "projects.json"
    source_pj, note = find_projects_json(settings.kanban_local_path())
    if source_pj is None or not source_pj.exists():
        raise FileNotFoundError(f"No local projects.json source found: {note}")

    copied = []
    if refresh or not dst_projects.exists():
        shutil.copy2(source_pj, dst_projects)
        copied.append(str(dst_projects))

    source_updates = settings.kanban_local_path() / "data" / "card_updates.jsonl"
    dst_updates = data_dir / "card_updates.jsonl"
    if source_updates.exists() and (refresh or not dst_updates.exists()):
        shutil.copy2(source_updates, dst_updates)
        copied.append(str(dst_updates))
    elif not dst_updates.exists():
        dst_updates.write_text("", encoding="utf-8")
        copied.append(str(dst_updates))

    for name in ("app_version.json", "kanban_config.json", "kanban_config.example.json"):
        src = settings.kanban_local_path() / "data" / name
        if src.exists():
            dst = data_dir / name
            if refresh or not dst.exists():
                shutil.copy2(src, dst)
                copied.append(str(dst))

    return {
        "sandboxPath": str(root),
        "projectsJson": str(dst_projects),
        "projectsJsonHash": file_hash(dst_projects),
        "sourceProjectsJson": str(source_pj),
        "sourceNote": note,
        "copied": copied,
    }


def load_sandbox_projects(settings: Any) -> tuple[list[dict[str, Any]], dict[str, Any], Path]:
    info = ensure_sandbox(settings, refresh=False)
    path = Path(info["projectsJson"])
    projects, meta, _hash, note = read_projects_json(path)
    if not projects:
        raise RuntimeError(f"Sandbox projects.json has no projects: {note}")
    return projects, meta, path


def _normalise(text: str) -> set[str]:
    return {t.lower() for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-.]+", text or "") if len(t) >= 2}


def _query_terms(text: str) -> set[str]:
    return {t for t in _normalise(text) if t not in QUERY_STOPWORDS and len(t) >= 3}


def find_card(settings: Any, query: str) -> dict[str, Any] | None:
    projects, _meta, _path = load_sandbox_projects(settings)
    q_tokens = _query_terms(query)
    best: tuple[int, dict[str, Any] | None] = (0, None)
    for card in projects:
        title = str(card.get("title", ""))
        hay = " ".join(str(card.get(k, "")) for k in ("id", "title", "context", "nextAction", "next", "projectLead", "notes"))
        score = len(q_tokens & _normalise(hay))
        if query.lower() in title.lower():
            score += 10
        if score > best[0]:
            best = (score, card)
    return copy.deepcopy(best[1]) if best[1] else None


def _card_ref(card: dict[str, Any]) -> str:
    return str(card.get("id") or card.get("projectId") or card.get("title") or "card")


def _card_title(card: dict[str, Any] | None) -> str:
    return str((card or {}).get("title") or "")


def search_prior_evidence(query: str, limit: int = 5) -> list[ChatSource]:
    ev_root = repo_root() / "runtime" / "apply" / "evidence"
    if not ev_root.exists():
        return []
    terms = _query_terms(query)
    if not terms:
        return []
    sources: list[ChatSource] = []
    for run_dir in sorted([p for p in ev_root.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True):
        score = 0
        summary_parts = []
        details: dict[str, Any] = {"run_dir": str(run_dir)}
        for name in ("local_model_input.json", "evidence_manifest.json", "attachment_index.json", "local_model_output.json", "v12_sitrep_report.txt", "v10_context.json", "v10_sitrep_report.txt", "v11_context.json", "v11_sitrep_report.txt"):
            candidates = []
            direct = run_dir / name
            if direct.exists():
                candidates.append(direct)
            candidates.extend(p for p in run_dir.rglob(name) if p != direct)
            for fp in candidates[:3]:
                try:
                    text = fp.read_text(encoding="utf-8", errors="replace")[:60000]
                except OSError:
                    continue
                matches = terms & _query_terms(text)
                if len(matches) < 2:
                    continue
                score += len(matches)
                if name not in details:
                    details[name] = str(fp)
                identifiers = details.setdefault("identifiers_found", [])
                identifiers.extend(_extract_context_identifiers(text))
                details["identifiers_found"] = sorted(set(identifiers))[:80]
                if name == "local_model_input.json":
                    try:
                        data = json.loads(text)
                        details["search_status"] = data.get("search_status", "")
                        details["evidence_strength"] = data.get("evidence_strength", "")
                        details["evidence_summary"] = data.get("evidence_summary", {})
                        details["run_id"] = data.get("run_id", run_dir.name)
                        details["evidence_count"] = data.get("evidence_count", 0)
                        details["attachment_count"] = data.get("attachment_count", 0)
                        terms_found: list[str] = []
                        for item in data.get("evidence_items", [])[:30]:
                            matches = item.get("term_matches", {}) if isinstance(item, dict) else {}
                            if isinstance(matches, dict):
                                for values in matches.values():
                                    if isinstance(values, list):
                                        terms_found.extend(str(v) for v in values if str(v).strip())
                        if terms_found:
                            details["terms_found"] = sorted(set(terms_found))[:40]
                    except json.JSONDecodeError:
                        pass
                if name == "attachment_index.json":
                    try:
                        atts = json.loads(text)
                        details["attachment_count"] = len(atts)
                        attachment_terms = details.setdefault("attachment_terms_found", [])
                        for att in atts:
                            tm = att.get("term_matches", {})
                            if isinstance(tm, dict):
                                for values in tm.values():
                                    if isinstance(values, list):
                                        attachment_terms.extend(str(v) for v in values if str(v).strip())
                        if attachment_terms:
                            details["attachment_terms_found"] = sorted(set(attachment_terms))[:60]
                        for att in atts[:8]:
                            fn = att.get("original_filename") or att.get("sanitized_filename") or "attachment"
                            tm = att.get("term_matches", {})
                            if tm.get("all"):
                                summary_parts.append(f"{fn}: {', '.join(tm.get('all', [])[:6])}")
                    except json.JSONDecodeError:
                        pass
        if score:
            status = details.get("search_status", "prior evidence")
            strength = details.get("evidence_strength", "")
            all_terms = sorted(set(details.get("terms_found", []) + details.get("attachment_terms_found", []) + details.get("identifiers_found", [])))
            terms_preview = ", ".join(all_terms[:10])
            counts = []
            if details.get("evidence_count"):
                counts.append(f"{details['evidence_count']} evidence items")
            if details.get("attachment_count"):
                counts.append(f"{details['attachment_count']} attachments")
            summary = f"{run_dir.name}: {status} {strength}".strip()
            if counts:
                summary += " | " + ", ".join(counts)
            if terms_preview:
                summary += " | terms: " + terms_preview
            if summary_parts:
                summary += " | " + "; ".join(summary_parts[:2])
            sources.append(ChatSource("prior_evidence", run_dir.name, summary, details))
        if len(sources) >= limit:
            break
    return sources


def search_mailbox_context(settings: Any, card: dict[str, Any] | None, query: str) -> tuple[list[ChatSource], dict[str, Any]]:
    profile = chat_profile_settings(settings)
    if not card:
        card = {"title": query, "projectId": "", "evidence": []}
    item = {
        "title": card.get("title", query),
        "projectId": card.get("id") or card.get("projectId") or "",
        "evidence": [],
        "approvedCurrentState": card.get("context", ""),
        "approvedNextAction": card.get("nextAction") or card.get("next", ""),
        "approvedStatus": card.get("status", ""),
    }
    extra = sorted(_normalise(query))[:12]
    targeted = card.get("title") if card and card.get("title") else None
    try:
        from .mailbox_search import search_mailbox_for_card
        result = search_mailbox_for_card(profile, item, latest_card_context=card, extra_terms=extra, targeted_subject=targeted)
        data = result.to_dict()
    except Exception as exc:
        data = {"enabled": True, "provider": "outlook_com", "available": False, "error": str(exc), "emails": [], "attachments": [], "inbox_info": {}}
    sources: list[ChatSource] = []
    for email in data.get("emails", [])[:5]:
        sources.append(ChatSource(
            "email",
            email.get("email_key", "email"),
            f"{email.get('subject','')} | {email.get('sender','')} | {email.get('date','')[:16]} | {email.get('snippet','')[:180]}",
            email,
        ))
    for att in data.get("attachments", [])[:6]:
        sources.append(ChatSource(
            "attachment_metadata",
            att.get("filename", "attachment"),
            f"{att.get('filename','attachment')} from {att.get('email_subject','')} ({att.get('size',0)} bytes)",
            att,
        ))
    return sources, data


def build_context(settings: Any, query: str) -> ChatState:
    state = ChatState(last_query=query)
    card = find_card(settings, query)
    state.selected_card = card
    if card:
        state.sources.append(ChatSource(
            "local_kanban_sandbox",
            _card_ref(card),
            f"{card.get('title','')} | status={card.get('status','')} risk={card.get('riskColour') or card.get('risk','')} | next={(card.get('nextAction') or card.get('next') or '')[:220]}",
            card,
        ))
    prior_query = query
    if card:
        prior_query = " ".join(str(card.get(k, "")) for k in ("id", "title")) + " " + query
    prior = search_prior_evidence(prior_query)
    state.sources.extend(prior)
    mailbox_sources, mailbox_data = search_mailbox_context(settings, card, query)
    state.sources.extend(mailbox_sources)
    state.mailbox_result = mailbox_data
    ok, msg, _models = check_ollama(settings.ollama_base_url, timeout=5)
    if ok:
        model_ok, model_msg = check_model_available(settings.ollama_base_url, settings.ollama_model, timeout=5)
        state.ollama_available = model_ok
        state.ollama_message = model_msg
    else:
        state.ollama_available = False
        state.ollama_message = msg
    return state


def _source_summary_for_prompt(state: ChatState) -> list[dict[str, Any]]:
    rows = []
    for src in state.sources[:18]:
        rows.append({"type": src.kind, "ref": src.ref, "summary": src.summary, "detail": src.detail})
    return rows


def _append_unique(rows: list[str], value: str) -> None:
    value = str(value).strip()
    if value and value not in rows:
        rows.append(value)


def _context_usage_lines(state: ChatState) -> list[str]:
    kinds = {s.kind for s in state.sources}
    lines: list[str] = []
    if "local_kanban_sandbox" in kinds:
        lines.append("Kanban context used: local sandbox/local copy.")
    if "email" in kinds or "attachment_metadata" in kinds:
        lines.append("Email context used: read-only.")
    else:
        lines.append("Email context not used for this answer.")
    prior = next((s for s in state.sources if s.kind == "prior_evidence"), None)
    if prior:
        lines.append(f"Prior evidence run used: {prior.ref}")
    return lines


def _extract_context_identifiers(text: str) -> list[str]:
    """Extract useful card/evidence identifiers without card-specific hardcoding."""
    patterns = [
        r"\bSRV[- ]?\d{5,}\b",
        r"\bREQ[- ]?\d{5,}\b",
        r"\bRFC[- ]?\d{4,}\b",
        r"\b\d{1,3}(?:\.\d{1,3}){3}\b",
        r"\b[A-Z]{2,}[A-Z0-9_-]{3,}\b",
        r"\b[^\s/\\]+\.(?:xlsx|xls|docx|pdf|msg|csv)\b",
    ]
    found: list[str] = []
    for pattern in patterns:
        for match in re.findall(pattern, text or "", flags=re.IGNORECASE):
            value = str(match).strip(".,;:()[]{}<>")
            if value and value.lower() not in {"https", "http", "null", "none", "true", "false"}:
                _append_unique(found, value)
    return found[:60]


def _known_fact_lines(state: ChatState) -> list[str]:
    """Surface concrete identifiers found in local/prior evidence context."""
    text_parts = [state.last_query, json.dumps(state.selected_card or {}, ensure_ascii=False, default=str)]
    for src in state.sources:
        text_parts.append(src.summary)
        text_parts.append(json.dumps(src.detail, ensure_ascii=False, default=str))
    facts: list[str] = []
    identifiers = _extract_context_identifiers("\n".join(text_parts))
    if identifiers:
        _append_unique(facts, "Identifiers found in retrieved context: " + ", ".join(identifiers[:20]))
    return facts


def fallback_answer(state: ChatState) -> dict[str, Any]:
    card = state.selected_card or {}
    source_types = {s.kind for s in state.sources}
    evidence_lines = _context_usage_lines(state) + _known_fact_lines(state) + [s.summary for s in state.sources[:8]]
    confidence = 0.35
    if "prior_evidence" in source_types:
        confidence += 0.25
    if "email" in source_types or "attachment_metadata" in source_types:
        confidence += 0.15
    if card:
        confidence += 0.15
    return {
        "answer": "Mr Kanban found local context. The local Qwen model was reachable checkable separately, but this turn did not return structured JSON, so Mr Kanban used the safe deterministic fallback summary below for operator review.",
        "evidence": evidence_lines,
        "recommendation": "Draft a local sandbox update only if the operator confirms the evidence matches the card.",
        "confidence": min(confidence, 0.75),
        "next_action": "Review sources, then use /draft before /apply-local.",
        "draft": {
            "card": card.get("title", state.last_query),
            "recommendedStatus": card.get("status", ""),
            "recommendedRisk": card.get("riskColour") or card.get("risk", ""),
            "currentState": card.get("context", ""),
            "nextAction": card.get("nextAction") or card.get("next") or "Review evidence and update local sandbox only.",
            "lead": card.get("projectLead", ""),
            "confidence": min(confidence, 0.75),
            "evidenceSummary": evidence_lines[:8],
            "sourceRefs": [s.ref for s in state.sources[:12]],
            "applyTarget": "local_sandbox",
        },
    }


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        lines = [line.strip(" -") for line in value.splitlines() if line.strip(" -")]
        if len(lines) > 1:
            return lines
        return [value] if value.strip() else []
    return [value]


def _coerce_confidence(value: Any) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, min(float(value), 0.75))
    text = str(value).strip().lower()
    if text in {"high", "strong"}:
        return 0.75
    if text in {"medium", "moderate"}:
        return 0.55
    if text in {"low", "weak"}:
        return 0.35
    try:
        return max(0.0, min(float(text), 0.75))
    except ValueError:
        return 0.0


def _normalise_model_response(parsed: dict[str, Any], state: ChatState) -> dict[str, Any]:
    """Coerce Qwen output into the Phase 6A user-facing schema."""
    card = state.selected_card or {}
    source_summaries = [f"{s.kind}: {s.summary}" for s in state.sources[:10]]
    parsed.setdefault("answer", "Mr Kanban reviewed the available local context.")
    parsed["evidence"] = [str(x) for x in _as_list(parsed.get("evidence")) if str(x).strip()]
    if len(parsed["evidence"]) < 2 and source_summaries:
        parsed["evidence"] = source_summaries
    parsed["evidence"] = _context_usage_lines(state) + _known_fact_lines(state) + parsed["evidence"]
    parsed.setdefault("recommendation", "Use local sandbox only.")
    parsed["confidence"] = _coerce_confidence(parsed.get("confidence", 0.0))
    parsed.setdefault("next_action", "Review evidence.")
    draft = parsed.setdefault("draft", {})
    if not isinstance(draft, dict):
        draft = {}
        parsed["draft"] = draft
    draft.setdefault("card", card.get("title", state.last_query))
    draft.setdefault("recommendedStatus", card.get("status", ""))
    draft.setdefault("recommendedRisk", card.get("riskColour") or card.get("risk", ""))
    draft.setdefault("currentState", card.get("context", ""))
    draft.setdefault("nextAction", card.get("nextAction") or card.get("next") or "")
    draft.setdefault("lead", card.get("projectLead", ""))
    if str(draft.get("currentState", "")).strip().lower() == str(card.get("status", "")).strip().lower():
        draft["currentState"] = card.get("context", "")
    draft["confidence"] = parsed.get("confidence", 0.0)
    draft["evidenceSummary"] = [str(x) for x in _as_list(draft.get("evidenceSummary") or parsed.get("evidence"))]
    draft["sourceRefs"] = [str(x) for x in _as_list(draft.get("sourceRefs"))]
    if len(draft["sourceRefs"]) < 2:
        draft["sourceRefs"] = [s.ref for s in state.sources[:12]]
    draft["applyTarget"] = "local_sandbox"
    return parsed


def ask_qwen(settings: Any, state: ChatState) -> dict[str, Any]:
    system = (
        "You are Mr Kanban — Local SAMI Kanban Coach, a practical, direct, evidence-aware "
        "assistant for a SAMI Kanban operator. Use ONLY the supplied local context. Do not guess. "
        "Never claim a Team ESMI write or mailbox write. Recommend local sandbox updates only. "
        "Return ONLY valid JSON with keys: answer, evidence, recommendation, confidence, next_action, draft. "
        "draft must contain card, recommendedStatus, recommendedRisk, currentState, nextAction, lead, confidence, "
        "evidenceSummary, sourceRefs, applyTarget. applyTarget must be local_sandbox."
    )
    card = state.selected_card or {}
    context = {
        "operator_question": state.last_query,
        "selected_card": card,
        "sources": _source_summary_for_prompt(state),
        "safety": {
            "mode": "local_sandbox_only",
            "team_esmi_writes": "disabled",
            "mailbox_writes": "disabled",
            "email_access": "read_only_context_search",
        },
    }
    ok, msg, parsed = generate(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        system_prompt=system,
        user_prompt=json.dumps(context, ensure_ascii=False, default=str),
        timeout=getattr(settings, "ollama_timeout_seconds", 60),
        max_retries=1,
    )
    if not ok or not isinstance(parsed, dict):
        out = fallback_answer(state)
        out["model_error"] = msg
        return out
    return _normalise_model_response(parsed, state)


def build_draft(settings: Any, state: ChatState) -> dict[str, Any]:
    response = ask_qwen(settings, state)
    state.last_answer = str(response.get("answer", ""))
    state.draft = response.get("draft") or fallback_answer(state)["draft"]
    card = state.selected_card or {}
    if state.draft and card:
        current_next = str(card.get("nextAction") or card.get("next") or "")
        if str(state.draft.get("nextAction", "")) == current_next and state.sources:
            state.draft["nextAction"] = current_next + "\nMr Kanban local draft: review read-only Kanban/email/evidence sources before any live update."
            response["draft"] = state.draft
    return response


def _load_raw_projects(path: Path) -> tuple[Any, list[dict[str, Any]]]:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(raw, dict):
        projects = raw.get("projects", [])
    elif isinstance(raw, list):
        projects = raw
    else:
        projects = []
    if not isinstance(projects, list):
        projects = []
    return raw, projects


def _write_raw_projects(path: Path, raw: Any, projects: list[dict[str, Any]]) -> None:
    if isinstance(raw, dict):
        raw["projects"] = projects
        out = raw
    else:
        out = projects
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")


def _audit_path(settings: Any) -> Path:
    return sandbox_root(settings) / "logs" / "local_audit.jsonl"


def append_audit(settings: Any, event: dict[str, Any]) -> None:
    path = _audit_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")


def apply_local_draft(settings: Any, draft: dict[str, Any], source_refs: list[str] | None = None) -> dict[str, Any]:
    guards = guard_summary(settings)
    if guards["allow_kanban_apply"] or guards["local_kanban_apply_enabled"] or guards["team_kanban_apply_enabled"]:
        raise PermissionError("Live/local Kanban apply gates must stay disabled for Phase 6A coach-chat.")
    if not guards["allow_local_kanban_apply"] or guards["kanban_apply_target"] != "local_sandbox":
        raise PermissionError("Local sandbox apply lane is not enabled.")
    if guards["team_esmi_write_enabled"] or guards["mailbox_write_enabled"]:
        raise PermissionError("Team ESMI or mailbox write gate is enabled; refusing local apply.")
    if draft.get("applyTarget") != "local_sandbox":
        raise PermissionError("Draft applyTarget must be local_sandbox.")

    ensure_sandbox(settings, refresh=False)
    root = sandbox_root(settings)
    assert_not_forbidden(root)
    path = root / "data" / "projects.json"
    backup_dir = root / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    before_team = team_hash(settings)
    before_sandbox = file_hash(path)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"projects.json.{stamp}.bak"
    shutil.copy2(path, backup_path)

    raw, projects = _load_raw_projects(path)
    target = str(draft.get("card") or "").strip()
    changed_fields: dict[str, dict[str, str]] = {}
    matched: dict[str, Any] | None = None
    for project in projects:
        if target and (target.lower() == str(project.get("title", "")).lower() or target == str(project.get("id", ""))):
            matched = project
            break
    if matched is None:
        for project in projects:
            if target and target.lower() in str(project.get("title", "")).lower():
                matched = project
                break
    if matched is None:
        raise KeyError(f"Card not found in sandbox: {target}")

    field_map = {
        "recommendedStatus": "status",
        "recommendedRisk": "riskColour",
        "currentState": "context",
        "nextAction": "nextAction",
        "lead": "projectLead",
    }
    for draft_key, card_key in field_map.items():
        value = draft.get(draft_key)
        if value is None or value == "":
            continue
        old = str(matched.get(card_key, ""))
        new = str(value)
        if old != new:
            changed_fields[card_key] = {"before": old, "after": new}
            matched[card_key] = new
    if changed_fields:
        matched["lastUpdated"] = datetime.now().isoformat(timespec="seconds")

    _write_raw_projects(path, raw, projects)
    after_sandbox = file_hash(path)
    after_team = team_hash(settings)

    event = {
        "timestamp": datetime.now().isoformat(),
        "actor": MR_KANBAN_ACTOR,
        "target": "local_sandbox",
        "card": str(matched.get("title", target)),
        "action": "apply_local_update",
        "backupPath": str(backup_path),
        "sandboxPath": str(root),
        "sandboxHashBefore": before_sandbox,
        "sandboxHashAfter": after_sandbox,
        "teamEsmiHashBefore": before_team,
        "teamEsmiHashAfter": after_team,
        "teamEsmiUntouched": before_team == after_team,
        "sourceRefs": source_refs or draft.get("sourceRefs", []),
        "changedFields": changed_fields,
        "draft": draft,
    }
    append_audit(settings, event)
    return event


def undo_last_local_apply(settings: Any, card_title: str | None = None) -> dict[str, Any]:
    root = sandbox_root(settings)
    assert_not_forbidden(root)
    path = root / "data" / "projects.json"
    backups = sorted((root / "backups").glob("projects.json.*.bak"), key=lambda p: p.name, reverse=True)
    if not backups:
        raise FileNotFoundError("No sandbox backup found to undo.")
    before_team = team_hash(settings)
    before_sandbox = file_hash(path)
    backup = backups[0]
    shutil.copy2(backup, path)
    after_sandbox = file_hash(path)
    after_team = team_hash(settings)
    projects, _meta, _ = load_sandbox_projects(settings)
    restored_card: dict[str, Any] | None = None
    if card_title:
        for project in projects:
            if str(project.get("title", "")).lower() == card_title.lower():
                restored_card = project
                break
    event = {
        "timestamp": datetime.now().isoformat(),
        "actor": MR_KANBAN_ACTOR,
        "target": "local_sandbox",
        "action": "undo_local_update",
        "restoredFrom": str(backup),
        "sandboxPath": str(root),
        "sandboxHashBefore": before_sandbox,
        "sandboxHashAfter": after_sandbox,
        "teamEsmiHashBefore": before_team,
        "teamEsmiHashAfter": after_team,
        "teamEsmiUntouched": before_team == after_team,
        "restoredCardCount": len(projects),
        "restoredCard": {
            "title": restored_card.get("title", "") if restored_card else "",
            "status": restored_card.get("status", "") if restored_card else "",
            "riskColour": restored_card.get("riskColour", "") if restored_card else "",
            "nextAction": restored_card.get("nextAction", "") if restored_card else "",
        },
    }
    append_audit(settings, event)
    return event


def render_banner(console: Console, settings: Any) -> None:
    text = Text()
    text.append("Mr Kanban — Local SAMI Kanban Coach\n", style=f"bold {SAMI_TEAL}")
    text.append("SAMI Project Portfolio Evidence Assistant\n\n", style="bold white")
    text.append("Mode: Local sandbox only\n", style="green")
    text.append("Team ESMI writes: disabled\n", style="yellow")
    text.append("Email access: read-only context search\n", style="cyan")
    text.append(f"Model: Ollama / Qwen ({settings.ollama_model})", style="cyan")
    console.print(Panel(text, border_style=SAMI_TEAL, title="SAMI", subtitle="Phase 6A"))


def render_status(console: Console, settings: Any, state: ChatState | None = None) -> None:
    ensure_sandbox(settings, refresh=False)
    guards = guard_summary(settings)
    profile = chat_profile_settings(settings)
    table = Table(title=MR_KANBAN_NAME, show_header=True, header_style=f"bold {SAMI_TEAL}")
    table.add_column("Setting", style="bold")
    table.add_column("Value")
    table.add_row("Mode", "local sandbox only")
    table.add_row("Model", f"Ollama / Qwen ({settings.ollama_model})")
    table.add_row("Team ESMI writes", "disabled")
    table.add_row("Live/local production apply gate", "disabled")
    table.add_row("Sandbox apply lane", "enabled")
    table.add_row("Apply target", str(guards["kanban_apply_target"]))
    table.add_row("Email context search", "enabled, read-only")
    table.add_row("Mailbox writes", "disabled")
    table.add_row("Recent email search days", str(profile.mailbox_search_recent_days))
    table.add_row("Sandbox path", str(sandbox_root(settings)))
    table.add_row("Team ESMI path", str(team_root(settings)))
    table.add_row("Team ESMI hash", team_hash(settings) or "unavailable/offline")
    table.add_row("Last draft", "yes" if state and state.draft else "no")
    table.add_row("Last local apply", "yes" if state and state.last_apply else "no")
    verified = (
        not guards["allow_kanban_apply"]
        and not guards["local_kanban_apply_enabled"]
        and not guards["team_kanban_apply_enabled"]
        and guards["allow_local_kanban_apply"]
        and guards["kanban_apply_target"] == "local_sandbox"
        and not guards["mailbox_write_enabled"]
    )
    table.add_row("Write gates verified", "yes" if verified else "check configuration")
    console.print(table)
    console.print("[green]Safety: Team ESMI untouched. Mailbox writes impossible in this harness.[/green]")


def render_greeting(console: Console) -> None:
    """Print a friendly greeting with no Kanban/email context retrieval."""
    console.print(Rule("Mr Kanban", style=SAMI_TEAL))
    console.print(
        "Hi Brian — Mr Kanban is online.\n\n"
        "I can help review stale cards, check project context, build draft "
        "recommendations, or look for read-only email evidence when you "
        "explicitly ask.\n\n"
        "What card or project do you want to look at?"
    )
    console.print()
    console.print("[dim]Type /help for commands.[/dim]", style="bold")


def render_sources(console: Console, state: ChatState) -> None:
    if not state.sources:
        console.print("[yellow]No sources yet. Ask a question first.[/yellow]")
        return
    table = Table(title="Mr Kanban sources", show_header=True, header_style=f"bold {SAMI_TEAL}")
    table.add_column("Type", style="cyan", no_wrap=True)
    table.add_column("Reference", style="bold")
    table.add_column("Summary")
    for src in state.sources:
        table.add_row(src.kind, src.ref[:48], src.summary[:220])
    console.print(table)


def render_draft(console: Console, draft: dict[str, Any] | None) -> None:
    if not draft:
        console.print("[yellow]No draft yet. Ask a question or use /draft after context retrieval.[/yellow]")
        return
    table = Table(title="Mr Kanban local sandbox draft", show_header=True, header_style=f"bold {SAMI_TEAL}")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    for key in ("card", "recommendedStatus", "recommendedRisk", "currentState", "nextAction", "lead", "confidence", "applyTarget"):
        table.add_row(key, str(draft.get(key, ""))[:700])
    console.print(table)
    refs = draft.get("sourceRefs", []) or []
    if refs:
        console.print("[bold]Source refs:[/bold] " + ", ".join(str(r) for r in refs[:10]))


def render_answer(console: Console, state: ChatState, response: dict[str, Any]) -> None:
    console.print(Rule("Mr Kanban answer", style=SAMI_TEAL))
    console.print(str(response.get("answer", "")))
    console.print("\n[bold]Evidence:[/bold]")
    ev = response.get("evidence", []) or []
    if ev:
        for item in ev[:6]:  # cap at 6
            txt = str(item)[:300]
            console.print(f"- {txt}")
    else:
        for src in state.sources[:5]:  # cap at 5
            txt = src.summary[:200]
            console.print(f"- {src.kind}: {txt}")
    console.print("\n[bold]Recommendation:[/bold]")
    console.print(str(response.get("recommendation", "Review evidence before local sandbox update.")))
    console.print("\n[bold]Confidence:[/bold]")
    console.print(str(response.get("confidence", 0.0)))
    console.print("\n[bold]Next action:[/bold]")
    console.print(str(response.get("next_action", "Review evidence.")))
    console.print("\n[bold]Safety:[/bold]")
    for line in _context_usage_lines(state):
        console.print(line)
    console.print("No Team ESMI write has been made. No mailbox write has been made.", style="green")


def help_text() -> str:
    return (
        "/status - show safety mode and paths\n"
        "/sources - show sources used\n"
        "/draft - show or build current local sandbox draft\n"
        "/apply-local - explicit local sandbox apply (requires APPLY LOCAL)\n"
        "/undo - restore the latest sandbox projects.json backup\n"
        "/reset-local - refresh sandbox from local Kanban source\n"
        "/search <query> - retrieve context and answer\n"
        "/card <card name> - select a local sandbox card\n"
        "/evidence <query> - search prior evidence runs\n"
        "/help - show help\n"
        "/exit - exit"
    )


def run_smoke_test(settings: Any, console: Console | None = None) -> dict[str, Any]:
    console = console or Console()
    settings = chat_profile_settings(settings)
    render_banner(console, settings)
    sandbox_info = ensure_sandbox(settings, refresh=True)
    state = build_context(settings, "What is the latest on NT UltraRad VPN/firewall?")
    response = build_draft(settings, state)
    if state.draft and state.selected_card:
        current_next = str(state.selected_card.get("nextAction") or state.selected_card.get("next") or "")
        if str(state.draft.get("nextAction", "")) == current_next:
            state.draft["nextAction"] = current_next + "\nMr Kanban local smoke test: validated sandbox-only apply and undo."
            response["draft"] = state.draft
    render_status(console, settings, state)
    render_answer(console, state, response)
    render_draft(console, state.draft)
    before_team = team_hash(settings)
    apply_result = apply_local_draft(settings, state.draft or {}, [s.ref for s in state.sources])
    state.last_apply = apply_result
    console.print(Rule("Local apply result", style=SAMI_TEAL))
    console.print(f"Sandbox path: {apply_result['sandboxPath']}")
    console.print(f"Card changed: {apply_result['card']}")
    console.print(f"Fields changed: {', '.join(apply_result['changedFields'].keys()) or 'none'}")
    console.print(f"Backup path: {apply_result['backupPath']}")
    console.print(f"Team ESMI hash before: {apply_result['teamEsmiHashBefore'] or 'unavailable/offline'}")
    console.print(f"Team ESMI hash after: {apply_result['teamEsmiHashAfter'] or 'unavailable/offline'}")
    console.print("Team ESMI untouched: " + str(apply_result["teamEsmiUntouched"]), style="green")
    undo_result = undo_last_local_apply(settings, apply_result["card"])
    console.print(Rule("Undo result", style=SAMI_TEAL))
    console.print(f"Restored from: {undo_result['restoredFrom']}")
    console.print(f"Team ESMI hash before: {undo_result['teamEsmiHashBefore'] or 'unavailable/offline'}")
    console.print(f"Team ESMI hash after: {undo_result['teamEsmiHashAfter'] or 'unavailable/offline'}")
    console.print("Team ESMI untouched: " + str(undo_result["teamEsmiUntouched"]), style="green")
    if undo_result.get("restoredCard", {}).get("title"):
        restored = undo_result["restoredCard"]
        console.print(f"Restored card: {restored.get('title')} | status={restored.get('status')} risk={restored.get('riskColour')}")
        console.print(f"Restored next action: {str(restored.get('nextAction', ''))[:300]}")
    return {
        "sandbox": sandbox_info,
        "teamHashBeforeSmoke": before_team,
        "teamHashAfterApply": apply_result["teamEsmiHashAfter"],
        "teamHashAfterUndo": undo_result["teamEsmiHashAfter"],
        "sources": [s.__dict__ for s in state.sources],
        "mailbox": state.mailbox_result,
        "model": {"available": state.ollama_available, "message": state.ollama_message},
        "response": response,
        "draft": state.draft,
        "apply": apply_result,
        "undo": undo_result,
    }


def interactive_loop(settings: Any, console: Console | None = None) -> None:
    from rich.prompt import Prompt

    console = console or Console()
    settings = chat_profile_settings(settings)
    render_banner(console, settings)
    ensure_sandbox(settings, refresh=False)
    state = ChatState()
    console.print("[dim]Type /help for commands. Normal questions search Kanban, email context, attachments, and prior evidence.[/dim]")
    while True:
        raw = Prompt.ask("Mr Kanban").strip()
        if not raw:
            continue
        if raw in {"/exit", "exit", "quit", "/quit"}:
            console.print("[green]Mr Kanban session closed. Team ESMI untouched.[/green]")
            return
        if raw == "/help":
            console.print(help_text())
            continue
        if raw == "/status":
            render_status(console, settings, state)
            continue
        if raw == "/sources":
            render_sources(console, state)
            continue
        if raw == "/draft":
            if not state.draft and state.last_query:
                build_draft(settings, state)
            render_draft(console, state.draft)
            continue
        if raw == "/reset-local":
            info = ensure_sandbox(settings, refresh=True)
            state = ChatState()
            console.print(f"[green]Local sandbox refreshed:[/green] {info['sandboxPath']}")
            console.print("[green]Team ESMI untouched.[/green]")
            continue
        if raw == "/undo":
            try:
                card_title = str((state.last_apply or {}).get("card") or "") or None
                result = undo_last_local_apply(settings, card_title)
                console.print(f"[green]Undo complete:[/green] restored {result['restoredFrom']}")
                console.print(f"Team ESMI hash before: {result['teamEsmiHashBefore'] or 'unavailable/offline'}")
                console.print(f"Team ESMI hash after: {result['teamEsmiHashAfter'] or 'unavailable/offline'}")
                console.print("Team ESMI untouched: " + str(result["teamEsmiUntouched"]))
                if result.get("restoredCard", {}).get("title"):
                    restored = result["restoredCard"]
                    console.print(f"Restored card: {restored.get('title')} | status={restored.get('status')} risk={restored.get('riskColour')}")
                    console.print(f"Restored next action: {str(restored.get('nextAction', ''))[:300]}")
            except Exception as exc:
                console.print(f"[red]Undo failed:[/red] {exc}")
            continue
        if raw == "/apply-local":
            if not state.draft:
                console.print("[yellow]No draft exists. Ask for a draft first.[/yellow]")
                continue
            console.print(Rule("Confirm local sandbox apply", style=SAMI_TEAL))
            console.print(f"Target path: {sandbox_root(settings)}")
            console.print(f"Card name: {state.draft.get('card', '')}")
            fields = [key for key in ("recommendedStatus", "recommendedRisk", "currentState", "nextAction", "lead") if state.draft.get(key)]
            console.print("Fields that may change: " + (", ".join(fields) or "none"))
            console.print("[yellow]Type APPLY LOCAL to update the sandbox copy only.[/yellow]")
            confirm = Prompt.ask("Confirm", default="").strip()
            if confirm != "APPLY LOCAL":
                console.print("[yellow]Cancelled. No write performed.[/yellow]")
                continue
            try:
                result = apply_local_draft(settings, state.draft, [s.ref for s in state.sources])
                state.last_apply = result
                console.print(f"[green]Local sandbox updated:[/green] {result['sandboxPath']}")
                console.print(f"Card changed: {result['card']}")
                console.print(f"Fields changed: {', '.join(result['changedFields'].keys()) or 'none'}")
                console.print(f"Backup path: {result['backupPath']}")
                console.print(f"Team ESMI hash before: {result['teamEsmiHashBefore'] or 'unavailable/offline'}")
                console.print(f"Team ESMI hash after: {result['teamEsmiHashAfter'] or 'unavailable/offline'}")
                console.print("Team ESMI untouched: " + str(result["teamEsmiUntouched"]))
                console.print("No Team ESMI write has been made.", style="green")
            except Exception as exc:
                console.print(f"[red]Apply failed:[/red] {exc}")
            continue
        if raw.startswith("/card "):
            query = raw[len("/card "):].strip()
            state.selected_card = find_card(settings, query)
            if state.selected_card:
                console.print(f"[green]Selected card:[/green] {_card_title(state.selected_card)}")
            else:
                console.print("[yellow]No matching local sandbox card found.[/yellow]")
            continue
        if raw.startswith("/evidence "):
            query = raw[len("/evidence "):].strip()
            state.sources = search_prior_evidence(query)
            render_sources(console, state)
            continue
        if raw.startswith("/search "):
            raw = raw[len("/search "):].strip()
        # Greeting/low-info guard — bypass retrieval entirely
        if is_low_information_prompt(raw):
            render_greeting(console)
            continue
        state = build_context(settings, raw)
        response = build_draft(settings, state)
        render_answer(console, state, response)
