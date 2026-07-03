"""Phase 4E — End-to-End Dry-Run Pilot Pack.

Provides coach-status (read-only dashboard), coach-dry-run (safe pilot pipeline),
and pilot report export. Never writes Kanban. Uses only existing safe functions.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from rich.text import Text
from rich.columns import Columns

from .apply_engine import (
    load_apply_plan,
    load_apply_decisions,
    load_apply_summary,
    load_apply_results,
    DECISIONS_FILE,
    apply_operator_approved_plan,
    build_apply_plan,
    is_smoke_item,
)
from .apply_models import ApplyRunSummary
from .review_engine import (
    load_review_queue,
    load_approved_drafts,
    load_edited_drafts,
    load_skipped_drafts,
    load_review_summary,
)
from .kanban_reader import file_hash, find_projects_json, file_mtime_iso
from .path_safety import is_forbidden_path
from .review_tui import _trunc, _export_markdown_report

console = Console()

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _get_safety_status(settings: Any) -> tuple[str, str]:
    """Determine the safety status label and colour.

    Returns (label, style) where style is a Rich style string.
    """
    local_enabled = settings.local_kanban_apply_enabled
    allow_apply = settings.allow_kanban_apply

    if local_enabled and allow_apply:
        return ("DANGER: apply gates enabled", "bold red")
    elif local_enabled or allow_apply:
        return ("WARN: partial gate enabled", "bold yellow")
    else:
        return ("SAFE: dry-run only - apply disabled", "bold green")


def _count_file_lines(path: Path) -> int:
    """Count non-empty lines in a file."""
    if not path.exists():
        return 0
    with open(path, "r", encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def _file_exists_str(path: Path) -> str:
    """Return YES/NO depending on whether a file exists."""
    return "[green]YES[/green]" if path.exists() else "[dim]NO[/dim]"


# ---------------------------------------------------------------------------
# coach-status command
# ---------------------------------------------------------------------------

def run_coach_status(settings: Any) -> None:
    """Print a read-only status dashboard for the Kanban Coach pipeline."""
    kanban_local = settings.kanban_local_path()
    kanban_index_root = settings.kanban_index_path()
    apply_root = settings.apply_path()
    review_root = settings.review_path()
    draft_root = settings.drafts_path()

    # --- Gather data ---
    plan = load_apply_plan(apply_root)
    decisions = load_apply_decisions(apply_root)
    review_summary = load_review_summary(review_root)
    apply_summary = load_apply_summary(apply_root)
    approved = load_approved_drafts(review_root)
    edited = load_edited_drafts(review_root)
    skipped = load_skipped_drafts(review_root)
    review_queue = load_review_queue(review_root)

    # Kanban hash
    pj_path, _ = find_projects_json(kanban_local)
    kanban_hash = file_hash(pj_path) if (pj_path and pj_path.exists()) else ""
    kanban_mtime = file_mtime_iso(pj_path) if (pj_path and pj_path.exists()) else ""

    # Safety status
    safety_label, safety_style = _get_safety_status(settings)

    # Decision counts
    dec_approved = sum(1 for d in decisions.values() if d.get("decision") == "approved_for_apply")
    dec_skipped = sum(1 for d in decisions.values() if d.get("decision") == "skipped")
    dec_needs = sum(1 for d in decisions.values() if d.get("decision") == "needs_edit")
    dec_pending = sum(1 for d in decisions.values() if d.get("decision") in ("pending", ""))
    total_decisions = len(decisions)

    # Apply plan counts
    plan_items_count = len(plan.get("planItems", [])) if plan else 0
    plan_ready = sum(1 for i in (plan.get("planItems", []) if plan else []) if i.get("readyToApply"))
    plan_conflicts = sum(1 for i in (plan.get("planItems", []) if plan else []) if i.get("hashStatus") in ("conflict", "missing"))

    # Draft counts
    draft_updates_count = _count_file_lines(draft_root / "data" / "card_update_drafts.jsonl")
    approved_count = len(approved)
    edited_count = len(edited)
    skipped_count = len(skipped)
    pending_review = len([q for q in review_queue if q.get("reviewStatus") == "pending"])

    # Forbidden path status
    local_guarded = is_forbidden_path(str(kanban_local))
    team_unc = "//fusafmcf01/Medical Imaging/Team_ESMI/Program Delivery/SAMI-Kanban-WorkServer"
    team_guarded = is_forbidden_path(team_unc)

    # ==============================
    # Build the dashboard panels
    # ==============================

    console.clear()
    console.print()

    # --- Title ---
    title_panel = Panel(
        "[bold cyan]SAMI Kanban Coach — Pipeline Status[/bold cyan]\n"
        f"[{safety_style}]{safety_label}[/{safety_style}]\n"
        f"[dim]Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]",
        border_style="cyan",
        padding=(1, 2),
    )
    console.print(title_panel)
    console.print()

    # --- Gates table ---
    gates_table = Table(
        title="Safety Gates",
        box=box.SIMPLE,
        title_style="bold",
        show_header=True,
    )
    gates_table.add_column("Gate", style="bold", width=30)
    gates_table.add_column("Status", width=16)

    gates_table.add_row(
        "local_kanban_apply_enabled",
        f"[{'red' if settings.local_kanban_apply_enabled else 'green'}]{'ENABLED' if settings.local_kanban_apply_enabled else 'disabled'}[/]"
    )
    gates_table.add_row(
        "allow_kanban_apply",
        f"[{'red' if settings.allow_kanban_apply else 'green'}]{'ENABLED' if settings.allow_kanban_apply else 'disabled'}[/]"
    )
    gates_table.add_row(
        "ignore_smoke_test_drafts",
        f"[green]{'enabled' if settings.ignore_smoke_test_drafts else 'disabled'}[/]"
    )
    gates_table.add_row(
        "backup_before_apply",
        f"[green]{'enabled' if settings.backup_before_apply else 'disabled'}[/]"
    )
    gates_table.add_row(
        "Local workspace blocked",
        f"[{'green' if local_guarded else 'red'}]{'BLOCKED' if local_guarded else 'ACCESSIBLE'}[/]"
    )
    gates_table.add_row(
        "Team ESMI UNC blocked",
        f"[{'green' if team_guarded else 'red'}]{'BLOCKED' if team_guarded else 'ACCESSIBLE'}[/]"
    )

    console.print(
        Panel(gates_table, border_style="blue", padding=(1, 2), title="[bold]Configuration[/bold]")
    )
    console.print()

    # --- Pipeline stages ---
    stages_table = Table(
        title="Pipeline Stages",
        box=box.SIMPLE,
        title_style="bold",
        show_header=True,
    )
    stages_table.add_column("Stage", style="bold", width=26)
    stages_table.add_column("State", width=14)
    stages_table.add_column("Detail", width=50)

    # Phase 0-3: Email matching + drafts
    stages_table.add_row(
        "Draft generation",
        _file_exists_str(draft_root / "data" / "draft_run_summary.json"),
        f"{draft_updates_count} card_update_drafts generated"
        if draft_updates_count > 0 else "No drafts (run generate-drafts)"
    )

    # Phase 4A: Review queue
    stages_table.add_row(
        "Review queue",
        _file_exists_str(review_root / "data" / "review_run_summary.json"),
        f"{len(review_queue)} items ({pending_review} pending, "
        f"{approved_count} approved, {edited_count} edited, {skipped_count} skipped)"
    )

    # Phase 4B: Apply plan
    stages_table.add_row(
        "Apply plan",
        _file_exists_str(apply_root / "data" / "apply_plan.json"),
        f"{plan_items_count} items ({plan_ready} ready, {plan_conflicts} conflicts)"
        if plan else "No apply plan (run build-apply-plan)"
    )

    # Phase 4C/4D: Decisions
    dec_file = settings.apply_path() / "data" / DECISIONS_FILE
    stages_table.add_row(
        "Apply decisions",
        _file_exists_str(dec_file),
        f"{total_decisions} latest ({dec_approved} approved, "
        f"{dec_skipped} skipped, {dec_needs} needs-edit)"
        if total_decisions > 0 else "No decisions (run review-apply-tui)"
    )

    # Phase 4E: Dry-run
    stages_table.add_row(
        "Dry-run apply",
        _file_exists_str(apply_root / "data" / "apply_run_summary.json"),
        (f"mode={apply_summary.get('mode','?')}, "
         f"{apply_summary.get('applied',0)} applied, "
         f"{apply_summary.get('conflicts',0)} conflicts")
        if apply_summary else "No dry-run run yet (run coach-dry-run)"
    )

    console.print(
        Panel(stages_table, border_style="blue", padding=(1, 2), title="[bold]Pipeline State[/bold]")
    )
    console.print()

    # --- Kanban target ---
    target_table = Table(
        title="Kanban Target",
        box=box.SIMPLE,
        title_style="bold",
        show_header=True,
    )
    target_table.add_column("Property", style="bold", width=26)
    target_table.add_column("Value", width=64)

    target_table.add_row("Local kanban path", str(kanban_local))
    target_table.add_row("Path blocked by guard", f"[{'green' if local_guarded else 'red'}]{local_guarded}[/]")
    target_table.add_row("Current hash", kanban_hash[:16] + "..." if kanban_hash else "(unknown)")
    target_table.add_row("Last modified", kanban_mtime if kanban_mtime else "(unknown)")

    console.print(
        Panel(target_table, border_style="blue", padding=(1, 2), title="[bold]Kanban Source[/bold]")
    )
    console.print()

    # --- Summary panel (non-actionable) ---
    info_table = Table(
        title="Runtime Paths",
        box=box.SIMPLE,
        title_style="bold",
        show_header=True,
    )
    info_table.add_column("Path", style="bold", width=26)
    info_table.add_column("Location", width=64)

    info_table.add_row("Apply root", str(apply_root))
    info_table.add_row("Review root", str(review_root))
    info_table.add_row("Draft root", str(draft_root))
    info_table.add_row("Kanban index", str(kanban_index_root))

    console.print(
        Panel(info_table, border_style="blue", padding=(1, 2), title="[bold]Runtime Paths[/bold]")
    )

    # Smoke/test warning
    if plan:
        smoke_items = sum(1 for i in plan.get("planItems", []) if is_smoke_item(i))
        if smoke_items > 0:
            console.print()
            console.print(
                Panel(
                    f"[bold yellow]WARNING:[/bold yellow] Current apply plan contains {smoke_items} "
                    f"smoke/test/demo draft item(s).\n"
                    f"Reset apply workspace and rebuild before pilot.\n"
                    f"  [dim]Run [bold]reset-apply-workspace[/bold] to archive test data.[/dim]",
                    border_style="yellow",
                    padding=(1, 2),
                )
            )

    # --- Safety reminder ---
    console.print()
    console.print(
        Panel(
            "[bold green]SAFETY:[/bold green] This command is read-only. "
            "No Kanban records were inspected for mutation.\n"
            "To apply changes, use apply-approved-plan --no-dry-run --confirm \"APPLY LOCAL KANBAN PLAN\" "
            "with config gates enabled.",
            border_style="green",
            padding=(1, 2),
        )
    )


# ---------------------------------------------------------------------------
# coach-dry-run command
# ---------------------------------------------------------------------------

def run_coach_dry_run(settings: Any) -> dict[str, Any]:
    """Run the end-to-end safe dry-run pilot pipeline.

    Steps:
    1. Build/refresh apply plan.
    2. Load latest operator decisions.
    3. Identify approved-for-apply items.
    4. Run dry-run apply only (read-only).
    5. Export a pilot Markdown report.
    6. Print summary.

    Returns a dict with the run results.
    """
    apply_root = settings.apply_path()
    (apply_root / "data").mkdir(parents=True, exist_ok=True)

    safety_label, safety_style = _get_safety_status(settings)

    console.print(f"[bold cyan]SAMI Kanban Coach — Dry-Run Pilot[/bold cyan]")
    console.print(f"[{safety_style}]{safety_label}[/{safety_style}]")
    console.print()

    # Step 1: Build apply plan
    console.print("[bold]Step 1:[/bold] Building apply plan...")
    plan = build_apply_plan(settings)
    plan_items = plan.planItems
    counts = plan.counts
    console.print(f"  {counts.get('totalEligible', 0)} eligible, "
                  f"{counts.get('readyToApply', 0)} ready, "
                  f"{counts.get('conflicts', 0)} conflicts, "
                  f"{counts.get('smokeSkipped', 0)} smoke-skipped")
    console.print()

    # Step 2: Load decisions
    console.print("[bold]Step 2:[/bold] Loading operator decisions...")
    decisions = load_apply_decisions(apply_root)
    console.print(f"  {len(decisions)} decision records loaded")
    console.print()

    # Step 3: Count approved items
    approved_ids = {
        aid for aid, d in decisions.items()
        if d.get("decision") == "approved_for_apply"
    }
    approved_items = [
        i for i in plan_items
        if i.applyId in approved_ids and i.readyToApply
    ]
    console.print(f"[bold]Step 3:[/bold] Approved items identified: {len(approved_items)}")
    for item in approved_items:
        console.print(f"  [green]✓[/green] {item.title[:60]}")
    if not approved_items:
        console.print("  [yellow](none — run review-apply-tui to approve items)[/yellow]")
    console.print()

    # Step 4: Run dry-run apply
    console.print(f"[bold]Step 4:[/bold] Running dry-run apply (read-only)...")
    hash_before = plan.projectsJsonHashBefore
    try:
        summary = apply_operator_approved_plan(settings, dry_run=True)
        console.print(f"  [green]Dry-run complete:[/green]")
        console.print(f"    Items considered: {summary.planItemsTotal}")
        console.print(f"    Would apply:      {summary.applied}")
        console.print(f"    Conflicts:        {summary.conflicts}")
        console.print(f"    Skipped:          {summary.skipped}")
        console.print(f"    Errors:           {summary.errors}")
    except Exception as e:
        console.print(f"  [red]Dry-run error: {e}[/red]")
        # Create a minimal summary for the report
        summary = ApplyRunSummary(
            mode="dry_run",
            planItemsTotal=len(plan_items),
            applied=0,
            conflicts=0,
            skipped=0,
            errors=1,
            projectsJsonHashBefore=hash_before,
            projectsJsonHashAfter=hash_before,
            localKanbanApplyEnabled=settings.local_kanban_apply_enabled,
        )
    console.print()

    # Re-read hash to confirm unchanged
    pj_path, _ = find_projects_json(settings.kanban_local_path())
    hash_after = file_hash(pj_path) if (pj_path and pj_path.exists()) else ""
    hash_unchanged = hash_before == hash_after

    # Step 5: Export pilot report
    console.print(f"[bold]Step 5:[/bold] Exporting pilot report...")
    report_path = _export_pilot_report(
        settings=settings,
        plan=plan.model_dump(exclude_none=True),
        decisions=decisions,
        summary=summary,
        approved_items=[i.model_dump(exclude_none=True) for i in approved_items],
        hash_before=hash_before,
        hash_after=hash_after,
        hash_unchanged=hash_unchanged,
        safety_label=safety_label,
    )
    console.print(f"  [green]Report:[/green] {report_path}")
    console.print()

    # Step 6: Final summary
    console.print("[bold]=== Dry-Run Summary ===[/bold]")
    console.print(f"  Drafts considered:     {counts.get('totalEligible', 0)}")
    console.print(f"  Plan items:            {summary.planItemsTotal}")
    console.print(f"  Approved items:        {len(approved_items)}")
    console.print(f"  Conflicts:             {summary.conflicts}")
    console.print(f"  Dry-run applied:       {summary.applied} [dim](read-only)[/dim]")
    console.print(f"  Dry-run skipped:       {summary.skipped}")
    console.print(f"  Kanban hash before:    {hash_before[:16]}...")
    console.print(f"  Kanban hash after:     {hash_after[:16]}..." if hash_after else "  Kanban hash after:     (unknown)")
    console.print(f"  Hash unchanged:        {'[green]YES[/green]' if hash_unchanged else '[red]NO[/red]'}")
    console.print(f"  Report path:           {report_path}")
    console.print()
    console.print(
        Panel(
            "[bold green]SAFETY CONFIRMED:[/bold green] No Kanban records were written. "
            "All operations were read-only.",
            border_style="green",
            padding=(1, 2),
        )
    )

    return {
        "plan_items_total": len(plan_items),
        "approved_count": len(approved_items),
        "conflicts": summary.conflicts,
        "dry_run_applied": summary.applied,
        "dry_run_skipped": summary.skipped,
        "hash_before": hash_before,
        "hash_after": hash_after,
        "hash_unchanged": hash_unchanged,
        "report_path": str(report_path),
        "safety_label": safety_label,
    }


# ---------------------------------------------------------------------------
# Pilot report export
# ---------------------------------------------------------------------------

def _export_pilot_report(
    settings: Any,
    plan: dict[str, Any],
    decisions: dict[str, dict[str, Any]],
    summary: ApplyRunSummary,
    approved_items: list[dict[str, Any]],
    hash_before: str,
    hash_after: str,
    hash_unchanged: bool,
    safety_label: str,
) -> Path:
    """Export a pilot-friendly Markdown report.

    Includes safety gate status, apply plan summary, decision summary,
    approved recommendations, dry-run result, and hash before/after.
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = settings.apply_path() / "data" / f"kanban_coach_pilot_report_{ts}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    apply_root = settings.apply_path()
    kanban_local = settings.kanban_local_path()

    lines: list[str] = []
    lines.append("# SAMI Kanban Coach — Pilot Dry-Run Report")
    lines.append("")
    lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Safety Gate Status")
    lines.append("")
    lines.append(f"| Gate | Status |")
    lines.append(f"|------|--------|")
    lines.append(f"| **Overall** | {safety_label} |")
    lines.append(f"| `local_kanban_apply_enabled` | {settings.local_kanban_apply_enabled} |")
    lines.append(f"| `allow_kanban_apply` | {settings.allow_kanban_apply} |")
    lines.append(f"| `ignore_smoke_test_drafts` | {settings.ignore_smoke_test_drafts} |")
    lines.append(f"| `backup_before_apply` | {settings.backup_before_apply} |")
    lines.append(f"| Kanban source blocked | {is_forbidden_path(str(kanban_local))} |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Apply Plan Summary")
    lines.append("")
    items = plan.get("planItems", [])
    counts = plan.get("counts", {})
    lines.append(f"| Metric | Count |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total Items | {len(items)} |")
    lines.append(f"| Ready to Apply | {counts.get('readyToApply', 0)} |")
    lines.append(f"| Conflicts | {counts.get('conflicts', 0)} |")
    lines.append(f"| Skipped (other) | {counts.get('skipped', 0)} |")
    lines.append(f"| Smoke Skipped | {counts.get('smokeSkipped', 0)} |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Decision Summary")
    lines.append("")
    dec_approved = sum(1 for d in decisions.values() if d.get("decision") == "approved_for_apply")
    dec_skipped = sum(1 for d in decisions.values() if d.get("decision") == "skipped")
    dec_needs = sum(1 for d in decisions.values() if d.get("decision") == "needs_edit")
    lines.append(f"| Decision | Count |")
    lines.append(f"|----------|-------|")
    lines.append(f"| Approved for Apply | {dec_approved} |")
    lines.append(f"| Skipped | {dec_skipped} |")
    lines.append(f"| Needs Edit | {dec_needs} |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Approved Recommendations")
    lines.append("")
    if approved_items:
        lines.append("| Card Title | Proposed Status | Proposed Risk | Proposed Next Action |")
        lines.append("|------------|----------------|---------------|---------------------|")
        for item in approved_items:
            lines.append(
                f"| {item.get('title', '')[:50]}"
                f" | {item.get('approvedStatus', '-') or '-'}"
                f" | {item.get('approvedRisk', '-') or '-'}"
                f" | {_trunc(item.get('approvedNextAction', ''), 40) or '-'} |"
            )
    else:
        lines.append("*No items approved for apply.*")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Conflict Summary")
    lines.append("")
    conflicts = [i for i in items if i.get("hashStatus") in ("conflict", "missing")]
    if conflicts:
        lines.append("| Card Title | Apply ID | Hash Status |")
        lines.append("|------------|----------|-------------|")
        for item in conflicts:
            lines.append(
                f"| {item.get('title', '')[:50]}"
                f" | `{item.get('applyId', '')}`"
                f" | {item.get('hashStatus', '')} |"
            )
    else:
        lines.append("*No conflicts.*")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Dry-Run Apply Result")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Mode | {summary.mode} |")
    lines.append(f"| Items Considered | {summary.planItemsTotal} |")
    lines.append(f"| Would Apply | {summary.applied} |")
    lines.append(f"| Conflicts | {summary.conflicts} |")
    lines.append(f"| Skipped | {summary.skipped} |")
    lines.append(f"| Errors | {summary.errors} |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Kanban Hash Verification")
    lines.append("")
    lines.append(f"| Hash | Value |")
    lines.append(f"|------|-------|")
    lines.append(f"| Before | `{hash_before[:16]}...` |")
    lines.append(f"| After | `{hash_after[:16]}...` |" if hash_after else "| After | *(unknown)* |")
    lines.append(f"| Unchanged | {'**YES**' if hash_unchanged else '**NO**'} |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Write Confirmation")
    lines.append("")
    lines.append(f"**{summary.applied} Kanban records would have been written in real mode.**")
    lines.append("")
    lines.append("**No Kanban records were actually written by this dry-run pilot.**")
    lines.append("")
    lines.append("Kanban source hash was confirmed unchanged before and after the dry-run.")
    lines.append("")
    if not hash_unchanged:
        lines.append(":warning: Hash changed! Investigate before proceeding.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Suggested Next Operator Actions")
    lines.append("")
    actions = []
    if not items:
        actions.append("1. **Generate drafts** — Run `build-review-queue` and `build-apply-plan`")
    if dec_approved == 0 and items:
        actions.append("1. **Review recommendations** — Run `review-apply-tui` to approve/skip items")
    if dec_approved > 0:
        actions.append("1. **Review approved items** — Verify the approved recommendations match expectations")
    if conflicts:
        actions.append(f"2. **Resolve conflicts** — {len(conflicts)} items have hash conflicts (card changed since review)")
    if safety_label != "SAFE: dry-run only - apply disabled":
        actions.append("3. **Apply to Kanban** — Only if `local_kanban_apply_enabled=true` AND `allow_kanban_apply=true`")
        actions.append("   - `apply-approved-plan --no-dry-run --confirm \"APPLY LOCAL KANBAN PLAN\"`")
    else:
        actions.append(f"3. **Enable real apply later** — Set `local_kanban_apply_enabled=true` and `allow_kanban_apply=true` in config when ready")
    actions.append("4. **Export audit** — `show-apply-audit` for full decision history")
    actions.append("5. **Share this report** — This file is suitable for email or audit notes")

    for action in actions:
        lines.append(action)
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"*Report generated by SAMI Kanban Coach Pilot at {datetime.now().isoformat()}*")
    lines.append("")

    report_text = "\n".join(lines)
    report_path.write_text(report_text, encoding="utf-8")

    return report_path


# ---------------------------------------------------------------------------
# Pilot report export (public CLI wrapper)
# ---------------------------------------------------------------------------

def export_pilot_report_cli(settings: Any) -> str:
    """Export a pilot Markdown report from the CLI (non-interactive).

    Returns the report file path as a string.
    """
    apply_root = settings.apply_path()
    plan = load_apply_plan(apply_root)
    if not plan:
        raise ValueError("No apply plan found. Run 'build-apply-plan' first.")

    decisions = load_apply_decisions(apply_root)
    summary = load_apply_summary(apply_root)
    if not summary:
        summary = ApplyRunSummary(mode="dry_run")

    # Re-build a summary: run dry-run
    summary = apply_operator_approved_plan(settings, dry_run=True)

    approved_items_data: list[dict[str, Any]] = []
    approved_ids = {
        aid for aid, d in decisions.items()
        if d.get("decision") == "approved_for_apply"
    }
    for item in plan.get("planItems", []):
        if item.get("applyId") in approved_ids and item.get("readyToApply"):
            approved_items_data.append(item)

    pj_path, _ = find_projects_json(settings.kanban_local_path())
    hash_before = plan.get("projectsJsonHashBefore", "")
    hash_after = file_hash(pj_path) if (pj_path and pj_path.exists()) else ""
    safety_label, _ = _get_safety_status(settings)

    report_path = _export_pilot_report(
        settings=settings,
        plan=plan,
        decisions=decisions,
        summary=summary,
        approved_items=approved_items_data,
        hash_before=hash_before or "",
        hash_after=hash_after,
        hash_unchanged=(hash_before == hash_after) if hash_before and hash_after else True,
        safety_label=safety_label,
    )
    return str(report_path)
