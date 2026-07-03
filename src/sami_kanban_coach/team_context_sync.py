"""Team ESMI context sync for local Qwen adviser (Phase 5, Part A).

Read-only polling of Team ESMI Kanban source for the latest board
context. Never writes to Team ESMI paths. Caches results locally.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .kanban_reader import file_hash, file_mtime_iso, find_projects_json, read_projects_json

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TEAM_CACHE_FILE = "team_context_cache.json"
_TEAM_POLL_LOG = "team_context_poll_log.jsonl"

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


class TeamContextStatus:
    """Status of a Team ESMI context poll."""

    def __init__(
        self,
        reachable: bool = False,
        hash_value: str = "",
        mtime: str = "",
        project_count: int = 0,
        source_path: str = "",
        cached_hash: str = "",
        cached_mtime: str = "",
        changed_since_last_poll: bool = False,
        error: str = "",
    ) -> None:
        self.reachable = reachable
        self.hash_value = hash_value
        self.mtime = mtime
        self.project_count = project_count
        self.source_path = source_path
        self.cached_hash = cached_hash
        self.cached_mtime = cached_mtime
        self.changed_since_last_poll = changed_since_last_poll
        self.error = error

    def to_dict(self) -> dict[str, Any]:
        return {
            "reachable": self.reachable,
            "hashValue": self.hash_value,
            "mtime": self.mtime,
            "projectCount": self.project_count,
            "sourcePath": self.source_path,
            "cachedHash": self.cached_hash,
            "cachedMtime": self.cached_mtime,
            "changedSinceLastPoll": self.changed_since_last_poll,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _cache_dir(settings: Any) -> Path:
    """Get the local cache directory for Team ESMI context."""
    return settings.apply_path() / "data"


def _cache_file(settings: Any) -> Path:
    return _cache_dir(settings) / _TEAM_CACHE_FILE


def _poll_log_file(settings: Any) -> Path:
    return _cache_dir(settings) / _TEAM_POLL_LOG


def _load_cached_context(settings: Any) -> dict[str, Any]:
    """Load the cached Team ESMI context, or empty dict."""
    path = _cache_file(settings)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return dict(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_cached_context(settings: Any, data: dict[str, Any]) -> None:
    """Save Team ESMI context to local cache."""
    path = _cache_file(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _append_poll_log(settings: Any, entry: dict[str, Any]) -> None:
    """Append a poll log entry."""
    path = _poll_log_file(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    if "timestamp" not in entry:
        entry["timestamp"] = datetime.now().isoformat()
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _get_team_root(settings: Any) -> Path:
    """Get Team ESMI kanban path, handling UNC."""
    raw = getattr(settings, "kanban_team_root", None) or getattr(settings, "kanban_team_root", "")
    return Path(raw)


# ---------------------------------------------------------------------------
# Core polling
# ---------------------------------------------------------------------------


def poll_team_context(settings: Any) -> TeamContextStatus:
    """Poll Team ESMI Kanban source read-only.

    Reads projects.json from the configured team path, computes hash/mtime,
    and compares to the local cache. Writes cache only if source is newer.
    Never writes to Team ESMI paths.

    Returns TeamContextStatus with reachable/info/error.
    """
    team_root = _get_team_root(settings)
    cached = _load_cached_context(settings)

    # Check reachability without crashing on unavailable UNC paths
    try:
        team_reachable = team_root.exists()
    except OSError:
        team_reachable = False

    if not team_reachable:
        status = TeamContextStatus(
            reachable=False,
            error=f"Team ESMI path not accessible: {team_root}",
            cached_hash=cached.get("hashValue", ""),
            cached_mtime=cached.get("mtime", ""),
        )
        _append_poll_log(settings, {
            "event": "team_esmi_poll",
            "reachable": False,
            "error": status.error,
        })
        return status

    # Find projects.json
    pj_path, note = find_projects_json(team_root)
    if not pj_path or not pj_path.exists():
        status = TeamContextStatus(
            reachable=False,
            source_path=str(team_root),
            error=f"No projects.json found: {note}",
            cached_hash=cached.get("hashValue", ""),
            cached_mtime=cached.get("mtime", ""),
        )
        _append_poll_log(settings, {
            "event": "team_esmi_poll",
            "reachable": False,
            "error": status.error,
        })
        return status

    # Read current hash and mtime
    live_hash = file_hash(pj_path)
    live_mtime = file_mtime_iso(pj_path)

    # Read card count
    projects, _, _, _ = read_projects_json(pj_path)
    project_count = len(projects) if projects else 0

    # Compare to cache
    cached_hash = cached.get("hashValue", "")
    cached_mtime = cached.get("mtime", "")
    changed = bool(cached_hash and live_hash != cached_hash)

    status = TeamContextStatus(
        reachable=True,
        hash_value=live_hash,
        mtime=live_mtime,
        project_count=project_count,
        source_path=str(pj_path),
        cached_hash=cached_hash,
        cached_mtime=cached_mtime,
        changed_since_last_poll=changed,
    )

    # Update cache if source is newer or cache is empty
    if not cached_hash or changed:
        cache_data = {
            "hashValue": live_hash,
            "mtime": live_mtime,
            "projectCount": project_count,
            "sourcePath": str(pj_path),
            "lastPolled": datetime.now().isoformat(),
            "teamRoot": str(team_root),
        }
        _save_cached_context(settings, cache_data)

    _append_poll_log(settings, {
        "event": "team_esmi_poll",
        "reachable": True,
        "hashValue": live_hash[:16],
        "mtime": live_mtime,
        "changed": changed,
        "projectCount": project_count,
    })

    return status


def load_latest_card_context(
    settings: Any,
    project_id: str | None = None,
    project_title: str | None = None,
) -> dict[str, Any] | None:
    """Load the latest card context from Team ESMI (from cache or fresh poll).

    Tries cached context first, then polls if needed.
    Returns a dict with card fields or None.
    """
    team_root = _get_team_root(settings)

    try:
        team_reachable = team_root.exists()
    except OSError:
        team_reachable = False

    if not team_reachable:
        return None

    pj_path, _ = find_projects_json(team_root)
    if not pj_path or not pj_path.exists():
        return None

    projects, _, _, _ = read_projects_json(pj_path)
    if not projects:
        return None

    for p in projects:
        p_id = p.get("id", "") or p.get("projectId", "")
        p_title = p.get("title", "")
        if project_id and p_id == project_id:
            return p
        if project_title and p_title and project_title.lower() in p_title.lower():
            return p

    # Fallback: return first card if no match
    return projects[0] if projects else None


def get_team_context_status(settings: Any) -> dict[str, Any]:
    """Get Team ESMI context status without polling (from cache).

    Returns dict with reachable, hash, mtime, etc.
    """
    cached = _load_cached_context(settings)
    team_root = _get_team_root(settings)

    result: dict[str, Any] = {
        "teamEsmiConfigured": bool(str(team_root)),
        "teamEsmiPath": str(team_root),
        "contextCachePath": str(_cache_file(settings)),
        "pollLogPath": str(_poll_log_file(settings)),
    }

    # Check reachability without crashing on unavailable UNC paths
    try:
        team_reachable = team_root.exists() if team_root else False
    except OSError:
        team_reachable = False
    result["reachable"] = team_reachable

    if cached:
        result["cachedHash"] = cached.get("hashValue", "")
        result["cachedMtime"] = cached.get("mtime", "")
        result["cachedProjectCount"] = cached.get("projectCount", 0)
        result["lastPolled"] = cached.get("lastPolled", "")

    return result


# ---------------------------------------------------------------------------
# Compatibility alias
# ---------------------------------------------------------------------------

get_team_context_status_simple = get_team_context_status
