"""Rich-based TUI review console for Phase 4D apply plan review.

Provides an interactive terminal review interface for operators to
review, approve, skip, or mark items as needing edits before applying.
Uses only Rich (no Textual dependency required).
Writes decisions to apply_review_decisions.jsonl only. Never writes Kanban.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.prompt import Prompt
from rich import box
from rich.columns import Columns

from .apply_engine import (
    load_apply_plan,
    load_apply_decisions,
    DECISIONS_FILE,
    is_smoke_item,
    is_smoke_decision,
)
from .kanban_reader import find_projects_json

console = Console()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONTROLS_HELP = (
    "n Next | p Previous | a Approve | s Skip | e Needs edit | "
    "r Reason | v Full view | m Summary | x Export | q Quit"
)

SAFETY_NOTE = "[dim]Read-only review: decisions are recorded, Kanban is not updated.[/dim]"

DECISION_LABELS: dict[str, tuple[str, str]] = {
    "approved_for_apply": ("[bold green]APPROVED[/bold green]", "green"),
    "skipped": ("[bold yellow]SKIPPED[/bold yellow]", "yellow"),
    "needs_edit": ("[bold magenta]NEEDS EDIT[/bold magenta]", "magenta"),
    "pending": ("[bold white]PENDING[/bold white]", "white"),
}

DECISION_COLORS: dict[str, str] = {
    "approved_for_apply": "green",
    "skipped": "yellow",
    "needs_edit": "magenta",
    "pending": "white",
}

HASH_LABELS: dict[str, tuple[str, str]] = {
    "match": ("[bold green]match[/bold green]", "green"),
    "conflict": ("[bold red]CONFLICT[/bold red]", "red"),
    "missing": ("[bold yellow]missing[/bold yellow]", "yellow"),
}

# Values that mean "no change proposed"
_NO_CHANGE_VALUES = {"", None, "(no change)", "no change", "unchanged"}

# Fields to compare in the before/after table
_FIELD_SPECS = [
    ("Status", "status", "approvedStatus"),
    ("Risk", "riskColour", "approvedRisk"),
    ("Lead / Owner", "projectLead", "approvedLead"),
    ("Current State", "context", "approvedCurrentState"),
    ("Next Action", "nextAction", "approvedNextAction"),
    ("Review Date", "reviewDate", None),
    ("Notes", "notes", None),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_current_card(kanban_root: Path, project_id: str) -> dict[str, Any] | None:
    """Load the current card state from projects.json for comparison."""
    projects, _ = _load_projects_json(kanban_root)
    for p in projects:
        if p.get("id") == project_id or p.get("projectId") == project_id:
            return p
    return None


def _load_projects_json(kanban_root: Path) -> tuple[list[dict[str, Any]], str]:
    """Load projects.json and return (list, hash)."""
    pj_path, _ = find_projects_json(kanban_root)
    if not pj_path or not pj_path.exists():
        return [], ""
    try:
        with open(pj_path, "r", encoding="utf-8-sig") as f:
            raw = json.load(f)
        projects: list[dict[str, Any]] = []
        if isinstance(raw, dict) and "projects" in raw:
            projects = raw["projects"]
        elif isinstance(raw, list):
            projects = raw
        import hashlib
        raw_str = json.dumps(raw, sort_keys=True, ensure_ascii=False)
        pj_hash = hashlib.sha256(raw_str.encode()).hexdigest()
        return projects, pj_hash
    except Exception:
        return [], ""


def _get_decision_status(
    apply_id: str,
    decisions: dict[str, dict[str, Any]],
) -> tuple[str, dict[str, Any] | None]:
    """Get the current decision for an applyId.

    Returns (decision_label, decision_record_or_None).
    """
    if apply_id in decisions:
        d = decisions[apply_id]
        return d.get("decision", "pending"), d
    return "pending", None


def _trunc(s: str | None, max_len: int = 100) -> str:
    if not s:
        return ""
    return s if len(s) <= max_len else s[:max_len] + "..."


def _normalise_proposed(proposed: Any) -> str | None:
    """Return the display value for a proposed field, or None if no change."""
    if proposed is None:
        return None
    val = str(proposed).strip()
    if not val or val.lower() in ("(no change)", "no change", "unchanged", "none"):
        return None
    return val


def _is_changed(current: str, proposed: str | None) -> bool:
    """Check if a proposed value represents a genuine change."""
    if proposed is None:
        return False
    cur = (current or "").strip()
    return cur != proposed


def _save_tui_decision(
    apply_root: Path,
    item: dict[str, Any],
    decision: str,
    reason: str = "",
    approved_by: str = "Brian",
) -> dict[str, Any]:
    """Save a TUI decision enriched with card metadata from the plan item."""
    from .logging_setup import setup_logging

    enriched = {
        "schemaVersion": 1,
        "timestamp": datetime.now().isoformat(),
        "applyId": item.get("applyId", ""),
        "decision": decision,
        "reason": reason,
        "approvedBy": approved_by,
        "cardId": item.get("projectId", ""),
        "cardTitle": item.get("title", ""),
        "sourceDraftId": item.get("draftId", ""),
    }
    path = apply_root / "data" / DECISIONS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(enriched, ensure_ascii=False) + "\n")
        f.flush()
    logger = setup_logging(Path("runtime/email_recall/logs"))
    logger.info("TUI decision saved: %s -> %s -- %s",
                enriched["applyId"], decision, item.get("title", ""))
    return enriched


# ---------------------------------------------------------------------------
# Filtered-item diagnostic helpers
# ---------------------------------------------------------------------------

def _get_filter_reasons(item: dict[str, Any], dec: dict[str, Any] | None = None) -> list[str]:
    """Return human-readable list of reasons an item was classified as smoke/test/demo."""
    reasons: list[str] = []
    gen_by = str(item.get("generatedBy", "") or "")
    if gen_by == "phase4a_smoke_test":
        reasons.append(f"generatedBy=phase4a_smoke_test")

    keys = item.get("sourceEmailKeys", []) or []
    if "SMOKE_TEST_EMAIL_KEY" in keys:
        reasons.append("sourceEmailKeys contains SMOKE_TEST_EMAIL_KEY")

    for field in ["title", "approvedCurrentState", "approvedNextAction", "approvedStatus", "approvedRisk"]:
        val = str(item.get(field, "") or "")
        if "EDITED SMOKE" in val.upper():
            reasons.append(f"proposed {field} contains \"EDITED SMOKE\"")
        elif "SMOKE" in val.upper():
            reasons.append(f"proposed {field} contains \"SMOKE\"")

    # Evidence
    for ev in (item.get("evidence", []) or []):
        for ev_field in ["subject", "summary", "messageKey"]:
            ev_val = str(ev.get(ev_field, "") or "")
            if "SMOKE" in ev_val.upper():
                reasons.append(f"evidence {ev_field} contains \"SMOKE\"")

    # Decision reason
    if dec:
        reason_str = str(dec.get("reason", "") or "")
        if "smoke draft" in reason_str.lower():
            reasons.append("operator note/reason contains \"smoke draft\"")

    if not reasons:
        reasons.append("unknown filter criteria")
    return reasons


def _show_filtered_only_screen(
    all_items: list[dict[str, Any]],
    filtered_items: list[dict[str, Any]],
    decisions: dict[str, dict[str, Any]],
    plan: dict[str, Any],
    apply_root: Path,
    settings: Any,
) -> None:
    """Show a rich diagnostic screen when all items are filtered smoke/test/demo.

    Read-only: no approve/skip/needs-edit actions.
    """
    total = len(all_items)
    filtered = len(filtered_items)

    console.clear()
    console.print()

    # Header panel
    header = Panel(
        "[bold yellow]No Real Recommendations to Review[/bold yellow]\n\n"
        f"Apply plan: {plan.get('kanbanRoot', '')}\n"
        f"Total plan items: {total}\n"
        f"Real reviewable items: [green]0[/green]\n"
        f"Filtered smoke/test/demo items: [yellow]{filtered}[/yellow]\n"
        f"Safety status: [bold green]SAFE / READ ONLY[/bold green]\n\n"
        "[dim]These items were excluded from normal review because "
        "ignore_smoke_test_drafts=true.[/dim]",
        border_style="yellow",
        padding=(2, 3),
    )
    console.print(header)
    console.print()

    # Filtered items table
    ftable = Table(
        title="Filtered Items -- Read Only",
        box=box.SIMPLE,
        title_style="bold yellow",
        show_header=True,
    )
    ftable.add_column("#", width=3)
    ftable.add_column("Card Title", style="cyan", width=28)
    ftable.add_column("Apply ID", width=16)
    ftable.add_column("Filter Reason", width=30, overflow="fold")
    ftable.add_column("Status: cur->proposed", width=20)
    ftable.add_column("Risk: cur->proposed", width=16)
    ftable.add_column("Next Action", width=24, overflow="fold")
    ftable.add_column("Decision", width=14)

    for i, item in enumerate(filtered_items, 1):
        aid = item.get("applyId", "")
        dec_status, dec_rec = _get_decision_status(aid, decisions)
        reasons = _get_filter_reasons(item, dec_rec)
        reason_text = reasons[0][:28] if reasons else "test data"

        cur_status = ""
        new_status = _normalise_proposed(item.get("approvedStatus"))
        status_str = f"{cur_status}->{new_status}" if new_status else "(no change)"

        cur_risk = ""
        new_risk = _normalise_proposed(item.get("approvedRisk"))
        risk_str = f"{cur_risk}->{new_risk}" if new_risk else "(no change)"

        na = _trunc(item.get("approvedNextAction", "") or "", 22) or "-"

        dec_label = dec_status.capitalize().replace("_", " ")
        color = DECISION_COLORS.get(dec_status, "white")

        ftable.add_row(
            str(i),
            _trunc(item.get("title", ""), 26),
            aid[:14],
            reason_text,
            status_str,
            risk_str,
            na,
            f"[{color}]{dec_label}[/{color}]",
        )

    console.print(Panel(ftable, border_style="blue", padding=(1, 2)))
    console.print()

    # Interactive loop for filtered items
    idx = 0
    while 0 <= idx < filtered:
        item = filtered_items[idx]
        aid = item.get("applyId", "")
        dec_status, dec_rec = _get_decision_status(aid, decisions)
        reasons = _get_filter_reasons(item, dec_rec)

        console.print(f"[bold]Item {idx+1}/{filtered}:[/bold] {item.get('title', '')}")
        console.print()

        console.print("[bold]Card Details:[/bold]")
        console.print(f"  Apply ID:      {aid}")
        console.print(f"  Draft ID:      {item.get('draftId', '')}")
        console.print(f"  Project ID:    {item.get('projectId', '')}")
        console.print(f"  Source Type:   {item.get('sourceType', '')}")
        console.print(f"  Hash Status:   {item.get('hashStatus', '')}")
        console.print(f"  Confidence:    {item.get('confidence', 0):.0%}")
        console.print(f"  Decision:      {dec_status.capitalize().replace('_', ' ')}")
        console.print()

        console.print("[bold]Filter Reason(s):[/bold]")
        for r_text in reasons:
            console.print(f"  - {r_text}")
        console.print()

        console.print("[bold]Proposed Changes:[/bold]")
        console.print(f"  Current State: {item.get('approvedCurrentState', '(no change)')}")
        console.print(f"  Next Action:   {item.get('approvedNextAction', '(no change)')}")
        console.print(f"  Status:        {item.get('approvedStatus', '(no change)')}")
        console.print(f"  Risk:          {item.get('approvedRisk', '(no change)')}")
        console.print()

        console.print("[dim]This item is filtered as smoke/test/demo data. "
                      "It cannot be approved from this screen.[/dim]")
        console.print()
        console.print("[dim]n=next p=prev v=full x=export-report q=quit[/dim]")

        cmd = Prompt.ask("Action", default="n").strip().lower()

        if cmd == "n":
            if idx < filtered - 1:
                idx += 1
            else:
                console.print("[yellow]Already at last filtered item.[/yellow]")
                Prompt.ask("Press Enter to continue", default="")
        elif cmd == "p":
            if idx > 0:
                idx -= 1
            else:
                console.print("[yellow]Already at first filtered item.[/yellow]")
                Prompt.ask("Press Enter to continue", default="")
        elif cmd in ("v", "view", "view-full"):
            console.clear()
            console.print(f"[bold cyan]Full View (Filtered): {item.get('title', '')}[/bold cyan]")
            console.print()
            console.print("[bold]Card Details:[/bold]")
            console.print(f"  Apply ID:      {aid}")
            console.print(f"  Draft ID:      {item.get('draftId', '')}")
            console.print(f"  Project ID:    {item.get('projectId', '')}")
            console.print(f"  Source Type:   {item.get('sourceType', '')}")
            console.print(f"  Hash Status:   {item.get('hashStatus', '')}")
            console.print(f"  Ready:         {item.get('readyToApply', False)}")
            console.print(f"  Skip Reason:   {item.get('skipReason', '(none)')}")
            console.print(f"  Confidence:    {item.get('confidence', 0):.0%}")
            console.print()
            console.print("[bold]Filter Reason(s):[/bold]")
            for r_text in reasons:
                console.print(f"  - {r_text}")
            console.print()
            console.print("[bold]Proposed Values:[/bold]")
            console.print(f"  Status:       {item.get('approvedStatus', '(no change)')}")
            console.print(f"  Risk:         {item.get('approvedRisk', '(no change)')}")
            console.print(f"  Current State:{item.get('approvedCurrentState', '(no change)')}")
            console.print(f"  Next Action:  {item.get('approvedNextAction', '(no change)')}")
            console.print()
            evidence = item.get("evidence", []) or []
            if evidence:
                console.print(f"[bold]Evidence ({len(evidence)} items):[/bold]")
                for ev in evidence[:3]:
                    console.print(f"  - {ev.get('subject','')[:60]}")
            console.print()
            console.print(f"Source email keys: {item.get('sourceEmailKeys', [])}")
            console.print(f"Source card hash:  {item.get('sourceCardHash', '')[:16]}...")
            console.print()
            Prompt.ask("Press Enter to return", default="")
        elif cmd in ("x", "export"):
            console.clear()
            console.print("[bold cyan]Export Filtered Diagnostic Report[/bold cyan]")
            console.print()
            default_name = f"filtered_diagnostic_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
            default_path = apply_root / "data" / default_name
            out = Prompt.ask(
                "Output path (Enter for default)",
                default=str(default_path),
            ).strip()
            if not out:
                out = str(default_path)
            _export_filtered_diagnostic_report(
                all_items, filtered_items, decisions, plan, apply_root, out
            )
            console.print()
            Prompt.ask("Press Enter to return", default="")
        elif cmd in ("q", "quit"):
            console.print("[yellow]Exiting filtered diagnostic view.[/yellow]")
            break
        else:
            console.print(f"[red]Unknown command: '{cmd}'[/red]")
            Prompt.ask("Press Enter to continue", default="")

    # Final footer
    console.clear()
    console.print()
    console.print(Panel(
        "[bold]Filtered Diagnostic View Complete[/bold]\n\n"
        "Next steps:\n"
        "  - Run [bold]reset-apply-workspace[/bold] to archive test plan/decisions\n"
        "  - Build a plan from approved non-smoke drafts\n"
        "  - Run [bold]coach-status[/bold] to check pipeline state\n"
        "  - Demo fixture mode must be explicit",
        border_style="green",
        padding=(2, 3),
    ))
    console.print()


def _export_filtered_diagnostic_report(
    all_items: list[dict[str, Any]],
    filtered_items: list[dict[str, Any]],
    decisions: dict[str, dict[str, Any]],
    plan: dict[str, Any],
    apply_root: Path,
    output_path: str | Path,
) -> str:
    """Export a diagnostic Markdown report for filtered smoke/test/demo items."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines.append("# SAMI Kanban Coach -- Filtered Diagnostic Report")
    lines.append("")
    lines.append("*No real recommendations were available. This report shows filtered test data only.*")
    lines.append("")
    lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append(f"**Apply Plan:** `{plan.get('kanbanRoot', '')}`")
    lines.append(f"**Total Items:** {len(all_items)}")
    lines.append(f"**Filtered Test Items:** {len(filtered_items)}")
    lines.append(f"**Real Reviewable:** 0")
    lines.append("")
    lines.append("**Safety:** No Kanban writes performed. This is diagnostic data only.")
    lines.append("")
    lines.append("---")
    lines.append("")

    for i, item in enumerate(filtered_items, 1):
        aid = item.get("applyId", "")
        title = item.get("title", "")
        dec_status, dec_rec = _get_decision_status(aid, decisions)
        reasons = _get_filter_reasons(item, dec_rec)

        lines.append(f"### {i}. {title}")
        lines.append("")
        lines.append(f"| Field | Value |")
        lines.append(f"|-------|-------|")
        lines.append(f"| **Apply ID** | `{aid}` |")
        lines.append(f"| **Draft ID** | `{item.get('draftId', '')}` |")
        lines.append(f"| **Decision** | {dec_status.capitalize().replace('_', ' ')} |")
        lines.append(f"| **Hash Status** | {item.get('hashStatus', '')} |")
        lines.append(f"| **Confidence** | {item.get('confidence', 0):.0%} |")
        lines.append("")
        lines.append("#### Filter Reasons")
        lines.append("")
        for r_text in reasons:
            lines.append(f"- {r_text}")
        lines.append("")
        lines.append("#### Proposed Changes")
        lines.append("")
        lines.append(f"| Field | Proposed Value |")
        lines.append(f"|-------|---------------|")
        if item.get("approvedStatus"):
            lines.append(f"| Status | {item['approvedStatus']} |")
        if item.get("approvedRisk"):
            lines.append(f"| Risk | {item['approvedRisk']} |")
        if item.get("approvedCurrentState"):
            lines.append(f"| Current State | {item['approvedCurrentState']} |")
        if item.get("approvedNextAction"):
            lines.append(f"| Next Action | {item['approvedNextAction']} |")
        lines.append("")
        lines.append("---")
        lines.append("")

    lines.append("*Report generated from filtered test data. No Kanban writes performed.*")
    lines.append("")

    report = "\n".join(lines)
    output_path.write_text(report, encoding="utf-8")
    console.print(f"[green]Filtered diagnostic report exported:[/green] {output_path}")
    return report


# ---------------------------------------------------------------------------
# Before / After Comparison Table
# ---------------------------------------------------------------------------

def _build_comparison_table(
    item: dict[str, Any],
    current_card: dict[str, Any] | None,
) -> Table:
    """Build a before/after comparison table for a plan item.

    Shows current value, proposed value, and change state.
    Only marks CHANGED when proposed is genuinely different from current.
    """
    table = Table(
        box=box.SIMPLE,
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
        title="Before / After Comparison",
        title_style="bold",
        expand=True,
    )
    table.add_column("Field", style="bold", width=16, no_wrap=True)
    table.add_column("Current", style="white", width=40, overflow="fold")
    table.add_column("Proposed", style="white", width=40, overflow="fold")
    table.add_column("Result", width=12)

    for field_label, cur_field, prop_field in _FIELD_SPECS:
        cur_val = ((current_card or {}).get(cur_field, "") or "").strip()

        if prop_field:
            proposed = _normalise_proposed(item.get(prop_field))
            if proposed is not None:
                proposed_display = proposed[:80] + ("..." if len(proposed) > 80 else "")
            else:
                proposed_display = "(no change)"
            changed = _is_changed(cur_val, proposed)
        else:
            proposed_display = "(no change)"
            changed = False

        if changed:
            result_text = "[bold yellow]CHANGED[/bold yellow]"
        elif proposed is not None:
            result_text = "[dim]unchanged[/dim]"
        else:
            result_text = "[dim]unchanged[/dim]"

        if field_label == "Risk" and cur_val:
            # Use risk colour as visual indicator — wrap in matching style tag
            risk_color = cur_val.lower().strip()
            cur_display = f"[{risk_color}]{cur_val}[/{risk_color}]" if risk_color in ("red", "green", "yellow", "blue", "magenta", "cyan", "white") else cur_val
        else:
            cur_display = cur_val[:80] + ("..." if len(cur_val) > 80 else "") if cur_val else "(empty)"

        style = "" if changed else "dim"
        cur_cell = f"[{style}]{cur_display}[/{style}]" if style and cur_display else (cur_display if cur_display else "")
        prop_cell = f"[{style}]{proposed_display}[/{style}]" if style and proposed_display else (proposed_display if proposed_display else "")
        table.add_row(
            field_label,
            cur_cell or "",
            prop_cell or "",
            result_text,
        )

    return table


# ---------------------------------------------------------------------------
# Panel builders
# ---------------------------------------------------------------------------

def _build_header_panel(
    idx: int,
    total: int,
    item: dict[str, Any],
    decisions: dict[str, dict[str, Any]],
    counts: dict[str, int],
) -> Panel:
    """Build the top header panel with summary counts."""
    apply_id = item.get("applyId", "")
    dec_status, _ = _get_decision_status(apply_id, decisions)
    dec_label, _ = DECISION_LABELS.get(dec_status, ("[bold white]PENDING[/bold white]", "white"))
    hash_status = item.get("hashStatus", "")
    hl_label, _ = HASH_LABELS.get(hash_status, (hash_status, "white"))

    approved_count = counts.get("approved", 0)
    skipped_count = counts.get("skipped", 0)
    pending_count = counts.get("pending", total)
    needs_edit_count = counts.get("needs_edit", 0)
    conflict_count = counts.get("conflicts", 0)

    status_line = (
        f"[bold]Item {idx + 1}/{total}[/bold]"
        f"  |  Pending: [yellow]{pending_count}[/yellow]"
        f"  |  Approved: [green]{approved_count}[/green]"
        f"  |  Skipped: {skipped_count}"
        f"  |  Needs edit: [magenta]{needs_edit_count}[/magenta]"
        f"  |  Conflicts: [red]{conflict_count}[/red]"
    )

    title_text = (
        "[bold cyan]SAMI Kanban Coach[/bold cyan]\n"
        "[bold]Apply Review Console[/bold]\n"
        f"{status_line}\n"
        "[dim]Read-only review: decisions are recorded, Kanban is not updated.[/dim]"
    )

    return Panel(title_text, border_style="cyan", padding=(1, 2))


def _build_item_detail_panel(
    item: dict[str, Any],
    current_card: dict[str, Any] | None,
    dec_status: str,
    dec_record: dict[str, Any] | None,
) -> Panel:
    """Build the main item detail panel with card info and comparison."""
    title = item.get("title", "[no title]")
    apply_id = item.get("applyId", "")
    draft_id = item.get("draftId", "")
    project_id = item.get("projectId", "")
    source_type = item.get("sourceType", "")
    hash_status = item.get("hashStatus", "")
    ready = item.get("readyToApply", False)
    skip_reason = item.get("skipReason", "")

    dec_label, dec_style = DECISION_LABELS.get(dec_status, ("[bold white]PENDING[/bold white]", "white"))
    hl_label, _ = HASH_LABELS.get(hash_status, (hash_status, "white"))

    # Two-column card details table
    card_table = Table.grid(padding=(0, 4))
    card_table.add_column(style="bold", width=14)
    card_table.add_column(style="white", width=36)
    card_table.add_column(style="bold", width=14)
    card_table.add_column(style="white", width=36)

    operator_note = ""
    if dec_record and dec_record.get("reason"):
        operator_note = f"[italic]{dec_record['reason']}[/italic]"

    card_table.add_row(
        "Card:", title[:50],
        "Project ID:", _trunc(project_id, 30),
    )
    card_table.add_row(
        "Apply ID:", _trunc(apply_id, 30),
        "Draft ID:", _trunc(draft_id, 26),
    )
    card_table.add_row(
        "Source:", source_type,
        "Hash:", hl_label,
    )
    card_table.add_row(
        "Ready:", "[green]YES[/green]" if ready else "[red]no[/red]",
        "Decision:", dec_label,
    )
    if operator_note:
        card_table.add_row(
            "Note:", operator_note,
            "", "",
        )
    if skip_reason:
        card_table.add_row(
            "Skip:", f"[yellow]{skip_reason}[/yellow]",
            "", "",
        )

    # Comparison table
    comp_table = _build_comparison_table(item, current_card)

    # Combine in a vertical layout
    layout = Table.grid(padding=(0, 1))
    layout.add_row(Panel(card_table, border_style="blue", padding=(1, 2), title="[bold]Card Details[/bold]"))
    layout.add_row(Panel(comp_table, border_style="blue", padding=(1, 2)))

    return Panel(layout, border_style="bright_blue", padding=(0, 1))


def _build_footer_panel() -> Panel:
    """Build the footer panel with controls and safety note."""
    content = CONTROLS_HELP + "\n" + SAFETY_NOTE
    return Panel(content, border_style="dim", padding=(1, 2))


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def _build_summary(items: list[dict[str, Any]], decisions: dict[str, dict[str, Any]]) -> None:
    """Display a summary table of all items with their decisions."""
    total = len(items)
    pending = 0
    approved = 0
    skipped = 0
    needs_edit = 0
    ready_count = 0
    non_ready = 0
    conflicts = 0
    noop = 0

    for item in items:
        aid = item.get("applyId", "")
        dec, _ = _get_decision_status(aid, decisions)
        if dec == "approved_for_apply":
            approved += 1
        elif dec == "skipped":
            skipped += 1
        elif dec == "needs_edit":
            needs_edit += 1
        else:
            pending += 1

        if item.get("readyToApply"):
            ready_count += 1
        else:
            non_ready += 1

        if item.get("hashStatus") in ("conflict", "missing"):
            conflicts += 1

        has_changes = any([
            item.get("approvedCurrentState"),
            item.get("approvedNextAction"),
            item.get("approvedStatus"),
            item.get("approvedRisk"),
        ])
        if not has_changes:
            noop += 1

    summary = Table(title="Apply Review Summary", box=box.ROUNDED, title_style="bold cyan")
    summary.add_column("Metric", style="bold", width=22)
    summary.add_column("Count", style="bold", width=8)
    summary.add_column("Details", width=44)

    summary.add_row("Total Items", str(total), "")
    summary.add_row("Pending", str(pending), f"[yellow]{pending} awaiting decision[/yellow]")
    summary.add_row("Approved for Apply", str(approved), f"[green]{approved} ready[/green]")
    summary.add_row("Skipped", str(skipped), f"[dim]{skipped} excluded[/dim]")
    summary.add_row("Needs Edit", str(needs_edit), f"[magenta]{needs_edit} flagged[/magenta]")
    summary.add_row("Ready to Apply", str(ready_count),
                    "[green]hash match[/green]" if ready_count else "[dim]none[/dim]")
    summary.add_row("Not Applyable", str(non_ready),
                    "[red]blocked[/red]" if non_ready else "[dim]none[/dim]")
    summary.add_row("Conflicts", str(conflicts),
                    "[red]hash mismatch[/red]" if conflicts else "[dim]none[/dim]")
    summary.add_row("No-op Items", str(noop), "[dim]no changes proposed[/dim]")

    console.print()
    console.print(summary)
    console.print()

    if approved:
        approved_table = Table(
            title="Approved Items",
            box=box.SIMPLE,
            title_style="bold green",
        )
        approved_table.add_column("Card Title", style="cyan", width=30)
        approved_table.add_column("Status", width=12)
        approved_table.add_column("Risk", width=10)
        approved_table.add_column("Lead", width=12)
        approved_table.add_column("Next Action", width=28)
        approved_table.add_column("Conf.", width=8)

        for item in items:
            aid = item.get("applyId", "")
            dec, _ = _get_decision_status(aid, decisions)
            if dec == "approved_for_apply":
                conf = item.get("confidence", 0) or 0
                approved_table.add_row(
                    _trunc(item.get("title", ""), 28),
                    item.get("approvedStatus", "-") or "-",
                    item.get("approvedRisk", "-") or "-",
                    _trunc(item.get("approvedLead", ""), 10) or "-",
                    _trunc(item.get("approvedNextAction", ""), 26) or "-",
                    f"{conf:.0%}" if conf else "-",
                )
        console.print(approved_table)
        console.print()

    console.print("[dim]This summary is read-only. No Kanban writes performed.[/dim]")
    console.print()


# ---------------------------------------------------------------------------
# Markdown Report Export
# ---------------------------------------------------------------------------

def _export_markdown_report(
    plan: dict[str, Any],
    items: list[dict[str, Any]],
    decisions: dict[str, dict[str, Any]],
    apply_root: Path,
    output_path: str | Path | None = None,
) -> str:
    """Export a Markdown review report, excluding smoke/test items by default."""
    if output_path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = apply_root / "data" / f"apply_review_report_{ts}.md"

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Filter smoke items
    real_items = [i for i in items if not is_smoke_item(i)]
    smoke_count = len(items) - len(real_items)

    approved = sum(1 for i in real_items if _get_decision_status(i.get("applyId", ""), decisions)[0] == "approved_for_apply")
    skipped = sum(1 for i in real_items if _get_decision_status(i.get("applyId", ""), decisions)[0] == "skipped")
    needs_edit = sum(1 for i in real_items if _get_decision_status(i.get("applyId", ""), decisions)[0] == "needs_edit")
    pending = len(real_items) - approved - skipped - needs_edit

    lines: list[str] = []
    lines.append("# SAMI Kanban Coach Review Report")
    lines.append("")
    lines.append("*This report was generated from review data only. No Kanban writes were performed.*")
    lines.append("")
    lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append(f"**Apply Plan:** `{plan.get('kanbanRoot', '')}`")
    lines.append(f"**Kanban Hash:** `{str(plan.get('projectsJsonHashBefore', ''))[:16]}...`")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Decision Summary")
    lines.append("")
    lines.append(f"| Metric | Count |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total Real Items | {len(real_items)} |")
    lines.append(f"| Approved for Apply | {approved} |")
    lines.append(f"| Skipped | {skipped} |")
    lines.append(f"| Needs Edit | {needs_edit} |")
    lines.append(f"| Pending | {pending} |")
    if smoke_count > 0:
        lines.append(f"| Smoke/Test Items Excluded | {smoke_count} |")
    lines.append("")
    lines.append("---")
    lines.append("")

    if not real_items:
        lines.append("*No real recommendations to review.*")
        if smoke_count > 0:
            lines.append(f"*{smoke_count} smoke/test items were excluded.*")
        lines.append("")
        lines.append("---")
        lines.append("")

    for item in real_items:
        aid = item.get("applyId", "")
        title = item.get("title", "")
        dec, rec = _get_decision_status(aid, decisions)
        dec_label = dec.capitalize().replace("_", " ")
        reason = rec.get("reason", "") if rec else ""
        hash_status = item.get("hashStatus", "")
        conf = item.get("confidence", 0) or 0
        draft_id = item.get("draftId", "")
        ready = item.get("readyToApply", False)

        lines.append(f"### {title}")
        lines.append("")
        lines.append(f"| Field | Value |")
        lines.append(f"|-------|-------|")
        lines.append(f"| **Apply ID** | `{aid}` |")
        lines.append(f"| **Draft ID** | `{draft_id}` |")
        lines.append(f"| **Decision** | {dec_label} |")
        lines.append(f"| **Reason** | {reason or '(none)'} |")
        lines.append(f"| **Hash Status** | {hash_status} |")
        lines.append(f"| **Ready** | {'Yes' if ready else 'No'} |")
        lines.append(f"| **Confidence** | {conf:.0%} |")
        lines.append("")
        lines.append("#### Proposed Changes")
        lines.append("")
        lines.append(f"| Field | Proposed Value |")
        lines.append(f"|-------|---------------|")
        for label, _, prop_field in _FIELD_SPECS:
            if prop_field:
                pv = _normalise_proposed(item.get(prop_field))
                if pv is not None:
                    lines.append(f"| {label} | {pv} |")
        lines.append("")
        lines.append("---")
        lines.append("")

    lines.append(f"*Report exported at {datetime.now().isoformat()}*")
    lines.append("")

    report = "\n".join(lines)
    output_path.write_text(report, encoding="utf-8")
    console.print(f"[green]Report exported:[/green] {output_path}")

    return report


# ---------------------------------------------------------------------------
# Recommendation sorting and badge helpers
# ---------------------------------------------------------------------------

def _get_item_badge(item: dict[str, Any]) -> str:
    """Return a Rich-style status badge string for an item."""
    ready = item.get("readyToApply", False)
    hash_status = item.get("hashStatus", "")
    has_changes = any([
        _normalise_proposed(item.get("approvedCurrentState")),
        _normalise_proposed(item.get("approvedNextAction")),
        _normalise_proposed(item.get("approvedStatus")),
        _normalise_proposed(item.get("approvedRisk")),
    ])
    if hash_status in ("conflict", "missing"):
        return "[bold red]CONFLICT[/bold red]"
    if not ready:
        return "[bold yellow]BLOCKED[/bold yellow]"
    if has_changes:
        return "[bold green]UPDATE[/bold green]"
    return "[bold dim]UNCHANGED[/bold dim]"


def _sort_review_items(
    items: list[dict[str, Any]],
    decisions: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Sort recommendations in priority order.

    Order:
    1. Real update candidates (ready + has changes), higher confidence first
    2. No-op/unchanged items
    3. Filtered smoke/test/demo items (last, diagnostic only)
    """
    def _sort_key(item: dict[str, Any]) -> tuple:
        ready = item.get("readyToApply", False)
        hash_ok = item.get("hashStatus") == "match"
        is_smoke = is_smoke_item(item)
        has_changes = any([
            _normalise_proposed(item.get("approvedCurrentState")),
            _normalise_proposed(item.get("approvedNextAction")),
            _normalise_proposed(item.get("approvedStatus")),
            _normalise_proposed(item.get("approvedRisk")),
        ])
        conf = item.get("confidence", 0) or 0
        dec_status, _ = _get_decision_status(item.get("applyId", ""), decisions)
        dec_score = 0 if dec_status == "pending" else 1

        return (
            1 if is_smoke else 0,                         # smoke last
            -1 if (ready and hash_ok and has_changes) else 0,  # real updates first
            -conf if (ready and hash_ok and has_changes) else 0,  # higher conf first
            dec_score,                                     # decided items next
            item.get("title", ""),                         # alphabetical tiebreaker
        )

    return sorted(items, key=_sort_key)


def _render_recommendation_queue(
    items: list[dict[str, Any]],
    decisions: dict[str, dict[str, Any]],
    selected_idx: int,
) -> Panel:
    """Render the recommendation queue as a numbered table."""
    if not items:
        return Panel("[dim]No items.[/dim]", border_style="dim", padding=(1, 2))

    table = Table(
        box=box.SIMPLE,
        show_header=True,
        header_style="bold cyan",
        expand=True,
    )
    table.add_column("#", width=4, no_wrap=True)
    table.add_column("Status", width=12, no_wrap=True)
    table.add_column("Card Title", width=32, overflow="fold")
    table.add_column("Decision", width=14, no_wrap=True)
    table.add_column("Lead", width=12, overflow="fold")
    table.add_column("Conf.", width=8, no_wrap=True)

    for i, item in enumerate(items):
        aid = item.get("applyId", "")
        badge = _get_item_badge(item)
        dec_status, _ = _get_decision_status(aid, decisions)

        # Decision label
        if dec_status == "approved_for_apply":
            dec_label = "[green]APPROVED[/green]"
        elif dec_status == "skipped":
            dec_label = "[yellow]SKIPPED[/yellow]"
        elif dec_status == "needs_edit":
            dec_label = "[magenta]NEEDS EDIT[/magenta]"
        else:
            dec_label = "[dim]pending[/dim]"

        # Lead change summary
        lead = _normalise_proposed(item.get("approvedLead"))
        lead_display = f"-> {lead}" if lead else "(no change)"

        conf = item.get("confidence", 0) or 0
        conf_display = f"{conf:.0%}" if conf > 0 else "-"

        row_style = "reverse" if i == selected_idx else ""
        title = _trunc(item.get("title", ""), 30)

        table.add_row(
            str(i + 1),
            badge,
            f"[{row_style}]{title}[/{row_style}]" if row_style else title,
            dec_label,
            lead_display,
            conf_display,
        )

    return Panel(table, border_style="blue", padding=(1, 2), title="[bold]Recommendation Queue[/bold]")


# ---------------------------------------------------------------------------
# Main TUI Loop
# ---------------------------------------------------------------------------

def run_apply_review_tui(settings: Any, show_filtered: bool = False) -> None:
    """Run the interactive Rich-based TUI for apply plan review.

    Displays a sorted recommendation queue + detail panel for selected item.
    Actions: n/p navigate, a=approve, s=skip, e=needs-edit, r=reason,
             v=full view, x=export, q=quit.
    Read-only: decisions recorded, Kanban not updated.
    """
    apply_root = settings.apply_path()
    plan = load_apply_plan(apply_root)

    if not plan:
        console.print("[yellow]No apply plan found. Run 'build-apply-plan' first.[/yellow]")
        return

    items = plan.get("planItems", [])
    if not items:
        console.print("[yellow]Apply plan is empty.[/yellow]")
        return

    # Filter out smoke/test/demo items
    ignore_smoke = getattr(settings, "ignore_smoke_test_drafts", True)
    if ignore_smoke:
        real_items = [i for i in items if not is_smoke_item(i)]
        smoke_items = [i for i in items if is_smoke_item(i)]
        smoke_count = len(smoke_items)

        if not real_items:
            if show_filtered:
                _show_filtered_only_screen(
                    items, smoke_items,
                    load_apply_decisions(apply_root),
                    plan, apply_root, settings,
                )
                return
            console.clear()
            console.print()
            console.print(Panel(
                "[bold yellow]No Real Recommendations to Review[/bold yellow]\n\n"
                f"All {len(items)} item(s) in the apply plan are smoke/test/demo data.\n\n"
                "To view filtered diagnostic details, run:\n"
                "  [bold]review-apply-tui --show-filtered[/bold]\n\n"
                "Suggestions:\n"
                "  - Run [bold]coach-status[/bold] to check pipeline state\n"
                "  - Run [bold]build-apply-plan[/bold] from approved non-smoke drafts\n"
                "  - Use [bold]reset-apply-workspace[/bold] to archive test data",
                border_style="yellow", padding=(2, 3),
            ))
            console.print()
            return

        items = real_items
    else:
        smoke_count = 0

    # If --show-filtered, append smoke items to the end for diagnostic view
    all_items = list(items)
    all_items.extend(smoke_items if ignore_smoke else [])

    # Load decisions
    decisions = load_apply_decisions(apply_root)

    # Build card cache
    kanban_root = settings.kanban_local_path()
    card_cache: dict[str, dict[str, Any] | None] = {}

    # Sort items
    sorted_items = _sort_review_items(all_items, decisions)

    idx = 0
    total = len(sorted_items)

    while 0 <= idx < total:
        item = sorted_items[idx]
        apply_id = item.get("applyId", "")
        project_id = item.get("projectId", "")

        # Get current card state (cached)
        if project_id not in card_cache:
            card_cache[project_id] = _load_current_card(kanban_root, project_id)
        current_card = card_cache[project_id]

        # Refresh decisions
        decisions = load_apply_decisions(apply_root)
        dec_status, dec_record = _get_decision_status(apply_id, decisions)
        is_smoke = is_smoke_item(item)

        # Build counts
        real_total = len([i for i in sorted_items if not is_smoke_item(i)])
        changed_count = sum(1 for i in sorted_items if not is_smoke_item(i) and _get_item_badge(i) != "[bold dim]UNCHANGED[/bold dim]")
        unchanged_count = sum(1 for i in sorted_items if not is_smoke_item(i) and _get_item_badge(i) == "[bold dim]UNCHANGED[/bold dim]")
        approved_count = sum(1 for i in sorted_items if _get_decision_status(i.get("applyId", ""), decisions)[0] == "approved_for_apply")
        skipped_count = sum(1 for i in sorted_items if _get_decision_status(i.get("applyId", ""), decisions)[0] == "skipped")

        # Render
        console.clear()

        # Header
        mode_str = "Read-only review"
        if show_filtered:
            mode_str += " (--show-filtered)"

        header_panel = Panel(
            f"[bold cyan]SAMI Kanban Coach[/bold cyan]  |  [bold]{mode_str}[/bold]\n"
            f"Recommendations: {real_total} real  |  "
            f"[bold green]{changed_count}[/bold green] update  |  "
            f"[dim]{unchanged_count}[/dim] unchanged  |  "
            f"[green]{approved_count}[/green] approved  |  "
            f"[yellow]{skipped_count}[/yellow] skipped"
            + (f"  |  [yellow]{len(smoke_items)} filtered (diagnostic)[/yellow]" if show_filtered and smoke_items else "")
            + "\n[dim]No Kanban writes performed from this screen.[/dim]",
            border_style="cyan", padding=(1, 2),
        )
        console.print(header_panel)
        console.print()

        # Queue table
        queue_panel = _render_recommendation_queue(sorted_items, decisions, idx)
        console.print(queue_panel)
        console.print()

        # Detail panel
        if is_smoke and show_filtered:
            # Show filtered diagnostic detail
            reasons = _get_filter_reasons(item, dec_record)
            detail_text = f"[bold]Filtered (diagnostic only):[/bold] {item.get('title', '')}\n\n"
            detail_text += "[bold]Filter Reason(s):[/bold]\n"
            for r_text in reasons:
                detail_text += f"  - {r_text}\n"
            detail_text += "\n[bold]Proposed Changes:[/bold]\n"
            detail_text += f"  Status:        {item.get('approvedStatus', '(no change)')}\n"
            detail_text += f"  Risk:          {item.get('approvedRisk', '(no change)')}\n"
            detail_text += f"  Current State: {item.get('approvedCurrentState', '(no change)')}\n"
            detail_text += f"  Next Action:   {item.get('approvedNextAction', '(no change)')}\n"
            console.print(Panel(detail_text, border_style="yellow", padding=(1, 2),
                                title="[bold yellow]Filtered Item (Read-Only)[/bold yellow]"))
        else:
            # Normal card detail
            console.print(_build_item_detail_panel(item, current_card, dec_status, dec_record))

        # Footer
        console.print(_build_footer_panel())

        # Prompt
        cmd = Prompt.ask("Action", default="n").strip().lower()

        if cmd in ("n", "down"):
            if idx < total - 1:
                idx += 1
            else:
                console.print("[yellow]Already at last item.[/yellow]")
                Prompt.ask("Press Enter to continue", default="")

        elif cmd in ("p", "up"):
            if idx > 0:
                idx -= 1
            else:
                console.print("[yellow]Already at first item.[/yellow]")
                Prompt.ask("Press Enter to continue", default="")

        elif cmd in ("a", "approve"):
            if is_smoke:
                console.print("[red]Cannot approve filtered smoke/test/demo items.[/red]")
                Prompt.ask("Press Enter to continue", default="")
                continue
            if not item.get("readyToApply"):
                console.print("[red]Cannot approve: item is not ready to apply (hash conflict or blocked).[/red]")
                console.print(f"[red]Reason: {item.get('skipReason', 'Unknown')}[/red]")
                Prompt.ask("Press Enter to continue", default="")
                continue
            if dec_status == "approved_for_apply":
                console.print("[yellow]Already approved. No change made.[/yellow]")
                Prompt.ask("Press Enter to continue", default="")
                continue
            reason = Prompt.ask("Approval note (optional)", default="Approved by operator via TUI")
            _save_tui_decision(apply_root, item, "approved_for_apply", reason)
            console.print("[green]Decision saved: approved for apply[/green]")
            Prompt.ask("Press Enter to continue", default="")

        elif cmd in ("s", "skip"):
            if is_smoke:
                console.print("[red]Cannot skip filtered smoke/test/demo items.[/red]")
                Prompt.ask("Press Enter to continue", default="")
                continue
            if dec_status == "skipped":
                console.print("[yellow]Already skipped. No change made.[/yellow]")
                Prompt.ask("Press Enter to continue", default="")
                continue
            reason = Prompt.ask("Skip reason", default="Skipped by operator")
            _save_tui_decision(apply_root, item, "skipped", reason)
            console.print("[green]Decision saved: skipped[/green]")
            Prompt.ask("Press Enter to continue", default="")

        elif cmd in ("e", "needs_edit"):
            if is_smoke:
                console.print("[red]Cannot edit filtered smoke/test/demo items.[/red]")
                Prompt.ask("Press Enter to continue", default="")
                continue
            if dec_status == "needs_edit":
                console.print("[yellow]Already marked as needs edit. No change made.[/yellow]")
                Prompt.ask("Press Enter to continue", default="")
                continue
            reason = Prompt.ask("What needs editing?", default="Requires manual review")
            _save_tui_decision(apply_root, item, "needs_edit", reason)
            console.print("[green]Decision saved: needs edit[/green]")
            Prompt.ask("Press Enter to continue", default="")

        elif cmd in ("r", "reason"):
            if is_smoke:
                console.print("[red]Cannot add reason to filtered items.[/red]")
                Prompt.ask("Press Enter to continue", default="")
                continue
            if dec_status == "pending":
                console.print("[yellow]No decision yet. Use 'a', 's', or 'e' first.[/yellow]")
                Prompt.ask("Press Enter to continue", default="")
                continue
            new_reason = Prompt.ask("Update note/reason", default=dec_record.get("reason", "") if dec_record else "")
            _save_tui_decision(apply_root, item, dec_status, new_reason)
            console.print("[green]Reason saved[/green]")
            Prompt.ask("Press Enter to continue", default="")

        elif cmd in ("v", "view", "view-full"):
            console.clear()
            console.print(f"[bold cyan]Full View: {item.get('title', '')}[/bold cyan]")
            console.print()
            console.print("[bold]Card Details:[/bold]")
            console.print(f"  Apply ID:      {apply_id}")
            console.print(f"  Draft ID:      {item.get('draftId', '')}")
            console.print(f"  Project ID:    {project_id}")
            console.print(f"  Source Type:   {item.get('sourceType', '')}")
            console.print(f"  Hash Status:   {item.get('hashStatus', '')}")
            console.print(f"  Ready:         {item.get('readyToApply', False)}")
            console.print(f"  Skip Reason:   {item.get('skipReason', '(none)')}")
            console.print(f"  Confidence:    {item.get('confidence', 0):.0%}")
            console.print()
            if is_smoke:
                reasons = _get_filter_reasons(item, dec_record)
                console.print("[bold]Filter Reason(s):[/bold]")
                for r_text in reasons:
                    console.print(f"  - {r_text}")
                console.print()
            if current_card:
                console.print("[bold]Current Card State:[/bold]")
                console.print(f"  Status:       {current_card.get('status', '')}")
                console.print(f"  Risk:         {current_card.get('riskColour', '')}")
                console.print(f"  Lead:         {current_card.get('projectLead', '')}")
                console.print(f"  Owner:        {current_card.get('owner', '')}")
                console.print(f"  Context:      {(current_card.get('context', '') or '')}")
                console.print(f"  Next Action:  {(current_card.get('nextAction', '') or '')}")
                console.print(f"  Review Date:  {current_card.get('reviewDate', '')}")
                console.print(f"  Notes:        {(current_card.get('notes', '') or '')}")
            else:
                console.print("[yellow]Could not load current card state.[/yellow]")
            console.print()
            console.print("[bold]Proposed Values:[/bold]")
            for label, _, prop_field in _FIELD_SPECS:
                if prop_field:
                    pv = _normalise_proposed(item.get(prop_field))
                    console.print(f"  {label}: {'(no change)' if pv is None else pv}")
            console.print()
            evidence = item.get("evidence", []) or []
            if evidence:
                console.print(f"[bold]Evidence ({len(evidence)} items):[/bold]")
                for ev in evidence[:5]:
                    console.print(f"  - {ev.get('subject','')[:60]} | {ev.get('from_','')[:20]} | {ev.get('receivedAt','')[:16]}")
            else:
                console.print("[dim]No evidence items.[/dim]")
            console.print()
            console.print(f"Source email keys: {item.get('sourceEmailKeys', [])}")
            console.print(f"Source card hash:  {item.get('sourceCardHash', '')[:16]}...")
            console.print(f"Live card hash:    {item.get('currentLiveCardHash', '')[:16]}...")
            console.print()
            Prompt.ask("Press Enter to return", default="")

        elif cmd in ("m", "summary"):
            console.clear()
            _build_summary(sorted_items, decisions)
            Prompt.ask("Press Enter to return to review", default="")

        elif cmd in ("x", "export"):
            console.clear()
            console.print("[bold cyan]Export Review Report[/bold cyan]")
            console.print()
            default_name = f"apply_review_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
            default_path = apply_root / "data" / default_name
            out = Prompt.ask(
                "Output path (Enter for default)",
                default=str(default_path),
            ).strip()
            if not out:
                out = str(default_path)
            _export_markdown_report(plan, sorted_items, decisions, apply_root, out)
            console.print()
            Prompt.ask("Press Enter to return to review", default="")

        elif cmd in ("q", "quit"):
            console.print("[yellow]Exiting review TUI.[/yellow]")
            break

        else:
            console.print(f"[red]Unknown command: '{cmd}'[/red]")
            console.print(f"Available: {CONTROLS_HELP}")
            Prompt.ask("Press Enter to continue", default="")

    # Final summary on exit
    console.clear()
    console.print("[bold cyan]Review session complete.[/bold cyan]")
    _build_summary(sorted_items, load_apply_decisions(apply_root))


# ---------------------------------------------------------------------------
# Non-interactive helpers (CLI-driven)
# ---------------------------------------------------------------------------

def export_apply_report(
    settings: Any,
    output_path: str | Path | None = None,
) -> str:
    """Export a Markdown review report from the CLI (non-interactive)."""
    apply_root = settings.apply_path()
    plan = load_apply_plan(apply_root)
    if not plan:
        raise ValueError("No apply plan found. Run 'build-apply-plan' first.")
    items = plan.get("planItems", [])
    decisions = load_apply_decisions(apply_root)
    return _export_markdown_report(plan, items, decisions, apply_root, output_path)


def show_apply_decisions_cli(settings: Any) -> None:
    """Show all current apply decisions as a table."""
    from rich.table import Table

    apply_root = settings.apply_path()
    decisions = load_apply_decisions(apply_root)
    plan = load_apply_plan(apply_root)

    if not decisions:
        console.print("[yellow]No apply decisions found.[/yellow]")
        return

    title_map: dict[str, str] = {}
    if plan:
        for item in plan.get("planItems", []):
            title_map[item.get("applyId", "")] = item.get("title", "")

    table = Table(
        title=f"Apply Decisions ({len(decisions)} total, latest per item)",
        box=box.ROUNDED,
        title_style="bold",
    )
    table.add_column("Apply ID", style="cyan", width=20)
    table.add_column("Card Title", width=40)
    table.add_column("Decision", style="bold", width=18)
    table.add_column("Reason", width=40)
    table.add_column("Timestamp", width=22)

    for aid, dec in sorted(decisions.items(), key=lambda x: x[1].get("timestamp", "")):
        dec_label = dec.get("decision", "").capitalize().replace("_", " ")
        color = DECISION_COLORS.get(dec.get("decision", ""), "white")
        table.add_row(
            aid[:18],
            _trunc(title_map.get(aid, ""), 38),
            f"[{color}]{dec_label}[/{color}]",
            _trunc(dec.get("reason", ""), 38),
            str(dec.get("timestamp", ""))[:19],
        )

    console.print()
    console.print(table)
    console.print()


def show_apply_audit_cli(settings: Any, show_history: bool = False) -> None:
    """Show full append-only audit trail."""
    from rich.table import Table

    apply_root = settings.apply_path()
    path = apply_root / "data" / DECISIONS_FILE
    if not path.exists():
        console.print("[yellow]No apply decision records found.[/yellow]")
        return

    plan = load_apply_plan(apply_root)
    title_map: dict[str, str] = {}
    if plan:
        for item in plan.get("planItems", []):
            title_map[item.get("applyId", "")] = item.get("title", "")

    records: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    if not records:
        console.print("[yellow]No records found in decisions file.[/yellow]")
        return

    table = Table(
        title=f"Apply Decision Audit -- {len(records)} total records",
        box=box.ROUNDED,
        title_style="bold",
    )
    table.add_column("Apply ID", style="cyan", width=20)
    table.add_column("Card Title", width=30)
    table.add_column("Decision", style="bold", width=18)
    table.add_column("Reason", width=32)
    table.add_column("Operator", width=14)
    table.add_column("Timestamp", width=22)

    for rec in records:
        aid = rec.get("applyId", "")
        dec_label = rec.get("decision", "").capitalize().replace("_", " ")
        color = DECISION_COLORS.get(rec.get("decision", ""), "white")
        table.add_row(
            aid[:18],
            _trunc(title_map.get(aid, ""), 28),
            f"[{color}]{dec_label}[/{color}]",
            _trunc(rec.get("reason", ""), 30),
            rec.get("approvedBy", "Brian"),
            str(rec.get("timestamp", ""))[:19],
        )

    console.print()
    console.print(table)
    console.print()


def reset_apply_decisions_cli(settings: Any) -> None:
    """Reset (clear) all apply decisions -- requires confirmation."""
    from rich.prompt import Confirm

    apply_root = settings.apply_path()
    path = apply_root / "data" / DECISIONS_FILE

    if not path.exists():
        console.print("[yellow]No apply decisions file found. Nothing to reset.[/yellow]")
        return

    count = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                count += 1

    console.print(f"[bold red]WARNING: This will delete {count} decision records.[/bold red]")
    console.print(f"  File: {path}")
    console.print("[red]This action cannot be undone.[/red]")
    console.print()

    confirmed = Confirm.ask("Are you sure you want to reset all apply decisions?")
    if not confirmed:
        console.print("[yellow]Reset cancelled.[/yellow]")
        return

    confirm_str = input("Type 'RESET' to confirm: ")
    if confirm_str != "RESET":
        console.print("[yellow]Reset cancelled -- confirmation mismatch.[/yellow]")
        return

    path.unlink()
    console.print(f"[green]Apply decisions reset:[/green] {path} removed ({count} records).")
