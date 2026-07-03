"""Read-only Kanban source reader for Phase 1.

Reads projects.json and card_updates.jsonl from a local or team Kanban repo.
Never writes to Kanban source paths.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .kanban_models import SourceStatus


def file_hash(path: Path) -> str:
    """SHA256 hex digest of file contents."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def file_mtime_iso(path: Path) -> str:
    """File modification time as ISO string, or empty string."""
    try:
        mtime = os.path.getmtime(path)
        return datetime.fromtimestamp(mtime).isoformat()
    except OSError:
        return ""


def find_projects_json(root: Path) -> tuple[Path | None, str]:
    """Find a usable projects.json, trying live file first, then backups.

    Returns:
        (path_or_None, note) where note describes what was found.
    """
    live = root / "data" / "projects.json"
    if live.exists():
        return live, f"Live projects.json found at {live}"

    # Most recent backup
    backup_dir = root / "data"
    candidates = []
    try:
        for p in backup_dir.iterdir():
            name = p.name
            if name.startswith("projects.json.bak-") or name.startswith("projects.json.backup-"):
                candidates.append(p)
    except OSError:
        pass

    if candidates:
        candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return candidates[0], f"No live projects.json — using most recent backup: {candidates[0].name}"

    # Example file as last resort
    example = root / "data" / "projects.example.json"
    if example.exists():
        return example, f"No projects.json or backup found — using example: {example.name}"

    return None, "No projects.json found anywhere in kanban root."


def read_projects_json(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any], str, str]:
    """Read and parse a projects.json file.

    Returns:
        (projects_list, meta_dict, hash_hex, parse_note)
    """
    hash_hex = file_hash(path)
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return [], {}, hash_hex, f"JSON parse error: {e}"
    except OSError as e:
        return [], {}, hash_hex, f"Read error: {e}"

    if isinstance(data, dict):
        meta = data.get("meta", {})
        projects = data.get("projects", [])
        if not isinstance(projects, list):
            projects = []
    elif isinstance(data, list):
        # Bare list of projects
        meta = {}
        projects = data
    else:
        return [], {}, hash_hex, f"Unexpected JSON structure: {type(data).__name__}"

    note = f"Parsed OK — {len(projects)} project(s)"
    return projects, meta, hash_hex, note


def read_card_updates_jsonl(path: Path) -> tuple[list[dict[str, Any]], str, str]:
    """Read and parse a card_updates.jsonl file.

    Returns:
        (updates_list, hash_hex, parse_note)
    """
    hash_hex = file_hash(path)
    if not path.exists():
        return [], hash_hex, "File does not exist (OK — may be absent)"

    updates = []
    errors = 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    updates.append(json.loads(line))
                except json.JSONDecodeError:
                    errors += 1
    except OSError as e:
        return [], hash_hex, f"Read error: {e}"

    note_parts = [f"{len(updates)} record(s)"]
    if errors:
        note_parts.append(f"{errors} parse error(s)")
    return updates, hash_hex, "; ".join(note_parts)


def build_source_status(
    root: Path,
    projects_json_path: Path | None,
    card_updates_path: Path | None,
) -> SourceStatus:
    """Build a SourceStatus for a given Kanban root."""
    st = SourceStatus(root=str(root))
    st.accessible = root.exists()

    if projects_json_path:
        st.projectsJsonExists = projects_json_path.exists()
        if st.projectsJsonExists:
            st.projectsJsonHash = file_hash(projects_json_path)
            st.projectsJsonMtime = file_mtime_iso(projects_json_path)

    if card_updates_path:
        st.cardUpdatesExists = card_updates_path.exists()
        if st.cardUpdatesExists:
            st.cardUpdatesHash = file_hash(card_updates_path)
            st.cardUpdatesMtime = file_mtime_iso(card_updates_path)

    return st


def check_team_accessibility(team_root: Path) -> tuple[bool, str]:
    """Check if a Team ESMI UNC path is accessible without writing.

    Returns (accessible: bool, status_message: str).
    """
    try:
        accessible = team_root.exists()
        if accessible:
            return True, "Accessible"
        return False, "Path does not exist (off network or unavailable)"
    except OSError as e:
        return False, f"Not accessible: {e}"
    except PermissionError:
        return False, "Access denied (permissions or off network)"
