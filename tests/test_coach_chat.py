from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sami_kanban_coach.coach_chat import (
    apply_local_draft,
    ensure_sandbox,
    find_card,
    guard_summary,
    sandbox_root,
    undo_last_local_apply,
)
from sami_kanban_coach.ollama_client import _extract_json_object


def _settings(tmp_path: Path) -> SimpleNamespace:
    local = tmp_path / "local_source"
    data = local / "data"
    data.mkdir(parents=True)
    projects = {
        "projects": [
            {
                "id": "card-001-nt-ultrarad-stroke-vpn-firewall-rules",
                "title": "NT UltraRad VPN/firewall",
                "status": "running",
                "riskColour": "amber",
                "projectLead": "Brian",
                "context": "Awaiting VPN/firewall evidence.",
                "nextAction": "Check latest email evidence.",
            }
        ]
    }
    (data / "projects.json").write_text(json.dumps(projects), encoding="utf-8")
    (data / "card_updates.jsonl").write_text("", encoding="utf-8")
    team = tmp_path / "team_source"
    (team / "data").mkdir(parents=True)
    (team / "data" / "projects.json").write_text(json.dumps(projects), encoding="utf-8")
    return SimpleNamespace(
        kanban_local_path=lambda: local,
        kanban_team_root=str(team),
        local_kanban_sandbox_root=str(tmp_path / "sandbox"),
        allow_kanban_apply=False,
        local_kanban_apply_enabled=False,
        team_kanban_apply_enabled=False,
        allow_local_kanban_apply=True,
        kanban_apply_target="local_sandbox",
        team_esmi_write_enabled=False,
        mailbox_write_enabled=False,
        mailbox_search_enabled=False,
        mailbox_search_read_only=True,
        mailbox_search_recent_days=180,
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="qwen3:8b",
        ollama_timeout_seconds=10,
    )


def test_ensure_sandbox_copies_projects_and_updates(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    info = ensure_sandbox(settings, refresh=True)

    root = sandbox_root(settings)
    assert Path(info["projectsJson"]).exists()
    assert (root / "data" / "card_updates.jsonl").exists()
    assert (root / "backups").exists()
    assert (root / "logs").exists()
    assert info["projectsJsonHash"]


def test_apply_local_draft_writes_only_sandbox_and_undo_restores(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    ensure_sandbox(settings, refresh=True)
    team_path = Path(settings.kanban_team_root) / "data" / "projects.json"
    team_before = team_path.read_text(encoding="utf-8")
    draft = {
        "card": "NT UltraRad VPN/firewall",
        "recommendedStatus": "running",
        "recommendedRisk": "green",
        "currentState": "Firewall spreadsheet evidence found in prior evidence run.",
        "nextAction": "Confirm REQ2026637 and SRV-3890870 closure evidence.",
        "lead": "Brian",
        "confidence": 0.9,
        "evidenceSummary": ["SRV-3890870", "REQ2026637"],
        "sourceRefs": ["ep_test"],
        "applyTarget": "local_sandbox",
    }

    result = apply_local_draft(settings, draft)

    sandbox_text = (sandbox_root(settings) / "data" / "projects.json").read_text(encoding="utf-8")
    assert "Firewall spreadsheet evidence found" in sandbox_text
    assert team_path.read_text(encoding="utf-8") == team_before
    assert result["teamEsmiUntouched"] is True
    assert Path(result["backupPath"]).exists()
    assert "context" in result["changedFields"]

    undo = undo_last_local_apply(settings)

    restored_text = (sandbox_root(settings) / "data" / "projects.json").read_text(encoding="utf-8")
    assert "Awaiting VPN/firewall evidence." in restored_text
    assert "Firewall spreadsheet evidence found" not in restored_text
    assert undo["teamEsmiUntouched"] is True
    assert team_path.read_text(encoding="utf-8") == team_before


def test_live_guards_block_local_harness_if_weakened(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.allow_kanban_apply = True
    draft = {"card": "NT UltraRad VPN/firewall", "applyTarget": "local_sandbox"}

    with pytest.raises(PermissionError):
        apply_local_draft(settings, draft)


def test_find_card_prefers_ultrarad_vpn_firewall(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    ensure_sandbox(settings, refresh=True)

    card = find_card(settings, "What is the latest on NT UltraRad VPN firewall?")

    assert card is not None
    assert card["title"] == "NT UltraRad VPN/firewall"
    guards = guard_summary(settings)
    assert guards["allow_kanban_apply"] is False
    assert guards["allow_local_kanban_apply"] is True
    assert guards["mailbox_write_enabled"] is False


def test_ollama_json_parser_handles_qwen_think_and_trailing_comma() -> None:
    raw = '<think>{not json}</think>\nPreamble {"answer":"ok","draft":{"applyTarget":"local_sandbox",}} tail'

    parsed = _extract_json_object(raw)

    assert parsed == {"answer": "ok", "draft": {"applyTarget": "local_sandbox"}}
