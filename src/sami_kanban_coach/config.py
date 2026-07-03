"""Configuration loader for SAMI Kanban Coach."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator


class Settings(BaseModel):
    """Application settings loaded from config/settings.json."""

    # Phase 0 — Outlook email recall
    outlook_folder_path: str = Field(
        default="Kanban Intake",
        description="Outlook folder path to watch.",
    )
    output_root: str = Field(
        default="C:\\Tools\\SAMI Kanban Coach\\runtime\\email_recall",
        description="Root directory for email recall output.",
    )
    save_msg_copy: bool = Field(default=True)
    save_body_html: bool = Field(default=True)
    save_headers: bool = Field(default=True)
    save_attachments: bool = Field(default=False)
    max_body_chars: int = Field(default=80000, ge=1000, le=10_000_000)
    default_since_hours: int = Field(default=48, ge=1, le=8760)
    default_max_items: int = Field(default=100, ge=1, le=10000)
    poll_seconds: int = Field(default=60, ge=5, le=3600)

    # Phase 1 — Kanban state indexer
    kanban_local_root: str = Field(
        default="C:\\Tools\\SAMI-Kanban-WorkServer",
        description="Primary local Kanban repo root.",
    )
    kanban_team_root: str = Field(
        default="\\\\fusafmcf01\\Medical Imaging\\Team_ESMI\\Program Delivery\\SAMI-Kanban-WorkServer",
        description="Optional Team ESMI network Kanban repo root.",
    )
    kanban_index_root: str = Field(
        default="C:\\Tools\\SAMI Kanban Coach\\runtime\\kanban_index",
        description="Root directory for kanban index output.",
    )
    prefer_team_source_if_available: bool = Field(
        default=False,
        description="If True and Team ESMI is accessible, use Team source over local.",
    )
    require_team_source_for_validation: bool = Field(
        default=False,
        description="If True, validation fails when Team ESMI is inaccessible.",
    )

    # Phase 3 — Qwen/Ollama draft generation
    ollama_base_url: str = Field(
        default="http://127.0.0.1:11434",
        description="Ollama API base URL.",
    )
    ollama_model: str = Field(
        default="qwen3:8b",
        description="Ollama model name for card-state comparison.",
    )
    draft_min_match_confidence: float = Field(
        default=0.60,
        description="Minimum match confidence to consider for draughting.",
        ge=0.0,
        le=1.0,
    )
    draft_high_confidence_threshold: float = Field(
        default=0.85,
        description="Confidence above which decisions are treated as high-confidence.",
        ge=0.0,
        le=1.0,
    )
    max_email_body_chars_for_draft: int = Field(
        default=12000,
        description="Max email body characters to include in an Ollama prompt.",
        ge=500,
        le=100000,
    )
    max_evidence_emails_per_card: int = Field(
        default=5,
        description="Max evidence emails to bundle per card for one prompt.",
        ge=1,
        le=20,
    )
    enable_ollama_drafts: bool = Field(
        default=True,
        description="If True, attempt Ollama API calls for draft generation.",
    )
    allow_draft_without_ollama: bool = Field(
        default=True,
        description="If True and Ollama is unavailable, create needs_review drafts from match data only.",
    )

    # Phase 4A — Draft review queue
    review_root: str = Field(
        default="C:\\Tools\\SAMI Kanban Coach\\runtime\\review",
        description="Root directory for review queue output.",
    )
    enable_textual_tui: bool = Field(
        default=True,
        description="If True, use Textual for review-tui (falls back to Rich prompts otherwise).",
    )
    review_requires_explicit_approval: bool = Field(
        default=True,
        description="If True, explicit approval required before marking a draft ready.",
    )
    allow_kanban_apply: bool = Field(
        default=False,
        description="SAFETY: Must remain false in Phase 4A. Enables writing approved edits to Kanban repo.",
    )

    # Phase 4B — Local Kanban apply engine
    kanban_apply_root: str = Field(
        default="C:\\Tools\\SAMI Kanban Coach\\runtime\\apply",
        description="Root directory for apply plan and results.",
    )
    local_kanban_apply_enabled: bool = Field(
        default=False,
        description="If True, allows writing approved edits to local Kanban repo.",
    )
    team_kanban_apply_enabled: bool = Field(
        default=False,
        description="SAFETY: Must remain false in Phase 4B. Team ESMI apply is out of scope.",
    )
    apply_requires_explicit_flag: bool = Field(
        default=True,
        description="If True, --write-local --confirm-local-kanban-write both required for real write.",
    )
    ignore_smoke_test_drafts: bool = Field(
        default=True,
        description="If True, skip drafts with generatedBy=phase4a_smoke_test in apply plans.",
    )
    backup_before_apply: bool = Field(
        default=True,
        description="If True, create timestamped backup of Kanban source files before writing.",
    )

    # Phase 5 — Local Qwen AI Adviser
    ollama_enabled: bool = Field(
        default=True,
        description="If True, enable the local Qwen adviser in review-apply-tui.",
    )
    ollama_timeout_seconds: int = Field(
        default=60,
        description="Timeout for Ollama API calls from the adviser.",
        ge=10,
        le=300,
    )
    local_ai_email_context_enabled: bool = Field(
        default=True,
        description="If True, load email evidence context for Qwen adviser.",
    )
    local_ai_max_email_context_chars: int = Field(
        default=12000,
        description="Max email body characters to include in adviser prompts.",
        ge=500,
        le=100000,
    )
    local_ai_max_email_count: int = Field(
        default=8,
        description="Max evidence emails to include per adviser query.",
        ge=1,
        le=20,
    )
    team_esmi_context_poll_enabled: bool = Field(
        default=True,
        description="If True, poll Team ESMI for latest context before adviser queries.",
    )
    team_esmi_context_poll_seconds: int = Field(
        default=30,
        description="Poll interval for Team ESMI context refresh.",
        ge=5,
        le=3600,
    )
    team_esmi_context_read_only: bool = Field(
        default=True,
        description="Safety: Team ESMI polling must be read-only.",
    )
    local_ai_update_log_enabled: bool = Field(
        default=True,
        description="If True, log accepted adviser suggestions to local_ai_update_log.jsonl.",
    )

    # Phase 5b — Mailbox search (pre-export)
    mailbox_search_enabled: bool = Field(
        default=False,
        description="If True, search mailbox directly for email evidence before Qwen adviser.",
    )
    mailbox_search_provider: str = Field(
        default="outlook_com",
        description="Mailbox provider: outlook_com, graph, imap, or disabled.",
    )
    mailbox_search_read_only: bool = Field(
        default=True,
        description="Safety: mailbox search must be read-only.",
    )
    mailbox_search_max_results: int = Field(
        default=10,
        description="Max emails to return from mailbox search.",
        ge=1,
        le=50,
    )
    mailbox_search_recent_days: int = Field(
        default=180,
        description="Only search emails within this many days.",
        ge=1,
        le=730,
    )
    mailbox_search_timeout_seconds: int = Field(
        default=30,
        description="Timeout for mailbox search operations.",
        ge=5,
        le=120,
    )
    mailbox_search_cache_enabled: bool = Field(
        default=True,
        description="If True, cache mailbox search results locally.",
    )
    mailbox_search_cache_path: str = Field(
        default="runtime/apply/data/mailbox_search_cache.jsonl",
        description="Path for mailbox search results cache.",
    )
    mailbox_search_snapshot_path: str = Field(
        default="runtime/apply/data/email_evidence_snapshots.jsonl",
        description="Path for email evidence snapshots from mailbox search.",
    )

    # Phase 6A — Mr Kanban local conversational harness
    allow_local_kanban_apply: bool = Field(
        default=True,
        description="Phase 6A only: allow writes to the isolated local sandbox copy.",
    )
    kanban_apply_target: str = Field(
        default="local_sandbox",
        description="Phase 6A apply target. Must remain local_sandbox.",
    )
    local_kanban_sandbox_root: str = Field(
        default="C:\\Tools\\SAMI Kanban Coach\\runtime\\local_kanban_sandbox",
        description="Isolated local sandbox for Mr Kanban coach-chat writes.",
    )
    team_esmi_write_enabled: bool = Field(
        default=False,
        description="Safety: Team ESMI writes remain disabled in Phase 6A.",
    )
    mailbox_write_enabled: bool = Field(
        default=False,
        description="Safety: mailbox writes are impossible in Phase 6A.",
    )

    @field_validator("output_root", "kanban_index_root")
    @classmethod
    def resolve_output_roots(cls, v: str) -> str:
        """Resolve and ensure absolute paths."""
        return str(Path(v).resolve())

    def output_path(self) -> Path:
        return Path(self.output_root).resolve()

    def data_dir(self) -> Path:
        return self.output_path() / "data"

    def evidence_dir(self) -> Path:
        return self.output_path() / "evidence" / "emails"

    def attachments_dir(self) -> Path:
        return self.output_path() / "evidence" / "attachments"

    def logs_dir(self) -> Path:
        return self.output_path() / "logs"

    def jsonl_path(self) -> Path:
        return self.data_dir() / "raw_email_recall.jsonl"

    def processed_ids_path(self) -> Path:
        return self.data_dir() / "processed_ids.json"

    # --- Phase 1 helpers ---

    def kanban_local_path(self) -> Path:
        return Path(self.kanban_local_root).resolve()

    def kanban_team_path(self) -> Path:
        return Path(self.kanban_team_root)

    def kanban_index_path(self) -> Path:
        return Path(self.kanban_index_root).resolve()

    def kanban_index_data_dir(self) -> Path:
        return self.kanban_index_path() / "data"

    def kanban_index_logs_dir(self) -> Path:
        return self.kanban_index_path() / "logs"

    # --- Phase 3 helpers ---

    def drafts_path(self) -> Path:
        return Path(self.output_path().parent / "drafts").resolve()

    def drafts_data_dir(self) -> Path:
        return self.drafts_path() / "data"

    def drafts_logs_dir(self) -> Path:
        return self.drafts_path() / "logs"

    # --- Phase 4A helpers ---

    def review_path(self) -> Path:
        return Path(self.review_root).resolve()

    def review_data_dir(self) -> Path:
        return self.review_path() / "data"

    def review_logs_dir(self) -> Path:
        return self.review_path() / "logs"

    # --- Phase 4B helpers ---

    def apply_path(self) -> Path:
        return Path(self.kanban_apply_root).resolve()

    def apply_data_dir(self) -> Path:
        return self.apply_path() / "data"

    def apply_backups_dir(self) -> Path:
        return self.apply_path() / "backups"

    def apply_logs_dir(self) -> Path:
        return self.apply_path() / "logs"


class ConfigLoader:
    """Loads and validates application settings."""

    DEFAULT_CONFIG_PATH = Path("config/settings.json")

    def __init__(self, config_path: str | Path | None = None) -> None:
        self.config_path = Path(config_path) if config_path else self._default_config_path()

    @staticmethod
    def _default_config_path() -> Path:
        cwd = Path.cwd().resolve()
        for parent in [cwd, *cwd.parents]:
            candidate = parent / "config" / "settings.json"
            if candidate.exists():
                return candidate
            if (parent / "pyproject.toml").exists():
                candidate = parent / "config" / "settings.json"
                if candidate.exists():
                    return candidate
        return Path("config/settings.json")

    def load(self) -> Settings:
        path = self.config_path
        if not path.exists():
            raise FileNotFoundError(
                f"Config file not found: {path}\n"
                f"Create from config/settings.example.json."
            )
        with open(path, "r", encoding="utf-8-sig") as f:
            raw: dict[str, Any] = json.load(f)
        return Settings(**raw)

    def load_raw(self) -> dict[str, Any]:
        path = self.config_path
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        with open(path, "r", encoding="utf-8-sig") as f:
            return dict(json.load(f))
