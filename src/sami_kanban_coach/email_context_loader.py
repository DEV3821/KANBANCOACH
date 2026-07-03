"""Email context loader for local Qwen adviser (Phase 5, Parts C/E).

Discovers and loads email evidence/source context from:
1. Local evidence folder (exact sourceEmailKeys)
2. Local evidence folder (keyword fallback)
3. Pre-export mailbox search (if enabled)

Read-only. Never mutates mailbox or Team ESMI.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def _get_email_recall_root(settings: Any) -> Path:
    """Get the email recall root path."""
    return getattr(settings, "output_path", lambda: Path())() if hasattr(settings, "output_path") else Path()


def _get_email_data_dir(settings: Any) -> Path:
    root = _get_email_recall_root(settings)
    return root / "data"


def _get_email_evidence_dir(settings: Any) -> Path:
    root = _get_email_recall_root(settings)
    return root / "evidence" / "emails"


def discover_email_context_folder(settings: Any) -> Path | None:
    """Discover the email evidence/context folder from settings."""
    evidence_dir = _get_email_evidence_dir(settings)
    if evidence_dir.exists():
        return evidence_dir
    data_dir = _get_email_data_dir(settings)
    fallback = data_dir.parent / "evidence" / "emails"
    if fallback.exists():
        return fallback
    root = _get_email_recall_root(settings)
    if root.exists():
        candidate = root / "evidence" / "emails"
        if candidate.exists():
            return candidate
    return None


def _load_raw_email_index(settings: Any) -> list[dict[str, Any]]:
    """Load the raw_email_recall.jsonl index."""
    path = _get_email_data_dir(settings) / "raw_email_recall.jsonl"
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    except (json.JSONDecodeError, OSError):
        pass
    return records


def _load_email_from_evidence_dir(evidence_dir: Path, message_key: str) -> dict[str, Any] | None:
    """Load an email JSON from the evidence directory by messageKey."""
    if not evidence_dir or not evidence_dir.exists():
        return None
    try:
        for date_dir in evidence_dir.iterdir():
            if not date_dir.is_dir():
                continue
            for subject_dir in date_dir.iterdir():
                if not subject_dir.is_dir():
                    continue
                email_json = subject_dir / "email.json"
                if not email_json.exists():
                    continue
                try:
                    with open(email_json, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    ek = data.get("messageKey", "") or data.get("id", "")
                    if ek == message_key:
                        return data
                except (json.JSONDecodeError, OSError):
                    continue
    except OSError:
        pass
    return None


def _load_email_body(evidence_dir: Path, message_key: str) -> str:
    """Load the body text for an email from evidence dir."""
    if not evidence_dir or not evidence_dir.exists():
        return ""
    try:
        for date_dir in evidence_dir.iterdir():
            if not date_dir.is_dir():
                continue
            for subject_dir in date_dir.iterdir():
                if not subject_dir.is_dir():
                    continue
                email_json = subject_dir / "email.json"
                if not email_json.exists():
                    continue
                try:
                    with open(email_json, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    ek = data.get("messageKey", "") or data.get("id", "")
                    if ek == message_key:
                        body_txt = subject_dir / "body.txt"
                        if body_txt.exists():
                            return body_txt.read_text(encoding="utf-8", errors="replace")[:5000]
                        body_html = subject_dir / "body.html"
                        if body_html.exists():
                            import re
                            html = body_html.read_text(encoding="utf-8", errors="replace")
                            text = re.sub(r"<[^>]+>", " ", html)
                            text = re.sub(r"\s+", " ", text).strip()
                            return text[:5000]
                except (json.JSONDecodeError, OSError):
                    continue
    except OSError:
        pass
    return ""


def _keyword_search_email_dir(
    evidence_dir: Path,
    keywords: list[str],
    max_results: int,
) -> list[dict[str, Any]]:
    """Search email evidence directory by keyword in subject/sender/content."""
    results: list[dict[str, Any]] = []
    if not evidence_dir or not evidence_dir.exists():
        return results
    try:
        for date_dir in sorted(evidence_dir.iterdir(), reverse=True):
            if not date_dir.is_dir():
                continue
            for subject_dir in date_dir.iterdir():
                if not subject_dir.is_dir():
                    continue
                email_json = subject_dir / "email.json"
                if not email_json.exists():
                    continue
                try:
                    with open(email_json, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except (json.JSONDecodeError, OSError):
                    continue
                subject = (data.get("subject", "") or "").lower()
                sender = (data.get("from", "") or "").lower()
                body_snippet = ""
                body_txt = subject_dir / "body.txt"
                if body_txt.exists():
                    body_snippet = body_txt.read_text(encoding="utf-8", errors="replace")[:500].lower()

                matched = False
                for kw in keywords:
                    kwl = kw.lower()
                    if kwl in subject or kwl in sender or kwl in body_snippet:
                        matched = True
                        break

                if matched:
                    entry = {
                        "messageKey": data.get("messageKey", "") or data.get("id", ""),
                        "subject": data.get("subject", ""),
                        "from": data.get("from", ""),
                        "receivedAt": data.get("receivedAt", "") or data.get("received_at", ""),
                        "matched_reason": "keyword search",
                    }
                    if entry["messageKey"] not in {r.get("messageKey") for r in results}:
                        results.append(entry)
                        if len(results) >= max_results:
                            return results
    except OSError:
        pass
    return results


# ---------------------------------------------------------------------------
# Main combined loader
# ---------------------------------------------------------------------------


def load_email_context(
    settings: Any,
    source_email_keys: list[str] | None = None,
    evidence_list: list[dict[str, Any]] | None = None,
    card_title: str | None = None,
    project_name: str | None = None,
    max_count: int = 8,
    max_chars: int = 12000,
    item: dict[str, Any] | None = None,
    extra_search_terms: list[str] | None = None,
    targeted_subject: str | None = None,
) -> dict[str, Any]:
    """Load relevant email context for a card from all sources.

    Priority:
    1. Local evidence folder — exact sourceEmailKeys match
    2. Local evidence folder — keyword fallback
    3. Pre-export mailbox search (if enabled)

    Args:
        settings: Application settings.
        source_email_keys: sourceEmailKeys from the apply plan item.
        evidence_list: evidence list from the apply plan item.
        card_title: Card title for keyword fallback.
        project_name: Project name for keyword fallback.
        max_count: Maximum emails to return.
        max_chars: Maximum total characters of email snippets.
        item: Full apply-plan item (for mailbox search context).

    Returns:
        Dict with 'emails' list and metadata counts.
    """
    evidence_dir = discover_email_context_folder(settings)
    max_count = min(max_count, getattr(settings, "local_ai_max_email_count", max_count))

    seen_keys: set[str] = set()
    emails: list[dict[str, Any]] = []
    total_chars = 0
    local_exact = 0
    local_fallback = 0

    def _add_email(data: dict[str, Any], reason: str, source: str = "local") -> None:
        nonlocal total_chars
        ek = data.get("email_key", "") or data.get("messageKey", "") or data.get("id", "")
        if not ek or ek in seen_keys:
            return
        if len(emails) >= max_count:
            return
        body = _load_email_body(evidence_dir, ek) if evidence_dir else ""
        snippet = body[:min(800, max_chars - total_chars)] if body else (
            data.get("summary", "") or data.get("bodyPreview", "") or ""
        )[:min(400, max_chars - total_chars)]
        char_add = len(snippet)
        if total_chars + char_add > max_chars:
            if total_chars >= max_chars:
                return
            snippet = snippet[:max_chars - total_chars]
            char_add = len(snippet)
        entry = {
            "email_key": ek,
            "subject": data.get("subject", ""),
            "from": data.get("from", "") or data.get("from_", "") or data.get("sender", ""),
            "date": data.get("date", "") or data.get("receivedAt", "") or data.get("received_at", ""),
            "snippet": snippet,
            "matched_reason": reason,
            "source": source,
        }
        emails.append(entry)
        seen_keys.add(ek)
        total_chars += char_add

    # Stage 1: Exact sourceEmailKeys from local evidence
    keys_to_try: list[str] = []
    if source_email_keys:
        keys_to_try.extend(source_email_keys)
    if evidence_list:
        for ev in evidence_list:
            mk = ev.get("messageKey", "") or ev.get("messageKey", "")
            if mk:
                keys_to_try.append(mk)
    keys_to_try = list(dict.fromkeys(keys_to_try))

    raw_index = _load_raw_email_index(settings)
    raw_by_key: dict[str, dict[str, Any]] = {}
    for rec in raw_index:
        rec_key = rec.get("messageKey", "") or rec.get("id", "")
        if rec_key:
            raw_by_key[rec_key] = rec

    for key in keys_to_try:
        if len(emails) >= max_count:
            break
        ev_data = _load_email_from_evidence_dir(evidence_dir, key) if evidence_dir else None
        if ev_data:
            _add_email(ev_data, "sourceEmailKeys exact match (evidence dir)")
            local_exact += 1
            continue
        if key in raw_by_key:
            _add_email(raw_by_key[key], "sourceEmailKeys exact match (index)")
            local_exact += 1
            continue

    # Stage 2: Keyword fallback in local evidence
    if len(emails) < max_count:
        keywords = []
        if card_title:
            words = card_title.split()
            keywords.extend(w for w in words if len(w) > 3)
        if project_name:
            keywords.extend(w for w in project_name.split() if len(w) > 3)
        if not keywords and card_title:
            keywords.append(card_title)
        if keywords:
            remaining = max_count - len(emails)
            fallback_results = _keyword_search_email_dir(evidence_dir, keywords, remaining) if evidence_dir else []
            for fb in fallback_results:
                if len(emails) >= max_count:
                    break
                if fb.get("messageKey") not in seen_keys:
                    fb["matched_reason"] = "keyword search fallback"
                    email_entry = {
                        "email_key": fb.get("messageKey", ""),
                        "subject": fb.get("subject", ""),
                        "from": fb.get("from", ""),
                        "date": fb.get("receivedAt", ""),
                        "snippet": "",
                        "matched_reason": "keyword search fallback",
                        "source": "local",
                    }
                    emails.append(email_entry)
                    seen_keys.add(fb.get("messageKey", ""))
                    local_fallback += 1

    # Stage 3: Pre-export mailbox search (if enabled)
    mailbox_matches = 0
    mailbox_provider = "disabled"
    mailbox_search_used = False
    search_terms_used: list[str] = []
    inbox_info: dict = {}
    attachment_evidence: list = []

    if getattr(settings, "mailbox_search_enabled", False) and item:
        try:
            from .mailbox_search import search_mailbox_for_card

            mailbox_result = search_mailbox_for_card(settings, item, extra_terms=extra_search_terms, targeted_subject=targeted_subject)
            mailbox_provider = mailbox_result.provider
            mailbox_search_used = bool(mailbox_result.available and mailbox_result.emails)

            if mailbox_result.query_terms:
                search_terms_used = mailbox_result.query_terms

            # Capture inbox identity
            inbox_info = mailbox_result.inbox_info or {}

            # Capture attachment evidence
            attachment_evidence = []
            for att in mailbox_result.attachments or []:
                attachment_evidence.append({
                    "email_subject": att.get("email_subject", ""),
                    "sender": att.get("sender", ""),
                    "date": att.get("date", ""),
                    "email_key": att.get("email_key", ""),
                    "filename": att.get("filename", ""),
                    "size": att.get("size", 0),
                })

            if mailbox_result.available and mailbox_result.emails:
                remaining = max_count - len(emails)
                for me in mailbox_result.emails[:remaining]:
                    ek = me.get("email_key", "")
                    if ek and ek not in seen_keys:
                        mb_entry = {
                            "email_key": ek,
                            "subject": me.get("subject", ""),
                            "from": me.get("sender", ""),
                            "date": me.get("date", ""),
                            "snippet": me.get("snippet", "")[:600],
                            "matched_reason": me.get("matched_reason", "mailbox search"),
                            "source": "mailbox_search",
                        }
                        emails.append(mb_entry)
                        seen_keys.add(ek)
                        mailbox_matches += 1
                        if len(emails) >= max_count:
                            break
        except Exception:
            pass

    return {
        "emails": emails,
        "localExactMatches": local_exact,
        "localFallbackMatches": local_fallback,
        "mailboxPreExportMatches": mailbox_matches,
        "searchTermsUsed": search_terms_used,
        "totalCandidatesScanned": len(emails),
        "truncated": len(emails) >= max_count,
        "mailboxSearchUsed": mailbox_search_used,
        "mailboxProvider": mailbox_provider,
        "inbox_info": inbox_info,
        "attachment_evidence": attachment_evidence,
        "attachment_count": len(attachment_evidence),
    }
