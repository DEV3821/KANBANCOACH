"""Outlook COM wrapper for SAMI Kanban Coach Phase 0.

Provides safe access to classic Outlook via win32com with:
- COM dispatch and detection
- Folder resolution (simple name and nested path)
- Selected item reading
- Folder item enumeration
- Live watcher with ItemAdd event + polling fallback
- Safe field extraction with fallbacks
- SaveAs for .msg via temporary files
"""

from __future__ import annotations

import datetime
import io
import os
import struct
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from .logging_setup import setup_logging

logger = setup_logging(Path("runtime/email_recall/logs"))

# Type alias for Outlook COM objects
OutlookApp = Any
OutlookNamespace = Any
OutlookFolder = Any
OutlookItems = Any
OutlookItem = Any

# Known MailItem class constant
OL_MAIL_ITEM_CLASS = 43

# SaveAs format constants
OL_MSG_UNICODE = 3  # olMsgUnicode
OL_MSG_TXT = 0
OL_MSG_HTML = 5
OL_MSG_MIME = 4
OL_MSG_DOC = 8  # olDoc


# ---------------------------------------------------------------------------
# Safe field extraction helpers
# ---------------------------------------------------------------------------
def _safe_get(obj: Any, attr: str, default: Any = None) -> Any:
    """Safely get an attribute from a COM object."""
    try:
        val = getattr(obj, attr, default)
        return val if val is not None else default
    except AttributeError:
        return default
    except Exception:
        return default


def _safe_get_property(prop_accessor: Any, prop_tag: str, default: Any = None) -> Any:
    """Safely get a MAPI property via PropertyAccessor."""
    if prop_accessor is None:
        return default
    try:
        return prop_accessor.GetProperty(prop_tag)
    except Exception:
        return default


def _safe_datetime(dt: Any) -> str | None:
    """Convert COM date/DateTime to ISO string, or None."""
    if dt is None:
        return None
    try:
        if hasattr(dt, "Format"):
            return str(dt)
        if isinstance(dt, datetime.datetime):
            return dt.isoformat()
        if isinstance(dt, float):
            # COM DATE format: days since 1899-12-30
            try:
                return datetime.datetime(1899, 12, 30) + datetime.timedelta(days=dt)
            except (OverflowError, ValueError):
                return None
        return str(dt)
    except Exception:
        return None


def _get_header_lines(prop_accessor: Any) -> str | None:
    """Try to retrieve transport message headers via PR_TRANSPORT_MESSAGE_HEADERS."""
    return _safe_get_property(prop_accessor, "http://schemas.microsoft.com/mapi/proptag/0x007D001E")


# ---------------------------------------------------------------------------
# Outlook COM Connection
# ---------------------------------------------------------------------------
class OutlookConnection:
    """Manages the classic Outlook COM connection."""

    def __init__(self) -> None:
        self._application: OutlookApp | None = None
        self._namespace: OutlookNamespace | None = None
        self._connected = False

    @property
    def application(self) -> OutlookApp:
        if self._application is None:
            raise RuntimeError("Not connected to Outlook. Call connect() first.")
        return self._application

    @property
    def namespace(self) -> OutlookNamespace:
        if self._namespace is None:
            raise RuntimeError("Not connected to Outlook. Call connect() first.")
        return self._namespace

    def connect(self) -> None:
        """Dispatch Outlook.Application and open MAPI namespace."""
        import win32com.client

        self._application = win32com.client.Dispatch("Outlook.Application")
        self._namespace = self._application.GetNamespace("MAPI")
        # Outlook is already running — MAPI session is inherited.
        # No explicit Logon() needed; Logon() blocks if Outlook is open.
        self._connected = True
        logger.info("Outlook COM connected: %s v%s", self.application.Name, self.application.Version)

    def disconnect(self) -> None:
        """Log off MAPI namespace."""
        if self._namespace and self._connected:
            try:
                self._namespace.Logoff()
            except Exception:
                pass
        self._connected = False
        self._application = None
        self._namespace = None
        logger.info("Outlook COM disconnected.")

    def is_connected(self) -> bool:
        return self._connected

    def get_application_info(self) -> dict[str, str]:
        """Return basic application metadata."""
        info = {
            "name": _safe_get(self._application, "Name", "Unknown"),
            "version": _safe_get(self._application, "Version", "Unknown"),
        }
        try:
            info["productCode"] = str(self._application.ProductCode)
        except Exception:
            info["productCode"] = "N/A"
        try:
            info["profileName"] = str(self._namespace.CurrentProfileName)
        except Exception:
            info["profileName"] = "N/A"
        return info

    def get_stores(self) -> list[dict[str, Any]]:
        """Return list of top-level store names."""
        stores = []
        try:
            for store in self._namespace.Stores:
                try:
                    stores.append({
                        "name": str(store.DisplayName or ""),
                        "exchange": bool(getattr(store, "IsExchange", False)),
                    })
                except Exception:
                    continue
        except Exception as e:
            logger.warning("Could not enumerate stores: %s", e)
        return stores

    def resolve_folder(self, folder_path: str) -> OutlookFolder | None:
        """Resolve a folder by path.

        Supports:
          - Simple name: "Kanban Intake"
          - Nested path: "Mailbox - Brian Shaw\\Kanban Intake"
        """
        if "\\" in folder_path or "/" in folder_path:
            return self._resolve_nested(folder_path)
        return self._resolve_simple(folder_path)

    def _sorted_stores(self) -> list:
        """Return stores sorted by priority (primary mailbox first, PACS/archive last).

        Skips stores with priority >= 3 (e.g. Health:PACS stores)
        which are slow to enumerate and never contain user-created folders.
        """
        stores = list(self._namespace.Stores)

        def _priority(s):
            try:
                dn = str(s.DisplayName or "")
                if dn.startswith("Health:") or "PACS" in dn:
                    return 3
                if "Archive" in dn and "Online" not in dn:
                    return 2
                if hasattr(s, "ExchangeStoreType") and s.ExchangeStoreType == 0:
                    return 0
                return 1
            except Exception:
                return 2

        stores.sort(key=_priority)
        return [s for s in stores if _priority(s) < 3]


    def _resolve_simple(self, name: str) -> OutlookFolder | None:
        """Search all stores for a folder by simple name.

        Skips non-mailbox stores (e.g. Health:PACS, Online Archive)
        to avoid slow root-folder enumeration on large Exchange stores.
        Searches the primary Exchange mailbox first, falls back to others.
        """
        try:
            for store in self._sorted_stores():
                try:
                    root = store.GetRootFolder()
                    for folder in root.Folders:
                        if str(folder.Name or "").strip() == name.strip():
                            logger.info("Folder found: %s in store %s", name, store.DisplayName)
                            return folder
                except Exception:
                    continue
        except Exception as e:
            logger.warning("Folder search error: %s", e)
        return None

    def _resolve_nested(self, path: str) -> OutlookFolder | None:
        """Resolve a backslash-separated nested folder path."""
        parts = path.replace("/", "\\").split("\\")
        if not parts:
            return None

        # Top-level: search sorted stores for the first component
        try:
            for store in self._sorted_stores():
                try:
                    root = store.GetRootFolder()
                    for folder in root.Folders:
                        if str(folder.Name or "").strip() == parts[0].strip():
                            return self._descend(folder, parts[1:])
                except Exception:
                    continue
        except Exception as e:
            logger.warning("Nested folder search error: %s", e)
        return None

    def _descend(self, folder: OutlookFolder, parts: list[str]) -> OutlookFolder | None:
        """Descend into subfolders following the path parts."""
        current = folder
        for part in parts:
            found = None
            try:
                for sub in current.Folders:
                    if str(sub.Name or "").strip() == part.strip():
                        found = sub
                        break
            except Exception:
                return None
            if found is None:
                return None
            current = found
        return current

    def get_child_folders(self, folder: OutlookFolder) -> list[dict[str, Any]]:
        """Return direct child folder names (1 level only)."""
        children = []
        try:
            for sub in folder.Folders:
                try:
                    children.append({
                        "name": str(sub.Name or ""),
                        "itemCount": int(getattr(sub, "Items", None) and sub.Items.Count or 0),
                    })
                except Exception:
                    continue
        except Exception:
            pass
        return children

    def get_all_top_folders(self) -> list[dict[str, Any]]:
        """Return top-level folder names from all stores."""
        top_folders = []
        try:
            for store in self._namespace.Stores:
                try:
                    root = store.GetRootFolder()
                    for folder in root.Folders:
                        try:
                            top_folders.append({
                                "store": str(store.DisplayName or "?"),
                                "folder": str(folder.Name or ""),
                                "itemCount": int(getattr(folder, "Items", None) and folder.Items.Count or 0),
                            })
                        except Exception:
                            top_folders.append({
                                "store": str(store.DisplayName or "?"),
                                "folder": str(folder.Name or "?"),
                                "itemCount": 0,
                            })
                except Exception:
                    continue
        except Exception as e:
            logger.warning("Could not enumerate top folders: %s", e)
        return top_folders


# ---------------------------------------------------------------------------
# Email field extraction
# ---------------------------------------------------------------------------
def compute_content_fingerprint(
    subject: str,
    sender_email: str,
    sender_name: str,
    received_at: str | None,
    sent_on: str | None,
    body_text: str | None,
    attachments: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """Compute a stable content fingerprint SHA256 for deduplication across moves/copies.

    Normalises inputs so the same email yields the same fingerprint
    even if EntryID/InternetMessageID change after a folder move.

    Returns (fingerprint_hex, normalized_input_string).
    """
    import hashlib

    # Normalize subject: strip, lowercase, collapse whitespace
    norm_subject = " ".join(subject.strip().lower().split()) if subject else ""

    # Normalize sender: prefer email, fall back to name
    norm_sender = (sender_email or sender_name or "").strip().lower()

    # Normalize received timestamp: round to minute
    norm_received = ""
    if received_at:
        try:
            # Keep only date + hour:minute to absorb second-level drift
            norm_received = received_at[:16] if len(received_at) >= 16 else received_at
        except Exception:
            norm_received = received_at or ""

    # Normalize sent timestamp
    norm_sent = ""
    if sent_on:
        try:
            norm_sent = sent_on[:16] if len(sent_on) >= 16 else sent_on
        except Exception:
            norm_sent = sent_on or ""

    # Body preview hash (first 500 chars)
    body_preview = (body_text or "")[:500]
    body_hash = hashlib.sha256(body_preview.encode("utf-8", errors="replace")).hexdigest()[:16]

    # Attachment signature
    att_parts = []
    if attachments:
        for a in attachments:
            name = (a.get("name") or "").strip().lower()
            size = a.get("size", 0)
            att_parts.append(f"{name}:{size}")
    att_sig = "|".join(sorted(att_parts))

    # Build normalized input
    parts = [
        norm_subject,
        norm_sender,
        norm_received,
        norm_sent,
        body_hash,
        att_sig,
    ]
    normalized = "|||".join(parts)

    fingerprint = hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()
    return fingerprint, normalized


def extract_email_fields(
    mail_item: OutlookItem,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract all relevant fields from an Outlook MailItem.

    Returns a dictionary suitable for CapturedEmail construction.
    """
    if config is None:
        config = {}

    # MessageKey: InternetMessageID > EntryID > SHA256 fallback
    internet_id = _safe_get(mail_item, "InternetMessageID")
    entry_id = _safe_get(mail_item, "EntryID")
    subject = _safe_get(mail_item, "Subject", "") or ""
    sender_name = _safe_get(mail_item, "SenderName", "") or ""
    sender_email = _safe_get(mail_item, "SenderEmailAddress", "") or ""

    # Build message key
    if internet_id:
        message_key = str(internet_id).strip()
    elif entry_id:
        message_key = str(entry_id).strip()
    else:
        import hashlib
        received = str(_safe_get(mail_item, "ReceivedTime", ""))
        raw = f"{subject}|{sender_name}|{sender_email}|{received}"
        message_key = hashlib.sha256(raw.encode()).hexdigest()

    # Recipients
    to_recipients = _safe_extract_recipients(mail_item, "To")
    cc_recipients = _safe_extract_recipients(mail_item, "CC")

    # Dates
    received_time = _safe_datetime(_safe_get(mail_item, "ReceivedTime"))
    sent_on = _safe_datetime(_safe_get(mail_item, "SentOn"))

    # Body
    body_text = _safe_get(mail_item, "Body") or ""
    max_chars = config.get("max_body_chars", 80000)
    body_text = body_text[:max_chars]

    body_html = _safe_get(mail_item, "HTMLBody") or None
    if body_html:
        body_html = body_html[:max_chars]

    # Conversation
    conversation_id = _safe_get(mail_item, "ConversationID")
    conversation_topic = _safe_get(mail_item, "ConversationTopic")

    # Headers via PropertyAccessor
    headers = None
    try:
        prop_accessor = _safe_get(mail_item, "PropertyAccessor")
        if prop_accessor:
            headers = _get_header_lines(prop_accessor)
    except Exception:
        pass

    # Attachment metadata (always capture metadata, even if not saving files)
    attachments = []
    try:
        for i, att in enumerate(mail_item.Attachments, start=1):
            try:
                att_name = str(att.DisplayName or att.FileName or f"attachment_{i}")
                att_ext = Path(att_name).suffix.lower() or ".bin"
                att_size = int(getattr(att, "Size", 0) or 0)
                attachments.append({
                    "index": i,
                    "name": att_name,
                    "extension": att_ext,
                    "size": att_size,
                })
            except Exception:
                attachments.append({
                    "index": i,
                    "name": f"attachment_{i}",
                    "extension": ".bin",
                    "size": 0,
                })
    except Exception:
        pass

    # SaveAs .msg to bytes
    msg_bytes = _save_msg_to_bytes(mail_item) if config.get("save_msg_copy", True) else None

    # Content fingerprint for cross-move deduplication
    fingerprint, fp_inputs = compute_content_fingerprint(
        subject=subject,
        sender_email=sender_email,
        sender_name=sender_name,
        received_at=received_time,
        sent_on=sent_on,
        body_text=body_text,
        attachments=attachments,
    )

    return {
        "messageKey": message_key,
        "contentFingerprint": fingerprint,
        "fingerprintInputs": fp_inputs,
        "internetMessageId": internet_id,
        "entryId": entry_id,
        "conversationId": conversation_id,
        "conversationTopic": conversation_topic,
        "subject": subject,
        "senderName": sender_name,
        "senderEmail": sender_email,
        "to": to_recipients,
        "cc": cc_recipients,
        "receivedAt": received_time,
        "sentOn": sent_on,
        "bodyText": body_text,
        "bodyHtml": body_html,
        "headers": headers,
        "msgBytes": msg_bytes,
        "attachments": attachments,
        "save_msg_copy": config.get("save_msg_copy", True),
        "save_body_html": config.get("save_body_html", True),
        "save_headers": config.get("save_headers", True),
    }


def _safe_extract_recipients(mail_item: OutlookItem, field: str) -> list[str]:
    """Extract recipient email addresses from a recipients field."""
    try:
        recipient_str = _safe_get(mail_item, field, "")
        if not recipient_str:
            return []
        # Semi-colon separated
        parts = str(recipient_str).split(";")
        return [p.strip() for p in parts if p.strip()]
    except Exception:
        return []


def _save_msg_to_bytes(mail_item: OutlookItem) -> bytes | None:
    """Save mail item as Unicode MSG to a bytes buffer.

    Uses Outlook's SaveAs method with olMsgUnicode format,
    reads the temp file back into memory.
    """
    tmp_dir = None
    try:
        tmp_dir = tempfile.mkdtemp(prefix="olm_")
        tmp_path = os.path.join(tmp_dir, "temp.msg")
        mail_item.SaveAs(tmp_path, OL_MSG_UNICODE)
        with open(tmp_path, "rb") as f:
            return f.read()
    except Exception as e:
        logger.debug("Could not save .msg: %s", e)
        return None
    finally:
        if tmp_dir:
            try:
                import shutil
                shutil.rmtree(tmp_dir)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Selected items
# ---------------------------------------------------------------------------
def get_selected_items(connection: OutlookConnection) -> list[OutlookItem]:
    """Get currently selected MailItems from the active explorer."""
    try:
        explorer = connection.application.ActiveExplorer()
        selection = explorer.Selection
        if not selection or selection.Count == 0:
            logger.info("No items selected in Outlook.")
            return []
        items = []
        for i in range(1, selection.Count + 1):
            item = selection.Item(i)
            item_class = _safe_get(item, "Class")
            if item_class == OL_MAIL_ITEM_CLASS:
                items.append(item)
            else:
                logger.info("Skipping non-mail item (class=%s): %s", item_class, _safe_get(item, "Subject", "?"))
        logger.info("Selected: %d mail items out of %d selected", len(items), selection.Count)
        return items
    except Exception as e:
        logger.error("Failed to get selection: %s", e)
        return []


# ---------------------------------------------------------------------------
# Folder items
# ---------------------------------------------------------------------------
def get_folder_items(
    folder: OutlookFolder,
    since_hours: int = 48,
    max_items: int = 100,
) -> list[OutlookItem]:
    """Get mail items from a folder, newest first, filtered by received time.

    Uses Items.Restrict where possible for server-side filtering.
    """
    try:
        items = folder.Items
        if not items or items.Count == 0:
            return []

        # Sort by ReceivedTime descending
        try:
            items.Sort("[ReceivedTime]", True)
        except Exception:
            pass

        # Build filter string
        since_dt = datetime.datetime.now() - datetime.timedelta(hours=since_hours)
        since_str = since_dt.strftime("%m/%d/%Y %H:%M %p")
        filter_str = f"[ReceivedTime] >= '{since_str}'"

        try:
            filtered = items.Restrict(filter_str)
        except Exception:
            logger.warning("Restrict failed, falling back to manual filtering.")
            filtered = items

        collected = []
        count = 0
        for item in filtered:
            if count >= max_items:
                break
            try:
                if _safe_get(item, "Class") == OL_MAIL_ITEM_CLASS:
                    collected.append(item)
                    count += 1
            except Exception:
                continue

        logger.info(
            "Folder items: %d mail items (filtered since %dh, max %d)",
            len(collected), since_hours, max_items,
        )
        return collected
    except Exception as e:
        logger.error("Failed to enumerate folder items: %s", e)
        return []


# ---------------------------------------------------------------------------
# Live watcher
# ---------------------------------------------------------------------------
class LiveWatcher:
    """Watches an Outlook folder for new mail using ItemAdd event + polling fallback."""

    def __init__(
        self,
        outlook_connection: OutlookConnection,
        folder: OutlookFolder,
        on_email: Callable[[OutlookItem, str], None],
        poll_seconds: int = 60,
    ) -> None:
        self.connection = outlook_connection
        self.folder = folder
        self.on_email = on_email
        self.poll_seconds = poll_seconds
        self._running = False
        self._event_sink = None

    def start(self) -> None:
        """Start watching. Blocks until Ctrl+C."""
        self._running = True
        items = self.folder.Items

        # Set up ItemAdd event if possible
        # NOTE: pywin32's WithEvents instantiates the handler class with ZERO
        # constructor arguments.  The handler class MUST NOT have a custom __init__.
        # Callback is set as an attribute on the sink instance *after* WithEvents returns.
        try:
            from win32com.client import WithEvents

            class ItemAddHandler:
                """Outlook Items.ItemAdd event sink.

                No custom __init__ — pywin32 instantiates this with no arguments.
                The callback is assigned to the instance by WithEvents caller.
                """
                callback = None

                def OnItemAdd(self, item: OutlookItem) -> None:
                    try:
                        if _safe_get(item, "Class") == OL_MAIL_ITEM_CLASS:
                            cb = self.callback
                            if cb:
                                cb(item, "live")
                        else:
                            logger.debug("Live event skipped non-mail (class=%s)", _safe_get(item, "Class"))
                    except Exception as e:
                        logger.error("ItemAdd handler error: %s", e)

            self._event_sink = WithEvents(items, ItemAddHandler)
            self._event_sink.callback = self.on_email
            logger.info("ItemAdd event handler registered.")
        except Exception as e:
            logger.warning("Could not register ItemAdd event: %s — using polling only.", e)

        # Polling loop with event co-existence
        logger.info(
            "Live watcher started — watching '%s' (poll every %ds). Press Ctrl+C to stop.",
            self.folder.Name,
            self.poll_seconds,
        )

        try:
            while self._running:
                time.sleep(self.poll_seconds)
                # Fallback poll — check recent items
                self._poll()
        except KeyboardInterrupt:
            logger.info("Live watcher stopped by user.")
        finally:
            self._running = False
            self._cleanup()

    def stop(self) -> None:
        """Signal the watcher to stop."""
        self._running = False

    def _poll(self) -> None:
        """Polling fallback: check folder for recent items."""
        try:
            items = get_folder_items(self.folder, since_hours=1, max_items=20)
            for item in items:
                self.on_email(item, "poll")
        except Exception as e:
            logger.error("Polling error: %s", e)

    def _cleanup(self) -> None:
        """Clean up event sink reference."""
        self._event_sink = None
        logger.info("Live watcher cleaned up.")
