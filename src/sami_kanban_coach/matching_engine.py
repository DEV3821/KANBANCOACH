"""Read-only email-to-card matching engine for Phase 2.

Uses deterministic local scoring only — no AI calls.
Matches captured emails to known Kanban cards by title phrases,
aliases, keywords, lead/owner names, and body term overlap.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .kanban_models import CardIndex
from .kanban_indexer import load_card_index
from .logging_setup import setup_logging
from .path_safety import assert_not_forbidden

logger = setup_logging(Path("runtime/email_recall/logs"))

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_STOPWORDS_PATH = Path("config/matching_stopwords.json")
_ALIASES_PATH = Path("config/project_aliases.json")

_CONFIDENCE_MATCHED = 0.85
_CONFIDENCE_POSSIBLE = 0.60


def _load_stopwords() -> set[str]:
    """Load stopwords from config file."""
    try:
        with open(_STOPWORDS_PATH, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        logger.warning("Stopwords file not found or invalid, using small built-in set.")
        return {"sami", "pacs", "ris", "project", "update", "email", "request"}


def _load_aliases() -> dict[str, list[str]]:
    """Load project aliases from config file."""
    try:
        with open(_ALIASES_PATH, "r", encoding="utf-8") as f:
            return dict(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------
STOPWORDS = _load_stopwords()
ALIASES = _load_aliases()

_RE_NON_ALPHA = re.compile(r"[^a-z0-9\s-]")
_RE_WHITESPACE = re.compile(r"\s+")


def _tokenize(text: str) -> set[str]:
    """Lowercase, strip punctuation, split into tokens, remove stopwords."""
    t = text.lower()
    t = _RE_NON_ALPHA.sub(" ", t)
    tokens = _RE_WHITESPACE.split(t)
    return {tok for tok in tokens if tok and tok not in STOPWORDS and len(tok) > 1}


def _tokenize_preserve_hyphens(text: str) -> set[str]:
    """Tokenize but keep hyphenated phrases as single tokens."""
    t = text.lower()
    t = re.sub(r"[^a-z0-9\s-]", " ", t)
    tokens = _RE_WHITESPACE.split(t)
    return {tok for tok in tokens if tok and tok not in STOPWORDS and len(tok) > 1}


def _phrases(text: str, min_len: int = 3, max_len: int = 6) -> set[str]:
    """Generate sliding n-gram phrases from text."""
    tokens = sorted(_tokenize(text))
    if len(tokens) < min_len:
        return set()
    phrases: set[str] = set()
    for n in range(min_len, min(max_len, len(tokens)) + 1):
        for i in range(len(tokens) - n + 1):
            phrase = " ".join(tokens[i:i+n])
            phrases.add(phrase)
    return phrases


def _extract_ref_numbers(text: str) -> set[str]:
    """Extract reference/label numbers like SAMI-12B88A, SR# 521202, REQ2026637."""
    refs: set[str] = set()
    for m in re.finditer(r"(?:SAMI[-:])?([A-Z0-9]{4,})", text.upper()):
        refs.add(m.group(0))
    for m in re.finditer(r"(?:SR[#\s]?|REQ)(\d{5,})", text.upper()):
        refs.add(m.group(0))
    return refs


# ---------------------------------------------------------------------------
# Card features
# ---------------------------------------------------------------------------
class CardFeatures:
    """Precomputed features for a card used during matching."""

    def __init__(self, card: dict[str, Any]) -> None:
        self.card = card
        self.project_id = (card.get("projectId") or "")
        self.title = (card.get("title") or "")
        self.status = (card.get("status") or "")
        self.lead = (card.get("lead") or "").lower().strip()
        self.owner = (card.get("owner") or "").lower().strip()
        self.current_state = (card.get("currentState") or "")
        self.next_action = (card.get("nextAction") or "")
        self.notes = (card.get("notes") or "")
        self.keywords = [str(k).lower().strip() for k in (card.get("keywords") or []) if k]
        self.tags = [str(t).lower().strip() for t in (card.get("tags") or []) if t]

        # Token sets
        self.title_tokens: set[str] = _tokenize_preserve_hyphens(self.title)
        self.state_tokens: set[str] = _tokenize(self.current_state)
        self.action_tokens: set[str] = _tokenize(self.next_action)
        self.notes_tokens: set[str] = _tokenize(self.notes)

        # All content tokens for broad matching
        self.all_tokens: set[str] = (
            self.title_tokens | self.state_tokens | self.action_tokens | self.notes_tokens
        )

        # Title phrases (bigrams/trigrams from title)
        title_words = [w for w in self.title.lower().split() if w not in STOPWORDS and len(w) > 1]
        self.title_phrases: set[str] = set()
        for n in range(2, min(4, len(title_words) + 1)):
            for i in range(len(title_words) - n + 1):
                self.title_phrases.add(" ".join(title_words[i:i+n]))

        # Aliases from config
        self.aliases: list[str] = [a.lower() for a in ALIASES.get(self.project_id, [])]

        # People
        self.people_names: set[str] = set()
        for kw in self.keywords:
            self.people_names.add(kw)

    def has_alias(self, text_lower: str) -> bool:
        """Check if any alias appears in the given lowercase text."""
        for alias in self.aliases:
            if alias in text_lower:
                return True
        return False

    def has_keyword(self, text_lower: str) -> bool:
        """Check if any keyword/name appears in the given lowercase text."""
        for kw in self.keywords:
            if kw in text_lower:
                return True
        return False


# ---------------------------------------------------------------------------
# Match signals
# ---------------------------------------------------------------------------
class MatchSignal:
    """A single piece of evidence for a match."""

    def __init__(self, signal_type: str, value: str, weight: float) -> None:
        self.type = signal_type
        self.value = value
        self.weight = weight

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "value": self.value, "weight": self.weight}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def _compute_signals(
    email_subject: str,
    email_body_preview: str,
    email_from: str,
    features: CardFeatures,
) -> list[MatchSignal]:
    """Compute all match signals between an email and a card.

    Returns weighted signals. Signal weights are additive toward confidence.
    """
    signals: list[MatchSignal] = []
    subj_lower = email_subject.lower()
    body_lower = email_body_preview.lower()
    combined_lower = f"{subj_lower} {body_lower}"
    from_lower = email_from.lower()

    # 1. Exact title phrase in subject (highest weight)
    for phrase in features.title_phrases:
        if phrase in subj_lower:
            signals.append(MatchSignal("title_phrase", phrase, min(0.35, 0.15 + 0.05 * len(phrase.split()))))
            break  # one title phrase match is enough

    # 2. Exact title phrase in body
    if not any(s.type == "title_phrase" for s in signals):
        for phrase in features.title_phrases:
            if phrase in body_lower:
                signals.append(MatchSignal("title_phrase", phrase, 0.20))
                break

    # 3. Aliases (from config)
    if features.has_alias(combined_lower):
        for alias in features.aliases:
            if alias in combined_lower:
                signals.append(MatchSignal("alias", alias, 0.25))
                break

    # 4. Reference numbers
    email_refs = _extract_ref_numbers(combined_lower)
    card_refs = _extract_ref_numbers(f"{features.title} {features.current_state} {features.next_action} {features.notes}")
    common_refs = email_refs & card_refs
    for ref in common_refs:
        signals.append(MatchSignal("body_term", ref, 0.30))

    # 5. Token overlap
    email_tokens = _tokenize(combined_lower)
    overlap = email_tokens & features.all_tokens
    if len(overlap) >= 3:
        weight = min(0.20, 0.05 * len(overlap))
        top_terms = sorted(overlap, key=lambda t: -len(t))[:3]
        signals.append(MatchSignal("keyword", ", ".join(top_terms), weight))

    # 6. Lead/owner names in email
    if features.lead and features.lead not in ("to be assigned", "n/a", ""):
        lead_parts = features.lead.lower().split()
        for part in lead_parts:
            if len(part) > 2 and part in from_lower:
                signals.append(MatchSignal("lead", features.lead, 0.15))
                break

    if features.owner and features.owner not in ("",):
        owner_parts = features.owner.lower().split()
        for part in owner_parts:
            if len(part) > 2 and part in from_lower:
                signals.append(MatchSignal("lead", features.owner, 0.10))
                break

    # 7. Keywords/people in email
    if features.has_keyword(combined_lower):
        for kw in features.keywords:
            if kw in combined_lower:
                signals.append(MatchSignal("keyword", kw, 0.10))
                break

    # 8. Body term overlap (broader)
    body_tokens = _tokenize(body_lower)
    card_content = features.title_tokens | features.state_tokens | features.action_tokens
    body_overlap = body_tokens & card_content
    if len(body_overlap) >= 4:
        weight = min(0.15, 0.03 * len(body_overlap))
        signals.append(MatchSignal("body_term", f"{len(body_overlap)} shared terms", weight))

    return signals


def _compute_confidence(signals: list[MatchSignal]) -> float:
    """Compute overall confidence from signals.

    Weights are additive but capped at 1.0.
    Threads (same signal type) are not double-counted beyond the highest.
    """
    by_type: dict[str, float] = {}
    for sig in signals:
        current = by_type.get(sig.type, 0.0)
        if sig.weight > current:
            by_type[sig.type] = sig.weight

    base = sum(by_type.values())
    return min(1.0, base)


def _make_decision(
    confidence: float,
    signals: list[MatchSignal],
    email_subject: str,
) -> tuple[str, bool]:
    """Determine match decision and whether human review is needed.

    Returns (decision, requires_human_review).
    """
    if confidence >= _CONFIDENCE_MATCHED:
        return "matched", False
    if confidence >= _CONFIDENCE_POSSIBLE:
        return "possible_match", True
    # Check if email looks like a potential new project
    subj_lower = email_subject.lower()
    has_project_language = any(
        term in subj_lower
        for term in ["new project", "proposal", "initiative", "new system",
                     "procurement", "business case", "pilot", "scope"]
    )
    if has_project_language and confidence > 0.2:
        return "possible_new_project", True
    return "unmatched", True


# ---------------------------------------------------------------------------
# Main matching pipeline
# ---------------------------------------------------------------------------
def run_matching(
    email_recall_root: Path,
    kanban_index_root: Path,
    matching_root: Path,
    since_hours: int = 72,
) -> dict[str, Any]:
    """Run the email-to-card matching pipeline.

    Args:
        email_recall_root: Path to email_recall root (containing data/).
        kanban_index_root: Path to kanban_index root (containing data/).
        matching_root: Path to matching output root.
        since_hours: Only process emails received within this many hours.

    Returns:
        Summary dict with match counts.
    """
    logger.info("Starting matching run (since_hours=%d)", since_hours)
    index_data_dir = matching_root / "data"
    index_data_dir.mkdir(parents=True, exist_ok=True)
    assert_not_forbidden(index_data_dir)
    cutoff = datetime.now() - timedelta(hours=since_hours)

    # --- Load emails ---
    email_path = email_recall_root / "data" / "raw_email_recall.jsonl"
    emails: list[dict[str, Any]] = []
    if email_path.exists():
        with open(email_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    emails.append(json.loads(line))
    logger.info("Loaded %d email records from %s", len(emails), email_path)

    # Filter by time
    filtered_emails = []
    for e in emails:
        captured_at = e.get("capturedAt") or e.get("receivedAt") or ""
        try:
            dt = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
            if dt >= cutoff:
                filtered_emails.append(e)
        except (ValueError, TypeError):
            filtered_emails.append(e)  # include if unparseable
    logger.info("%d emails within %dh window", len(filtered_emails), since_hours)

    # --- Load cards ---
    cards = load_card_index(kanban_index_root)
    card_dicts = [c.model_dump() for c in cards]
    card_features = [CardFeatures(c) for c in card_dicts]
    logger.info("Loaded %d card features", len(card_features))

    if not card_features:
        logger.warning("No cards loaded — cannot match.")
        return {"emails_scanned": 0, "matched": 0, "possible_match": 0,
                "unmatched": 0, "possible_new_project": 0}

    # --- Match each email ---
    matches: list[dict[str, Any]] = []
    possible_matches: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    possible_new: list[dict[str, Any]] = []

    for email in emails:  # process ALL emails in the file (not just filtered)
        email_subject = email.get("subject") or ""
        email_body_preview = email.get("bodyPreview") or email.get("bodyText") or ""
        email_from = email.get("senderEmail") or email.get("senderName") or ""
        email_msg_key = email.get("messageKey") or ""
        email_received = email.get("receivedAt") or email.get("capturedAt") or ""
        email_evidence = email.get("evidenceFolder") or ""

        # Score against each card
        best_signals: list[MatchSignal] = []
        best_card_id = ""
        best_card_title = ""
        best_confidence = 0.0
        best_card_preview = ""
        best_card_next = ""

        for feat in card_features:
            signals = _compute_signals(
                email_subject, email_body_preview, email_from, feat
            )
            confidence = _compute_confidence(signals)
            if confidence > best_confidence:
                best_confidence = confidence
                best_signals = signals
                best_card_id = feat.project_id
                best_card_title = feat.title
                best_card_preview = (feat.current_state or "")[:200]
                best_card_next = (feat.next_action or "")[:200]

        decision, needs_review = _make_decision(best_confidence, best_signals, email_subject)

        match_record: dict[str, Any] = {
            "schemaVersion": 1,
            "generatedAt": datetime.now().isoformat(),
            "emailMessageKey": email_msg_key,
            "emailSubject": email_subject,
            "emailFrom": email_from,
            "emailReceivedAt": email_received,
            "emailEvidenceFolder": email_evidence,
            "matchedProjectId": best_card_id,
            "matchedTitle": best_card_title,
            "confidence": round(best_confidence, 4),
            "decision": decision,
            "matchedSignals": [s.to_dict() for s in best_signals],
            "emailPreview": (email_body_preview or "")[:500],
            "cardCurrentStatePreview": best_card_preview,
            "cardNextActionPreview": best_card_next,
            "requiresHumanReview": needs_review,
        }

        if decision == "unmatched":
            unmatched.append(match_record)
        elif decision == "possible_new_project":
            possible_new.append(match_record)
        elif decision == "possible_match":
            possible_matches.append(match_record)
        else:
            matches.append(match_record)

    # --- Write outputs ---
    match_path = index_data_dir / "email_card_matches.jsonl"
    with open(match_path, "w", encoding="utf-8") as f:
        for m in matches + possible_matches:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")

    unmatch_path = index_data_dir / "unmatched_emails.jsonl"
    with open(unmatch_path, "w", encoding="utf-8") as f:
        for m in unmatched:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")

    new_proj_path = index_data_dir / "possible_new_project_emails.jsonl"
    with open(new_proj_path, "w", encoding="utf-8") as f:
        for m in possible_new:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")

    # --- Write summary ---
    summary = {
        "schemaVersion": 1,
        "generatedAt": datetime.now().isoformat(),
        "sinceHours": since_hours,
        "emailsScanned": len(emails),
        "emailsInWindow": len(filtered_emails),
        "cardsAvailable": len(card_features),
        "matched": len(matches),
        "possibleMatch": len(possible_matches),
        "unmatched": len(unmatched),
        "possibleNewProject": len(possible_new),
        "topMatches": [
            {
                "title": m.get("matchedTitle", ""),
                "confidence": m.get("confidence", 0),
                "emailSubject": (m.get("emailSubject") or "")[:60],
            }
            for m in sorted(
                matches + possible_matches + possible_new,
                key=lambda x: x.get("confidence", 0),
                reverse=True,
            )[:5]
        ],
    }

    summary_path = index_data_dir / "matching_run_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    logger.info(
        "Matching complete: %d matched, %d possible, %d unmatched, %d new-project",
        len(matches), len(possible_new), len(unmatched), len(possible_new),
    )

    return summary


def load_matches(matching_root: Path) -> list[dict[str, Any]]:
    """Load email_card_matches.jsonl."""
    path = matching_root / "data" / "email_card_matches.jsonl"
    if not path.exists():
        return []
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def load_unmatched(matching_root: Path) -> list[dict[str, Any]]:
    """Load unmatched_emails.jsonl."""
    path = matching_root / "data" / "unmatched_emails.jsonl"
    if not path.exists():
        return []
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def load_new_project_emails(matching_root: Path) -> list[dict[str, Any]]:
    """Load possible_new_project_emails.jsonl."""
    path = matching_root / "data" / "possible_new_project_emails.jsonl"
    if not path.exists():
        return []
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def load_matching_summary(matching_root: Path) -> dict[str, Any] | None:
    """Load matching_run_summary.json."""
    path = matching_root / "data" / "matching_run_summary.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
