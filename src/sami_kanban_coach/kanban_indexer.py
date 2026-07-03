"""Read-only Kanban state indexer for Phase 1.

Builds normalised card index, activity index, snapshot, and source status
from raw Kanban JSON data. Never writes to Kanban source paths.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .kanban_models import (
    ActivityIndex,
    CardIndex,
    KanbanSourceStatus,
    KanbanStateSnapshot,
    SourceStatus,
    TeamSourceStatus,
)
from .kanban_reader import (
    build_source_status,
    check_team_accessibility,
    file_hash,
    file_mtime_iso,
    find_projects_json,
    read_card_updates_jsonl,
    read_projects_json,
)
from .logging_setup import setup_logging
from .path_safety import assert_not_forbidden

logger = setup_logging(Path("runtime/email_recall/logs"))


def _safe_str(val: Any) -> str:
    """Safely convert a value to string, handling None."""
    if val is None:
        return ""
    if not isinstance(val, str):
        return str(val)
    return val


def _normalise_project(project: dict[str, Any]) -> dict[str, Any]:
    """Normalise a raw project dict into a CardIndex-compatible dict.

    Handles field aliases (next/nextAction, blocked/status, projectLead/lead, etc.).
    """
    p = {}

    # ID
    p["projectId"] = _safe_str(project.get("id", ""))

    # Title
    p["title"] = _safe_str(project.get("title", ""))

    # Status — normalise to lowercase
    status = _safe_str(project.get("status", "")).lower()
    if not status:
        # Check if "blocked" is a separate field
        if project.get("blocked"):
            status = "blocked"
    p["status"] = status

    # Column mapping
    col_map = {
        "running": "In Progress",
        "blocked": "Blocked",
        "ready": "Ready",
        "done": "Done",
    }
    p["column"] = col_map.get(status, status.capitalize() if status else "")

    # Risk
    risk = _safe_str(project.get("riskColour", "")).lower()
    if not risk:
        risk = _safe_str(project.get("risk", "")).lower()
    p["risk"] = risk

    # Lead / owner
    p["lead"] = _safe_str(project.get("projectLead", "") or project.get("lead", ""))
    p["owner"] = _safe_str(project.get("owner", ""))

    # Dates
    p["reviewDate"] = _safe_str(project.get("reviewDate", ""))
    p["lastUpdated"] = _safe_str(project.get("lastUpdated", ""))

    # Context / current state
    context = _safe_str(project.get("context", "") or project.get("currentState", ""))
    p["currentState"] = context

    # Next action — handle both "next" and "nextAction"
    p["nextAction"] = _safe_str(project.get("nextAction", "") or project.get("next", ""))

    # Notes
    p["notes"] = _safe_str(project.get("notes", ""))

    # Tags
    tags = project.get("tags", [])
    if isinstance(tags, list):
        p["tags"] = [str(t) for t in tags if t]
    else:
        p["tags"] = []

    # People as keywords
    people = project.get("people", [])
    if isinstance(people, list):
        p["keywords"] = sorted(set(
            str(h).strip().lower() for h in people if h
        ))
    else:
        p["keywords"] = []

    return p


def _compute_summary_for_ai(card: dict[str, Any]) -> str:
    """Build a short neutral summary string from card fields."""
    parts = []
    if card.get("title"):
        parts.append(f"Title: {card['title']}")
    if card.get("status"):
        parts.append(f"Status: {card['status']}")
    if card.get("risk"):
        parts.append(f"Risk: {card['risk']}")
    if card.get("nextAction"):
        na = card["nextAction"]
        parts.append(f"Next: {na[:120]}{'...' if len(na) > 120 else ''}")
    if card.get("currentState"):
        cs = card["currentState"]
        parts.append(f"Context: {cs[:200]}{'...' if len(cs) > 200 else ''}")
    return " | ".join(parts)


def _count_activity(project_id: str, activities: list[dict[str, Any]]) -> tuple[int, str]:
    """Count activity entries and find latest timestamp for a project."""
    count = 0
    latest = ""
    for act in activities:
        if act.get("projectId") == project_id or act.get("cardId") == project_id:
            count += 1
            ts = _safe_str(act.get("timestamp", "") or act.get("capturedAt", ""))
            if ts and (not latest or ts > latest):
                latest = ts
    return count, latest


# ---------------------------------------------------------------------------
# Main index function
# ---------------------------------------------------------------------------

def index_kanban_source(
    settings: Any,
    source_mode: str = "local",
) -> KanbanStateSnapshot:
    """Read a Kanban source and build all index files under kanban_index_root.

    Args:
        settings: Settings object with kanban_* config fields.
        source_mode: 'local' or 'team'.

    Returns:
        KanbanStateSnapshot with captured data.

    This function never writes to Kanban source paths.
    """
    if source_mode == "local":
        source_root = settings.kanban_local_path()
    else:
        source_root = settings.kanban_team_path()

    index_root = settings.kanban_index_path()
    index_data_dir = index_root / "data"
    index_data_dir.mkdir(parents=True, exist_ok=True)
    assert_not_forbidden(index_data_dir)  # should never match Kanban paths
    logger.info("Index root: %s", index_root)

    # Read projects
    projects_json_path, proj_note = find_projects_json(source_root)
    logger.info("Projects source: %s", proj_note)

    if projects_json_path is None:
        raise FileNotFoundError(f"No projects.json found at {source_root}")

    projects, meta, projects_hash, parse_note = read_projects_json(projects_json_path)
    logger.info("Projects parse: %s", parse_note)

    card_updates_path = source_root / "data" / "card_updates.jsonl"
    activities, updates_hash, updates_note = read_card_updates_jsonl(card_updates_path)
    logger.info("Card updates: %s", updates_note)

    # Build card index
    card_index_records: list[CardIndex] = []
    for proj in projects:
        normalised = _normalise_project(proj)
        act_count, act_latest = _count_activity(normalised["projectId"], activities)

        card = CardIndex(
            projectId=normalised["projectId"],
            title=normalised["title"],
            status=normalised["status"],
            risk=normalised["risk"],
            lead=normalised["lead"],
            owner=normalised["owner"],
            reviewDate=normalised["reviewDate"],
            lastUpdated=normalised["lastUpdated"],
            currentState=normalised["currentState"],
            nextAction=normalised["nextAction"],
            notes=normalised["notes"],
            column=normalised["column"],
            tags=normalised["tags"],
            keywords=normalised["keywords"],
            activityCount=act_count,
            latestActivityAt=act_latest,
            sourceHash=projects_hash,
            summaryForAi=_compute_summary_for_ai(normalised),
        )
        card_index_records.append(card)

    # Build activity index
    activity_records: list[ActivityIndex] = []
    for act in activities:
        project_id = _safe_str(act.get("cardId", "") or act.get("projectId", ""))
        activity_records.append(ActivityIndex(
            projectId=project_id,
            timestamp=_safe_str(act.get("timestamp", "") or act.get("capturedAt", "")),
            actor=_safe_str(act.get("updatedBy", "") or act.get("actor", "")),
            action=_safe_str(act.get("action", "")),
            summary=_safe_str(act.get("note", "") or act.get("summary", "")),
        ))

    # Write card_index.jsonl
    card_index_path = index_data_dir / "card_index.jsonl"
    with open(card_index_path, "w", encoding="utf-8") as f:
        for card in card_index_records:
            f.write(card.model_dump_json(exclude_none=True) + "\n")
    logger.info("Card index written: %d records to %s", len(card_index_records), card_index_path)

    # Write card_activity_index.jsonl
    activity_path = index_data_dir / "card_activity_index.jsonl"
    with open(activity_path, "w", encoding="utf-8") as f:
        for act in activity_records:
            f.write(act.model_dump_json(exclude_none=True) + "\n")
    logger.info("Activity index written: %d records to %s", len(activity_records), activity_path)

    # Build counts
    counts_by_status: dict[str, int] = {}
    counts_by_risk: dict[str, int] = {}
    for card in card_index_records:
        st = card.status or "unknown"
        counts_by_status[st] = counts_by_status.get(st, 0) + 1
        rk = card.risk or "unknown"
        counts_by_risk[rk] = counts_by_risk.get(rk, 0) + 1

    # Build snapshot
    snapshot = KanbanStateSnapshot(
        sourceMode=source_mode,
        sourceRoot=str(source_root),
        projectsJsonPath=str(projects_json_path),
        cardUpdatesPath=str(card_updates_path),
        projectsJsonHash=projects_hash,
        cardUpdatesHash=updates_hash,
        cardCount=len(card_index_records),
        countsByStatus=counts_by_status,
        countsByRisk=counts_by_risk,
        cards=[card.model_dump(exclude_none=True) for card in card_index_records],
    )

    snapshot_path = index_data_dir / "kanban_state_snapshot.json"
    with open(snapshot_path, "w", encoding="utf-8") as f:
        json.dump(snapshot.model_dump(exclude_none=True), f, indent=2, ensure_ascii=False)
    logger.info("Snapshot written to %s", snapshot_path)

    # Build source status
    local_st = build_source_status(
        settings.kanban_local_path(),
        find_projects_json(settings.kanban_local_path())[0],
        settings.kanban_local_path() / "data" / "card_updates.jsonl",
    )

    team_accessible, team_status = check_team_accessibility(settings.kanban_team_path())
    team_st = TeamSourceStatus(
        root=str(settings.kanban_team_path()),
        accessible=team_accessible,
        status=team_status,
        requiredForValidation=settings.require_team_source_for_validation,
    )
    if team_accessible:
        team_path = settings.kanban_team_path()
        team_pj = find_projects_json(team_path)[0]
        team_cu = team_path / "data" / "card_updates.jsonl"
        team_st.projectsJsonExists = team_pj.exists() if team_pj else False
        if team_st.projectsJsonExists:
            team_st.projectsJsonHash = file_hash(team_pj)
            team_st.projectsJsonMtime = file_mtime_iso(team_pj)
        team_st.cardUpdatesExists = team_cu.exists()
        if team_st.cardUpdatesExists:
            team_st.cardUpdatesHash = file_hash(team_cu)
            team_st.cardUpdatesMtime = file_mtime_iso(team_cu)

    source_status = KanbanSourceStatus(
        local=local_st,
        team=team_st,
        selectedSource=source_mode,
    )

    status_path = index_data_dir / "kanban_source_status.json"
    with open(status_path, "w", encoding="utf-8") as f:
        json.dump(source_status.model_dump(exclude_none=True), f, indent=2, ensure_ascii=False)
    logger.info("Source status written to %s", status_path)

    return snapshot


def load_card_index(index_root: Path) -> list[CardIndex]:
    """Load card_index.jsonl from the index root."""
    path = index_root / "data" / "card_index.jsonl"
    if not path.exists():
        return []
    cards = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cards.append(CardIndex(**json.loads(line)))
    return cards


def load_snapshot(index_root: Path) -> KanbanStateSnapshot | None:
    """Load kanban_state_snapshot.json from the index root."""
    path = index_root / "data" / "kanban_state_snapshot.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return KanbanStateSnapshot(**json.load(f))


def load_source_status(index_root: Path) -> KanbanSourceStatus | None:
    """Load kanban_source_status.json from the index root."""
    path = index_root / "data" / "kanban_source_status.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return KanbanSourceStatus(**json.load(f))
