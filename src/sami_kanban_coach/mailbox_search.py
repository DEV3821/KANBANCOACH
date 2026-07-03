"""Pre-export mailbox search for Qwen adviser (Phase 5b, Part A).

Read-only mailbox search across supported providers (Outlook COM, Graph placeholder).
Builds search queries from card context, returns relevant email snippets,
and writes read-only evidence snapshots.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Domain/system terms for search expansion
# ---------------------------------------------------------------------------

_SYSTEM_TERMS = [
    "RIS", "PACS", "UltraRad", "Viewpoint", "PPP",
    "VPN", "SIA", "TPNA", "IAC", "SAMI", "ESMI",
    "DICOM", "EOL", "AVW", "Syngo", "Firewall",
    "Circuit", "Telstra", "NT Health", "Migration",
    "Server", "Azure", "Decommission", "Vendor",
    "Connectivity", "Tunnel", "Upgrade", "Rollout",
    "Stakeholder", "Sponsor", "Steering", "Go-live",
    "NEC", "OCIO", "ACSC", "IPESC",
    "Stroke", "NTGMIPRDG", "Siva", "Faraz",
]

# ---------------------------------------------------------------------------
# Search query builder
# ---------------------------------------------------------------------------


def _tokenise(text: str) -> list[str]:
    """Split text into meaningful tokens (>=3 chars, alphanumeric)."""
    import re
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-\.]+", text)
    return [t for t in tokens if len(t) >= 3]


def build_search_terms(
    card_title: str | None = None,
    project_id: str | None = None,
    current_card: dict[str, Any] | None = None,
    proposed: dict[str, Any] | None = None,
    evidence_list: list[dict[str, Any]] | None = None,
    extra_terms: list[str] | None = None,
) -> list[str]:
    """Build mailbox search terms from card context.

    Priority:
    1. Exact title/phrases
    2. Lead/owner names
    3. Current state keywords
    4. Next action keywords
    5. Proposed recommendation keywords
    6. System/domain terms detected in context
    7. Explicit extra terms (SRV/REQ/IPs etc.)

    Returns an ordered list of search terms (narrow first).
    """
    terms: list[str] = []

    # Card title and project
    if card_title:
        terms.extend(_tokenise(card_title))
    if project_id:
        terms.extend(_tokenise(project_id))

    # Lead / owner
    card = current_card or {}
    lead = (card.get("projectLead") or card.get("owner") or "").strip()
    if lead:
        terms.extend(_tokenise(lead))

    # Current state
    cs = (card.get("context") or "").strip()
    if cs:
        terms.extend(_tokenise(cs))

    # Next action
    na = (card.get("nextAction") or "").strip()
    if na:
        terms.extend(_tokenise(na))

    # Proposed recommendation
    prop = proposed or {}
    for field in ["approvedCurrentState", "approvedNextAction", "approvedStatus"]:
        val = (prop.get(field) or "").strip()
        if val:
            terms.extend(_tokenise(val))

    # Evidence subjects
    if evidence_list:
        for ev in evidence_list:
            subj = (ev.get("subject") or "").strip()
            if subj:
                terms.extend(_tokenise(subj))

    # System terms that appear in any context
    all_text = " ".join([
        card_title or "", project_id or "", cs, na,
        str(prop or {}),
    ])
    all_upper = all_text.upper()
    for st in _SYSTEM_TERMS:
        if st.upper() in all_upper and st not in terms:
            terms.append(st)

    # Explicit extra terms (SRV/REQ/IPs etc.)
    if extra_terms:
        for et in extra_terms:
            if et not in terms:
                terms.append(et)

    # Deduplicate preserving order
    seen = set()
    ordered = []
    for t in terms:
        t_upper = t.upper()
        if t_upper not in seen:
            seen.add(t_upper)
            ordered.append(t)

    return ordered[:20]


# ---------------------------------------------------------------------------
# Mailbox search result
# ---------------------------------------------------------------------------


class MailboxSearchResult:
    """Result of a mailbox search for a card."""

    def __init__(
        self,
        enabled: bool = False,
        provider: str = "disabled",
        available: bool = False,
        query_terms: list[str] | None = None,
        emails: list[dict[str, Any]] | None = None,
        total_scanned: int = 0,
        exact_matches: int = 0,
        fallback_matches: int = 0,
        error: str | None = None,
        inbox_info: dict[str, Any] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> None:
        self.enabled = enabled
        self.provider = provider
        self.available = available
        self.query_terms = query_terms or []
        self.emails = emails or []
        self.total_scanned_or_returned = total_scanned
        self.exact_matches = exact_matches
        self.fallback_matches = fallback_matches
        self.error = error
        self.inbox_info = inbox_info or {}
        self.attachments = attachments or []

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "available": self.available,
            "query_terms": self.query_terms,
            "emails": self.emails,
            "total_scanned_or_returned": self.total_scanned_or_returned,
            "exact_matches": self.exact_matches,
            "fallback_matches": self.fallback_matches,
            "error": self.error,
            "inbox_info": self.inbox_info,
            "attachments": self.attachments,
        }


# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------


def _check_outlook_com_available() -> tuple[bool, str]:
    """Check if Outlook COM provider is available (read-only check)."""
    try:
        import win32com.client  # noqa: F401
        # Light probe — try creating the application object
        app = win32com.client.Dispatch("Outlook.Application")
        # Don't probe deeper — just check Dispatch works
        _ = app  # reference
        return True, "Outlook COM available"
    except ImportError:
        return False, "pywin32 not installed"
    except Exception as e:
        msg = str(e)
        if "Invalid class string" in msg or "Could not obtain" in msg:
            return False, "Outlook desktop not detected"
        # Attempt to query MAPI namespace as a lighter touch
        try:
            import win32com.client
            app = win32com.client.Dispatch("Outlook.Application")
            ns = app.GetNamespace("MAPI")
            _ = ns  # reference
            return True, "Outlook COM available (MAPI namespace)"
        except Exception:
            return False, f"Outlook COM unavailable: {msg}"


def get_mailbox_search_status(settings: Any) -> dict[str, Any]:
    """Get mailbox search status without performing a search.

    Returns dict with enabled, provider, available, paths, etc.
    Does NOT send data to Ollama or search mailbox.
    """
    enabled = getattr(settings, "mailbox_search_enabled", False)
    provider = getattr(settings, "mailbox_search_provider", "disabled")
    read_only = getattr(settings, "mailbox_search_read_only", True)
    max_results = getattr(settings, "mailbox_search_max_results", 10)
    recent_days = getattr(settings, "mailbox_search_recent_days", 180)

    available = False
    available_msg = ""
    if enabled and provider == "outlook_com":
        available, available_msg = _check_outlook_com_available()
    elif enabled and provider == "graph":
        available = False
        available_msg = "Microsoft Graph provider not configured"
    elif enabled:
        available = False
        available_msg = f"Provider '{provider}' not implemented"

    cache_path = getattr(settings, "mailbox_search_cache_path", "")
    snapshot_path = getattr(settings, "mailbox_search_snapshot_path", "")

    # Count existing snapshots
    snapshot_count = 0
    if snapshot_path:
        sp = Path(snapshot_path)
        if sp.exists():
            try:
                with open(sp) as f:
                    snapshot_count = sum(1 for line in f if line.strip())
            except OSError:
                pass

    return {
        "mailboxSearchEnabled": enabled,
        "mailboxSearchProvider": provider,
        "mailboxSearchAvailable": available,
        "mailboxSearchAvailableMsg": available_msg,
        "mailboxSearchReadOnly": read_only,
        "mailboxSearchMaxResults": max_results,
        "mailboxSearchRecentDays": recent_days,
        "cachePath": cache_path,
        "snapshotPath": snapshot_path,
        "snapshotCount": snapshot_count,
    }


# ---------------------------------------------------------------------------
# Outlook COM search
# ---------------------------------------------------------------------------


def _search_outlook_com(
    settings: Any,
    query_terms: list[str],
    max_results: int,
    recent_days: int,
    targeted_subject: str | None = None,
) -> MailboxSearchResult:
    """Search Outlook via COM — read-only, no mutations.

    Strategy:
    1. If targeted_subject provided, search by exact subject first (Items.Find).
    2. If targeted fails or not provided, run chunked month-by-month scan.
    3. Combine results.

    Returns MailboxSearchResult with inbox_info, emails, attachments.
    """
    import calendar
    import hashlib
    import re
    from datetime import timezone

    try:
        import win32com.client
    except ImportError:
        return MailboxSearchResult(
            enabled=True, provider="outlook_com", available=False,
            query_terms=query_terms, error="pywin32 not installed",
        )

    try:
        app = win32com.client.Dispatch("Outlook.Application")
        ns = app.GetNamespace("MAPI")
    except Exception as e:
        return MailboxSearchResult(
            enabled=True, provider="outlook_com", available=False,
            query_terms=query_terms, error=f"Outlook dispatch failed: {e}",
        )

    # Find Inbox and log its identity
    try:
        inbox = ns.GetDefaultFolder(6)  # olFolderInbox = 6
    except Exception as e:
        return MailboxSearchResult(
            enabled=True, provider="outlook_com", available=False,
            query_terms=query_terms, error=f"Cannot open Inbox: {e}",
        )

    # Resolve Inbox identity
    inbox_info: dict = {}
    try:
        store = inbox.Store
        inbox_info["storeDisplayName"] = str(getattr(store, "DisplayName", "?"))
        inbox_info["storeID"] = str(getattr(store, "StoreID", "") or "")[:32]
    except Exception:
        inbox_info["storeDisplayName"] = "?"
    try:
        inbox_info["folderName"] = str(getattr(inbox, "Name", "?"))
        inbox_info["folderPath"] = str(getattr(inbox, "FolderPath", "?"))
        inbox_info["entryID"] = str(getattr(inbox, "EntryID", "") or "")[:32]
    except Exception:
        inbox_info["folderName"] = "?"
    try:
        inbox_info["totalItemCount"] = int(getattr(inbox.Items, "Count", 0))
    except Exception:
        inbox_info["totalItemCount"] = 0
    inbox_info["isInbox"] = inbox_info.get("folderName") == "Inbox"
    inbox_info["search_window_days"] = recent_days
    inbox_info["enumeration_errors_count"] = 0

    cutoff_date = datetime.now(timezone.utc) - timedelta(days=recent_days)

    # Build patterns from query_terms
    patterns = _build_patterns(query_terms)

    # =====================================================================
    # Helper: process one mail item (extract, score, match)
    # =====================================================================
    def _process_item(mail_item: Any) -> dict | None:
        """Process a single mail item. Returns email dict or None."""
        subject = ""; received = None; sender = ""; sender_email = ""; body = ""; entry_id = ""; conv_id = ""
        try: subject = str(mail_item.Subject)
        except: pass
        try: received = mail_item.ReceivedTime
        except: pass
        try: sender = str(mail_item.SenderName)
        except: pass
        try: sender_email = str(mail_item.SenderEmailAddress)
        except: pass
        try: body = str(mail_item.Body)[:2000]  # longer body for context
        except: pass
        try: entry_id = str(mail_item.EntryID)
        except: pass
        try: conv_id = str(mail_item.ConversationID)
        except: pass

        received_str = received.isoformat() if received else ""

        # Score
        score = 0
        matched_reasons = []
        all_text = f"{subject} {sender} {sender_email} {body}"
        for p in patterns:
            if p.search(subject):
                score += 2
                matched_reasons.append("subject matched")
                break
        for p in patterns:
            if p.search(sender) or p.search(sender_email):
                score += 1
                if "sender" not in " ".join(matched_reasons):
                    matched_reasons.append("sender matched")
                break
        for p in patterns:
            if p.search(body):
                score += 1
                break

        if score == 0 and not patterns:
            score = 1  # include if no patterns (targeted match)

        dedup_raw = f"{entry_id}:{subject}:{sender_email}:{received_str}"
        dedup_hash = hashlib.sha256(dedup_raw.encode()).hexdigest()[:24]
        email_key = f"mailbox:{dedup_hash}"
        snippet = body[:300].strip()
        if len(body) > 300:
            snippet += "..."

        return {
            "email_key": email_key, "subject": subject[:200],
            "sender": sender, "sender_email": sender_email,
            "date": received_str, "snippet": snippet,
            "matched_reason": "; ".join(dict.fromkeys(matched_reasons)),
            "relevance_score": score, "source": "mailbox_search",
            "snapshot_id": uuid.uuid4().hex[:12],
            "_raw_received": received,
            "_conv_id": conv_id,
            "_entry_id": entry_id,
        }

    # =====================================================================
    # Helper: extract attachments from a mail item
    # =====================================================================
    def _extract_attachments(mail_item: Any) -> list[dict]:
        atts = []
        try:
            for att in mail_item.Attachments:
                try:
                    atts.append({
                        "name": str(getattr(att, "DisplayName", "") or getattr(att, "FileName", "") or "unnamed"),
                        "size": int(getattr(att, "Size", 0) or 0),
                    })
                except Exception:
                    pass
        except Exception:
            pass
        return atts

    # =====================================================================
    # Strategy A: Targeted subject search
    # =====================================================================
    all_matched_emails: list[dict] = []
    all_attachments: list[dict] = []
    search_strategy = "for_item_in_items"
    chunks_attempted = 0
    chunks_completed = 0
    total_enumerated = 0
    targeted_found = False
    target_thread_count = 0
    search_incomplete_reason = ""

    if targeted_subject:
        inbox_info["search_strategy"] = "targeted_then_chunked"
        inbox_info["targeted_subject_searched"] = True
        # Escape for DASL
        escaped = targeted_subject.replace("'", "''").replace('"', '""')
        dasl_filter = f'@SQL="urn:schemas:httpmail:subject" LIKE \'%{escaped}%\''
        try:
            findings = inbox.Items.Restrict(dasl_filter)
            findings.Sort("[ReceivedTime]", False)  # oldest first for thread
            count = 0
            for item in findings:
                count += 1
                processed = _process_item(item)
                if processed:
                    processed["_targeted_match"] = True
                    all_matched_emails.append(processed)
                    # Extract attachments
                    for att in _extract_attachments(item):
                        dedup_key = f"{att['name']}:{att['size']}"
                        if dedup_key not in {f"{a['filename']}:{a['size']}" for a in all_attachments}:
                            all_attachments.append({
                                "email_subject": processed["subject"],
                                "sender": processed["sender"],
                                "date": processed["date"],
                                "email_key": processed["email_key"],
                                "filename": att["name"],
                                "size": att["size"],
                            })
                # Also capture total items found by targeted search
                target_thread_count += 1
            targeted_found = count > 0
            inbox_info["targeted_subject_found"] = targeted_found
            inbox_info["targeted_subject_searched_value"] = targeted_subject
            inbox_info["targeted_total_found"] = target_thread_count
            search_strategy = "targeted_dasl"
        except Exception as e:
            inbox_info["targeted_subject_found"] = False
            inbox_info["targeted_search_error"] = str(e)[:100]
    else:
        inbox_info["search_strategy"] = "chunked_default"
        inbox_info["targeted_subject_searched"] = False

    # =====================================================================
    # Thread capture by ConversationID (if targeted search found matches)
    # =====================================================================
    conv_ids_found = set()
    for em in all_matched_emails:
        cid = em.get("_conv_id", "")
        if cid and len(cid) > 4:
            conv_ids_found.add(cid)

    if conv_ids_found and len(all_matched_emails) < max_results:
        for cid in conv_ids_found:
            try:
                escaped_cid = cid.replace("'", "''")
                cid_filter = f'@SQL="urn:schemas:httpmail:conversationid" = \'{escaped_cid}\''
                cid_items = inbox.Items.Restrict(cid_filter)
                for cid_item in cid_items:
                    processed = _process_item(cid_item)
                    if processed and processed["email_key"] not in {e["email_key"] for e in all_matched_emails}:
                        if len(all_matched_emails) >= max_results:
                            break
                        all_matched_emails.append(processed)
                        for att in _extract_attachments(cid_item):
                            dedup_key = f"{att['name']}:{att['size']}"
                            if dedup_key not in {f"{a['filename']}:{a['size']}" for a in all_attachments}:
                                all_attachments.append({
                                    "email_subject": processed["subject"],
                                    "sender": processed["sender"],
                                    "date": processed["date"],
                                    "email_key": processed["email_key"],
                                    "filename": att["name"], "size": att["size"],
                                })
            except Exception:
                pass

    # Count total thread messages found (generic, not tied to specific topic)
    thread_count = len(all_matched_emails)
    inbox_info["total_thread_messages_found"] = thread_count
    inbox_info["conv_ids_found"] = len(conv_ids_found)

    # =====================================================================
    # Strategy B: Chunked month-by-month scan (fallback)
    # =====================================================================
    # Only run if we have fewer than max_results matches from targeted
    if len(all_matched_emails) < max_results:
        today = datetime.now(timezone.utc)
        current = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # Start from 365 days ago
        start_boundary = today - timedelta(days=recent_days)
        chunk_start = max(current, start_boundary)

        inbox_info.setdefault("chunks", [])

        while chunk_start >= start_boundary and len(all_matched_emails) < max_results:
            chunk_end = chunk_start + timedelta(days=32)
            chunk_end = chunk_end.replace(day=1) - timedelta(days=1)  # last day of month
            if chunk_end > today:
                chunk_end = today

            chunks_attempted += 1

            # DASL filter for this month
            start_str = chunk_start.strftime("%m/%d/%Y")
            end_str = chunk_end.strftime("%m/%d/%Y")
            month_filter = (
                f'@SQL="urn:schemas:httpmail:datereceived" >= \'{start_str}\''
                f' AND "urn:schemas:httpmail:datereceived" <= \'{end_str}\''
            )

            chunk_count = 0
            chunk_candidates = 0
            chunk_errors = 0
            try:
                chunk_items = inbox.Items.Restrict(month_filter)
                chunk_items.Sort("[ReceivedTime]", True)
                for chunk_item in chunk_items:
                    chunk_count += 1
                    total_enumerated += 1
                    processed = _process_item(chunk_item)
                    if processed:
                        # Check date cutoff
                        rd = processed["_raw_received"]
                        if rd is not None:
                            try:
                                if isinstance(rd, datetime):
                                    if rd.tzinfo is None:
                                        rd = rd.replace(tzinfo=timezone.utc)
                                    if rd < cutoff_date:
                                        # Past cutoff for this chunk — skip
                                        pass
                            except Exception:
                                pass
                            if rd is not None and hasattr(rd, "isoformat"):
                                pass  # already captured above

                        if processed and processed["relevance_score"] > 0:
                            chunk_candidates += 1
                            # Dedup by email_key
                            if processed["email_key"] not in {e["email_key"] for e in all_matched_emails}:
                                all_matched_emails.append(processed)
                                # Extract attachments
                                for att in _extract_attachments(chunk_item):
                                    dedup_key = f"{att['name']}:{att['size']}"
                                    if dedup_key not in {f"{a['filename']}:{a['size']}" for a in all_attachments}:
                                        all_attachments.append({
                                            "email_subject": processed["subject"],
                                            "sender": processed["sender"],
                                            "date": processed["date"],
                                            "email_key": processed["email_key"],
                                            "filename": att["name"],
                                            "size": att["size"],
                                        })
                        # Check if we've scanned past the cutoff for this chunk
                        if processed and processed.get("_raw_received"):
                            rd = processed["_raw_received"]
                            if rd is not None:
                                try:
                                    if isinstance(rd, datetime):
                                        if rd.tzinfo is None:
                                            rd = rd.replace(tzinfo=timezone.utc)
                                        if rd < cutoff_date:
                                            break
                                except Exception:
                                    pass
            except Exception:
                chunk_errors += 1

            inbox_info["chunks"].append({
                "month": chunk_start.strftime("%Y-%m"),
                "start": start_str, "end": end_str,
                "enumerated": chunk_count,
                "candidates": chunk_candidates,
                "errors": chunk_errors,
            })

            if chunk_errors == 0:
                chunks_completed += 1

            # Move to previous month
            current = chunk_start - timedelta(days=1)
            chunk_start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # Sort by relevance
    all_matched_emails.sort(key=lambda e: e.get("relevance_score", 0), reverse=True)
    all_matched_emails = all_matched_emails[:max_results]

    # Strip internal fields
    for e in all_matched_emails:
        e.pop("_raw_received", None)
        e.pop("_targeted_match", None)
        e.pop("_conv_id", None)
        e.pop("_entry_id", None)

    # Build inbox_info
    inbox_info["enumeration_method"] = search_strategy
    inbox_info["chunks_attempted"] = chunks_attempted
    inbox_info["chunks_completed"] = chunks_completed
    inbox_info["total_items_enumerated"] = total_enumerated
    inbox_info["candidate_email_count"] = len(all_matched_emails)
    inbox_info["relevant_email_count"] = len(all_matched_emails)
    inbox_info["attachment_count"] = len(all_attachments)
    inbox_info["scanned_items_count"] = total_enumerated
    inbox_info["search_window_days"] = recent_days

    if not targeted_found and targeted_subject:
        inbox_info["search_incomplete_reason"] = "targeted_subject_not_found_in_inbox"
    elif total_enumerated == 0 and not targeted_subject:
        inbox_info["search_incomplete_reason"] = "chunked_enumeration_empty"

    # Search status classification
    if targeted_found:
        inbox_info["search_status"] = "target_thread_found"
    elif targeted_subject and not targeted_found and chunks_completed > 0:
        inbox_info["search_status"] = "target_thread_not_found_but_chunked_search_complete"
    elif chunks_attempted > 0 and chunks_attempted != chunks_completed:
        inbox_info["search_status"] = "evidence_search_incomplete"
    elif inbox_info.get("search_incomplete_reason"):
        inbox_info["search_status"] = "evidence_search_incomplete"
    else:
        inbox_info["search_status"] = "search_error"

    return MailboxSearchResult(
        enabled=True,
        provider="outlook_com",
        available=True,
        query_terms=query_terms,
        emails=all_matched_emails,
        total_scanned=total_enumerated,
        exact_matches=len(all_matched_emails),
        inbox_info=inbox_info,
        attachments=all_attachments,
    )


def _build_patterns(query_terms: list[str]) -> list[re.Pattern]:
    """Build regex patterns from query terms."""
    import re
    patterns = []
    for t in query_terms:
        if len(t) >= 3:
            try:
                patterns.append(re.compile(re.escape(t), re.IGNORECASE))
            except re.error:
                pass
    return patterns


# ---------------------------------------------------------------------------
# Main mailbox search dispatcher
# ---------------------------------------------------------------------------


def search_mailbox_for_card(
    settings: Any,
    item: dict[str, Any],
    latest_card_context: dict[str, Any] | None = None,
    max_results: int | None = None,
    extra_terms: list[str] | None = None,
    targeted_subject: str | None = None,
) -> MailboxSearchResult:
    """Search mailbox for emails relevant to a plan item.

    Read-only. Never mutates mailbox. Stores evidence snapshots locally.

    Args:
        settings: App settings.
        item: Apply-plan item dict.
        latest_card_context: Latest Team ESMI card context (optional).
        max_results: Max emails to return (overrides config).

    Returns:
        MailboxSearchResult with matched emails.
    """
    enabled = getattr(settings, "mailbox_search_enabled", False)
    provider = getattr(settings, "mailbox_search_provider", "disabled")
    read_only = getattr(settings, "mailbox_search_read_only", True)

    if not enabled:
        return MailboxSearchResult(
            enabled=False, provider=provider, available=False,
            query_terms=[], error="Mailbox search disabled in settings",
        )

    if not read_only:
        return MailboxSearchResult(
            enabled=True, provider=provider, available=False,
            query_terms=[], error="Mailbox search must be read-only — check mailbox_search_read_only setting",
        )

    if max_results is None:
        max_results = getattr(settings, "mailbox_search_max_results", 10)
    recent_days = getattr(settings, "mailbox_search_recent_days", 180)

    # Build search terms
    evidence_list = item.get("evidence", []) or []
    query_terms = build_search_terms(
        card_title=item.get("title", ""),
        project_id=item.get("projectId", ""),
        current_card=latest_card_context,
        proposed=item,
        evidence_list=evidence_list,
        extra_terms=extra_terms,
    )

    # Dispatch by provider
    if provider == "outlook_com":
        result = _search_outlook_com(settings, query_terms, max_results, recent_days, targeted_subject=targeted_subject)
    elif provider == "graph":
        result = MailboxSearchResult(
            enabled=True, provider="graph", available=False,
            query_terms=query_terms,
            error="Microsoft Graph provider not configured. No credentials available.",
        )
    else:
        result = MailboxSearchResult(
            enabled=True, provider=provider, available=False,
            query_terms=query_terms,
            error=f"Provider '{provider}' not implemented",
        )

    # Write evidence snapshots for matched emails
    if result.emails and getattr(settings, "mailbox_search_cache_enabled", True):
        snapshot_path = getattr(settings, "mailbox_search_snapshot_path", "")
        if snapshot_path:
            sp = Path(snapshot_path)
            sp.parent.mkdir(parents=True, exist_ok=True)
            for email in result.emails:
                snapshot = {
                    "timestamp": datetime.now().isoformat(),
                    "applyId": item.get("applyId", ""),
                    "projectId": item.get("projectId", ""),
                    "cardTitle": item.get("title", ""),
                    "source": "mailbox_search",
                    "provider": provider,
                    "emailKey": email.get("email_key", ""),
                    "subject": email.get("subject", ""),
                    "sender": email.get("sender", ""),
                    "date": email.get("date", ""),
                    "snippet": email.get("snippet", "")[:500],
                    "matchedReason": email.get("matched_reason", ""),
                    "relevanceScore": email.get("relevance_score", 0),
                    "mailboxMutated": False,
                }
                with open(sp, "a", encoding="utf-8") as f:
                    f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")

    return result
