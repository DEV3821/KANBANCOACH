"""Storage layer for SAMI Kanban Coach Phase 0.

Handles:
- Atomic writes for processed_ids.json and email.json
- Append-only JSONL writes
- Evidence folder creation and file saving
- Deduplication via messageKey AND contentFingerprint (dual dedup)
- Collision-avoiding folder/filename generation
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .logging_setup import setup_logging
from .models import CapturedEmail, DedupeKeys, ProcessedIDs
from .path_safety import (
    assert_not_forbidden,
    safe_path_for_email,
)

logger = setup_logging(Path("runtime/email_recall/logs"))


# ---------------------------------------------------------------------------
# Atomic file helpers
# ---------------------------------------------------------------------------
def _atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON to path atomically using a temp file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    assert_not_forbidden(path)
    fd, tmp_path = tempfile.mkstemp(
        suffix=".tmp",
        prefix=path.stem + "_",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            json.dump(data, tmp, ensure_ascii=False, indent=2)
            tmp.flush()
            os.fsync(fd)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _atomic_write_text(path: Path, text: str) -> None:
    """Write text to path atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    assert_not_forbidden(path)
    fd, tmp_path = tempfile.mkstemp(
        suffix=".tmp",
        prefix=path.stem + "_",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", errors="replace") as tmp:
            tmp.write(text)
            tmp.flush()
            os.fsync(fd)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Deduplication (processed_ids.json) — dual-key: messageKey + contentFingerprint
# ---------------------------------------------------------------------------
def load_processed_ids(data_dir: Path) -> ProcessedIDs:
    """Load processed message keys and content fingerprints from disk.

    Backward compatible: old schema (list or dict with only ``processed``)
    loads without error and initialises ``fingerprints`` as empty.
    """
    path = data_dir / "processed_ids.json"
    if not path.exists():
        return ProcessedIDs()
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, list):
            # Old schema: bare list of keys
            return ProcessedIDs(processed=raw, fingerprints=[])
        if isinstance(raw, dict):
            # New schema (processed + fingerprints) or old dict schema
            processed = raw.get("processed", [])
            fingerprints = raw.get("fingerprints", [])
            if not isinstance(processed, list):
                processed = []
            if not isinstance(fingerprints, list):
                fingerprints = []
            return ProcessedIDs(processed=processed, fingerprints=fingerprints)
        return ProcessedIDs()
    except (json.JSONDecodeError, OSError, ValueError):
        logger.warning("Corrupted processed_ids.json, starting fresh.")
        return ProcessedIDs()


def save_processed_ids(data_dir: Path, processed: ProcessedIDs) -> None:
    """Persist processed IDs + fingerprints atomically."""
    path = data_dir / "processed_ids.json"
    _atomic_write_json(path, {
        "processed": processed.processed,
        "fingerprints": processed.fingerprints,
    })


def is_duplicate(
    data_dir: Path,
    processed: ProcessedIDs,
    message_key: str,
    content_fingerprint: str,
) -> tuple[bool, str]:
    """Check if an email is already processed by messageKey OR contentFingerprint.

    Returns:
        (is_dup: bool, reason: str) where reason is
        'messageKey', 'contentFingerprint', or ''.
    """
    if processed.has(message_key):
        return True, "messageKey"
    if content_fingerprint and processed.has_fingerprint(content_fingerprint):
        return True, "contentFingerprint"
    return False, ""


def mark_processed(
    data_dir: Path,
    processed: ProcessedIDs,
    message_key: str,
    content_fingerprint: str,
) -> None:
    """Add messageKey and contentFingerprint, then persist."""
    processed.add(message_key)
    if content_fingerprint:
        processed.add_fingerprint(content_fingerprint)
    save_processed_ids(data_dir, processed)


# ---------------------------------------------------------------------------
# JSONL append
# ---------------------------------------------------------------------------
def append_jsonl(jsonl_path: Path, record: CapturedEmail) -> None:
    """Append a JSONL record atomically-safe (append + flush)."""
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    assert_not_forbidden(jsonl_path)
    try:
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(record.model_dump_json(exclude_none=True) + "\n")
            f.flush()
    except OSError as e:
        logger.error("Failed to append JSONL: %s", e)
        raise


# ---------------------------------------------------------------------------
# Evidence folder / file helpers
# ---------------------------------------------------------------------------
def _safe_path_counter(path: Path) -> Path:
    """If path exists, append (N) before extension to avoid overwrite."""
    if not path.exists():
        return path
    stem = path.stem
    ext = path.suffix
    parent = path.parent
    counter = 1
    while True:
        new_path = parent / f"{stem}_{counter}{ext}"
        if not new_path.exists():
            return new_path
        counter += 1


def _safe_evidence_folder(
    output_root: Path,
    received_date: str,
    subject: str,
    is_duplicate: bool,
) -> Path:
    """Build and possibly deduplicate an evidence folder path.

    If the folder already exists:
      - For a duplicate email: return the existing path (do NOT rewrite).
      - For a genuine new email: append a counter suffix to avoid collision.

    Returns the folder path (already created if genuinely new).
    """
    base_dir = safe_path_for_email(output_root, received_date, subject)

    if not base_dir.exists():
        # Fresh folder — create and return
        base_dir.mkdir(parents=True, exist_ok=True)
        return base_dir

    if is_duplicate:
        # Duplicate — return existing folder, caller will skip write
        return base_dir

    # Genuine collision (different email, same subject+hash) — append counter
    parent = base_dir.parent
    stem = base_dir.name
    counter = 1
    while True:
        alt_dir = parent / f"{stem}_{counter}"
        if not alt_dir.exists():
            alt_dir.mkdir(parents=True, exist_ok=True)
            return alt_dir
        counter += 1


def save_email_evidence(
    output_root: Path,
    record: CapturedEmail,
    body_text: str | None,
    body_html: str | None,
    headers_text: str | None,
    msg_bytes: bytes | None,
    save_msg_copy: bool,
    save_body_html_flag: bool,
    save_headers_flag: bool,
    is_duplicate_email: bool = False,
) -> str:
    """Save all evidence files for a captured email.

    Args:
        output_root: Root of the email_recall output.
        record: CapturedEmail record (mutated in-place with paths).
        body_text: Plain text body content.
        body_html: HTML body content.
        headers_text: Transport headers as text.
        msg_bytes: Raw .msg bytes from Outlook SaveAs.
        save_msg_copy: Whether to save .msg file.
        save_body_html_flag: Whether to save HTML body.
        save_headers_flag: Whether to save headers.
        is_duplicate_email: If True and evidence folder exists, do NOT rewrite.

    Returns:
        Absolute path to the evidence folder as a string.
    """
    received_date = record.capturedAt[:10]  # YYYY-MM-DD
    subject = record.subject or "no_subject"

    evidence_dir = _safe_evidence_folder(
        output_root, received_date, subject, is_duplicate_email
    )
    assert_not_forbidden(evidence_dir)

    # If this is a duplicate and folder already exists, skip writing
    if is_duplicate_email:
        record.evidenceFolder = str(evidence_dir.relative_to(output_root))
        logger.debug("Skipping evidence write for duplicate at %s", evidence_dir)
        return str(evidence_dir)

    # email.json — full metadata
    meta_path = evidence_dir / "email.json"
    _atomic_write_json(meta_path, record.model_dump(exclude_none=True))

    # body.txt
    if body_text:
        txt_path = _safe_path_counter(evidence_dir / "body.txt")
        _atomic_write_text(txt_path, body_text)
        record.bodyTextPath = str(txt_path.relative_to(output_root))

    # body.html
    if body_html and save_body_html_flag:
        html_path = _safe_path_counter(evidence_dir / "body.html")
        _atomic_write_text(html_path, body_html)
        record.bodyHtmlPath = str(html_path.relative_to(output_root))

    # headers.txt
    if headers_text and save_headers_flag:
        hdr_path = _safe_path_counter(evidence_dir / "headers.txt")
        _atomic_write_text(hdr_path, headers_text)
        record.headersPath = str(hdr_path.relative_to(output_root))

    # original.msg
    if msg_bytes and save_msg_copy:
        msg_path = _safe_path_counter(evidence_dir / "original.msg")
        msg_path.write_bytes(msg_bytes)
        record.msgPath = str(msg_path.relative_to(output_root))

    record.evidenceFolder = str(evidence_dir.relative_to(output_root))
    return str(evidence_dir)


# ---------------------------------------------------------------------------
# Full capture pipeline
# ---------------------------------------------------------------------------
def capture_and_save(
    output_root: Path,
    mode: str,
    source_folder: str,
    email_data: dict[str, Any],
) -> CapturedEmail:
    """Run the full capture pipeline: dedupe check, evidence save, JSONL append.

    Deduplicates on messageKey OR contentFingerprint (dual key).
    Supports cross-move/copy dedup where EntryID changes.

    Args:
        output_root: Root of the email_recall output.
        mode: Capture mode (live/poll/selected/folder).
        source_folder: Outlook folder name the email came from.
        email_data: Dictionary of email fields from the Outlook COM extractor.

    Returns:
        The CapturedEmail record if captured.

    Raises:
        ValueError if the email is a duplicate (caller should handle).
    """
    message_key = email_data.get("messageKey")
    if not message_key:
        raise ValueError("No messageKey in email_data — cannot dedupe.")

    content_fingerprint = email_data.get("contentFingerprint", "")
    data_dir = output_root / "data"
    processed = load_processed_ids(data_dir)

    dup, dup_reason = is_duplicate(data_dir, processed, message_key, content_fingerprint)
    if dup:
        logger.info(
            "Already captured (by %s): %s — %s",
            dup_reason,
            message_key,
            email_data.get("subject", ""),
        )
        raise ValueError(f"Duplicate ({dup_reason}): {message_key}")

    # Build the record
    record = CapturedEmail(
        captureMode=mode,
        messageKey=message_key,
        contentFingerprint=content_fingerprint,
        internetMessageId=email_data.get("internetMessageId"),
        entryId=email_data.get("entryId"),
        conversationId=email_data.get("conversationId"),
        subject=email_data.get("subject", ""),
        senderName=email_data.get("senderName", ""),
        senderEmail=email_data.get("senderEmail", ""),
        to=email_data.get("to", []),
        cc=email_data.get("cc", []),
        receivedAt=email_data.get("receivedAt"),
        sentOn=email_data.get("sentOn"),
        sourceFolder=source_folder,
        bodyPreview=(email_data.get("bodyText") or "")[:500],
        bodyCharCount=len(email_data.get("bodyText") or ""),
        processed=False,
    )

    # Build dedupeKeys
    record.dedupeKeys = DedupeKeys(
        messageKey=message_key,
        internetMessageId=email_data.get("internetMessageId"),
        entryId=email_data.get("entryId"),
        contentFingerprint=content_fingerprint,
    )

    # Save evidence files
    evidence_path = save_email_evidence(
        output_root=output_root,
        record=record,
        body_text=email_data.get("bodyText"),
        body_html=email_data.get("bodyHtml"),
        headers_text=email_data.get("headers"),
        msg_bytes=email_data.get("msgBytes"),
        save_msg_copy=email_data.get("save_msg_copy", True),
        save_body_html_flag=email_data.get("save_body_html", True),
        save_headers_flag=email_data.get("save_headers", True),
        is_duplicate_email=False,
    )
    logger.info("Evidence saved: %s", evidence_path)

    # Append JSONL
    append_jsonl(output_root / "data" / "raw_email_recall.jsonl", record)

    # Mark processed with both keys
    mark_processed(data_dir, processed, message_key, content_fingerprint)

    return record
