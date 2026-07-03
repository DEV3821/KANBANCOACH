"""Path safety utilities for SAMI Kanban Coach Phase 0.

Handles sanitisation, length limits, collision avoidance,
and forbidden-path guardrails.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Forbidden paths — explicit guardrails
# ---------------------------------------------------------------------------
_FORBIDDEN_PATHS: list[Path] = [
    Path("C:/Tools/SAMI-Kanban-WorkServer").resolve(),
]

# Team ESMI UNC path — resolved lazily to avoid network errors at import time.
_UNC_FORBIDDEN_STR = "//fusafmcf01/Medical Imaging/Team_ESMI/Program Delivery/SAMI-Kanban-WorkServer"

# Unicode and control-character cleanup
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}

_MAX_DIR_COMPONENT = 120
_MAX_FILENAME = 200
_HASH_SUFFIX_LEN = 8


def sanitise_filename(name: str, max_len: int = _MAX_FILENAME) -> str:
    """Remove/replace invalid Windows filename characters and truncate."""
    cleaned = _INVALID_FILENAME_CHARS.sub("_", name).strip()
    if not cleaned:
        cleaned = "_unnamed"
    # Strip trailing dots/spaces (Windows issue)
    cleaned = cleaned.rstrip(". ")
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip(". ")
    # Reserved name check
    stem = Path(cleaned).stem.upper()
    if stem in _RESERVED_NAMES:
        cleaned = "_" + cleaned
    return cleaned


def sanitise_dir_component(name: str) -> str:
    """Sanitise a single directory component."""
    return sanitise_filename(name, max_len=_MAX_DIR_COMPONENT)


def subject_hash(subject: str, length: int = _HASH_SUFFIX_LEN) -> str:
    """SHA256 hex digest of subject for safe directory naming."""
    return hashlib.sha256(subject.encode("utf-8", errors="replace")).hexdigest()[:length]


def safe_subject_dirname(subject: str) -> str:
    """Create a safe directory name from an email subject.

    Combines sanitised subject prefix + short hash to avoid collisions.
    """
    sanitised = sanitise_dir_component(subject)
    short_hash = subject_hash(subject)
    # Trim sanitised part to leave room for underscore + hash
    max_prefix = _MAX_DIR_COMPONENT - len(short_hash) - 1
    if len(sanitised) > max_prefix:
        sanitised = sanitised[:max_prefix].rstrip("_").rstrip(".")
    return f"{sanitised}_{short_hash}"


def safe_path_for_email(output_root: Path, received_date: str, subject: str) -> Path:
    """Build the safe evidence folder path for an email.

    Args:
        output_root: Root of the email_recall output directory.
        received_date: YYYY-MM-DD date string from the email.
        subject: Email subject line.

    Returns:
        Path like output_root/evidence/emails/YYYY-MM-DD/<safe-dir>/
    """
    date_part = sanitise_dir_component(received_date)
    subject_part = safe_subject_dirname(subject)
    return output_root / "evidence" / "emails" / date_part / subject_part


def is_forbidden_path(target: str | Path) -> bool:
    """Check if a path is within the explicitly forbidden Kanban/ESMI zones.

    Returns True if the target is under any forbidden root.
    The UNC path is checked as a string prefix to avoid network resolve errors.
    """
    target_str = str(target).replace("/", "\\")

    # String-based UNC check (no network resolve needed)
    unc_prefix = _UNC_FORBIDDEN_STR.replace("/", "\\").rstrip("\\")
    if target_str.replace("\\", "").startswith(unc_prefix.replace("\\", "")):
        return True

    # Path-based check for local paths
    try:
        target_resolved = Path(target).resolve()
    except (OSError, ValueError):
        return False
    for forbidden in _FORBIDDEN_PATHS:
        try:
            if target_resolved == forbidden or forbidden in target_resolved.parents:
                return True
        except (OSError, ValueError):
            continue
    return False


def assert_not_forbidden(target: str | Path) -> None:
    """Raise PermissionError if target is in a forbidden zone."""
    if is_forbidden_path(target):
        raise PermissionError(
            f"Write denied: {target} is within a forbidden Kanban/ESMI path.\n"
            f"Forbidden roots: {[str(p) for p in _FORBIDDEN_PATHS]} + UNC"
        )


def check_writeable(path: Path) -> tuple[bool, str]:
    """Check if a directory is writeable.

    Returns (is_writeable: bool, message: str).
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
        test_file = path / ".write_test_tmp"
        test_file.write_text("test")
        test_file.unlink()
        return True, "Writable"
    except PermissionError as e:
        return False, f"Permission denied: {e}"
    except OSError as e:
        return False, f"OS error: {e}"
