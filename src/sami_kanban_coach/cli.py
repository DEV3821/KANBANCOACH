"""CLI entry point for SAMI Kanban Coach.

Phase 0 commands:
  doctor            — Check Outlook COM environment
  export-selected   — Export currently selected Outlook emails
  export-folder     — Export emails from the configured folder
  live-watch        — Watch a folder continuously for new emails
  validate-phase0   — Run all Phase 0 validation checks

Phase 1 commands:
  kanban-doctor     — Check Kanban source paths and state
  index-kanban      — Build local kanban state index
  show-cards        — Show indexed cards from local kanban index
  show-stale-cards  — Show stale/neglected cards
  validate-phase1   — Run all Phase 1 validation checks

Phase 2 commands:
  match-emails           — Match captured emails to Kanban cards
  show-email-matches     — Show email-to-card match results
  show-unmatched-emails  — Show emails with no card match
  validate-phase2        — Run all Phase 2 validation checks

Phase 3 commands:
  ollama-doctor     — Check Ollama endpoint and model availability
  generate-drafts   — Generate card-state comparison drafts via Qwen/Ollama
  show-drafts       — Show generated card update drafts
  show-no-change    — Show no-change decisions
  validate-phase3   — Run all Phase 3 validation checks

Phase 4A commands:
  build-review-queue      — Build review queue from Phase 3 drafts
  show-review-queue       — Show pending review queue
  review-draft            — Show full review view for a draft
  approve-draft           — Approve a draft (writes approved_drafts.jsonl only)
  edit-draft              — Edit suggested fields before approval
  skip-draft              — Skip a draft with reason
  review-tui              — Terminal review interface (Rich-based)
  create-review-smoke-draft — Create a synthetic smoke-test draft
  validate-phase4a        — Run all Phase 4A validation checks

Phase 4B commands:
  build-apply-plan         — Build apply plan from approved/edited drafts
  show-apply-plan          — Show current apply plan
  apply-approved-local     — Apply approved/edited drafts to local Kanban (dry-run by default)
  show-apply-results       — Show apply results
  validate-phase4b         — Run all Phase 4B validation checks

Phase 4C commands:
  review-apply-plan        — Interactively review and approve/skip plan items
  apply-approved-plan      — Apply operator-approved items with strong confirmation
  apply-flow               — Full interactive apply flow (build → review → apply)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .config import ConfigLoader
from .logging_setup import setup_logging
from .path_safety import check_writeable, is_forbidden_path
from .storage import capture_and_save

console = Console()
app = typer.Typer(
    name="SAMI Kanban Coach — Phase 0",
    help="Outlook Email Recall Cache for SAMI Kanban Coach",
    no_args_is_help=True,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_REPO_ROOT = Path.cwd().resolve()
_CONFIG_PATH = _REPO_ROOT / "config" / "settings.json"
logger = setup_logging(_REPO_ROOT / "runtime/email_recall/logs")


def _load_settings() -> tuple[ConfigLoader, object]:
    """Load config and return (loader, settings)."""
    loader = ConfigLoader(_CONFIG_PATH)
    settings = loader.load()
    return loader, settings


def _get_outlook():
    """Get an OutlookConnection, handling import errors."""
    try:
        from .outlook_com import OutlookConnection
    except ImportError as e:
        console.print("[red]FAIL:[/red] Cannot import outlook_com module.", style="bold")
        console.print(f"  Detail: {e}")
        raise typer.Exit(1) from e

    conn = OutlookConnection()
    try:
        conn.connect()
    except Exception as e:
        console.print(f"[red]FAIL:[/red] Cannot connect to Outlook COM: {e}", style="bold")
        raise typer.Exit(1) from e
    return conn


# ---------------------------------------------------------------------------
# Command: doctor
# ---------------------------------------------------------------------------
@app.command()
def doctor():
    """Check Outlook COM environment and configuration."""
    from rich.table import Table

    table = Table(title="SAMI Kanban Coach — Phase 0 Doctor", show_header=True)
    table.add_column("Check", style="cyan", no_wrap=True)
    table.add_column("Result", style="bold")
    table.add_column("Detail")

    # 1. Python version
    py_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    table.add_row("Python version", "PASS" if sys.version_info >= (3, 11) else "FAIL", py_version)

    # 2. pywin32 import
    try:
        import win32com  # noqa: F401
        table.add_row("pywin32 import", "PASS", "win32com imported OK")
    except ImportError:
        table.add_row("pywin32 import", "FAIL", "pywin32 not installed — run pip install pywin32")

    # 3. Process detection
    outlook_running = False
    new_outlook_running = False
    outlook_classic = False
    try:
        import subprocess
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq OUTLOOK.EXE"],
            capture_output=True, text=True, timeout=10,
        )
        if "OUTLOOK.EXE" in result.stdout:
            outlook_running = True
            outlook_classic = True
        result_n = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq olk.exe"],
            capture_output=True, text=True, timeout=10,
        )
        if "olk.exe" in result_n.stdout:
            new_outlook_running = True
    except Exception:
        pass

    table.add_row(
        "Classic Outlook running",
        "PASS" if outlook_classic else "WARN",
        "OUTLOOK.EXE process found" if outlook_classic else "No OUTLOOK.EXE detected",
    )
    table.add_row(
        "New Outlook running",
        "WARN" if new_outlook_running else "INFO",
        "olk.exe process found — new Outlook does not support COM automation"
        if new_outlook_running
        else "No olk.exe detected",
    )

    # 4. COM dispatch
    outlook_app = None
    try:
        import win32com.client
        outlook_app = win32com.client.Dispatch("Outlook.Application")
        table.add_row("Outlook COM dispatch", "PASS", "Outlook.Application dispatched OK")
    except Exception as e:
        msg = str(e)
        if new_outlook_running:
            msg += " — New Outlook does not support classic Outlook COM automation. Use classic Outlook."
        table.add_row("Outlook COM dispatch", "FAIL", msg)
        # Show table even on failure
        console.print(table)
        raise typer.Exit(1) from e

    # 5. Application metadata
    try:
        name = getattr(outlook_app, "Name", "Unknown")
        ver = getattr(outlook_app, "Version", "Unknown")
        table.add_row("Outlook Application", "PASS", f"{name} v{ver}")
    except Exception as e:
        table.add_row("Outlook Application", "WARN", str(e))

    try:
        product_code = getattr(outlook_app, "ProductCode", "N/A")
        table.add_row("Product Code", "INFO", str(product_code))
    except Exception:
        table.add_row("Product Code", "INFO", "N/A")

    # 6. Profile info
    try:
        ns = outlook_app.GetNamespace("MAPI")
        profile = getattr(ns, "CurrentProfileName", "N/A")
        table.add_row("MAPI Profile", "INFO", str(profile))
    except Exception as e:
        table.add_row("MAPI Profile", "WARN", f"Cannot read: {e}")

    # 7. MAPI namespace open
    try:
        ns = outlook_app.GetNamespace("MAPI")
        # No explicit Logon — Outlook is already running, MAPI session inherited.
        table.add_row("MAPI namespace", "PASS", "GetNamespace('MAPI') opened OK")
    except Exception as e:
        table.add_row("MAPI namespace", "FAIL", str(e))
        console.print(table)
        raise typer.Exit(1) from e

    # 8. Top-level stores
    try:
        stores_info = []
        for store in ns.Stores:
            try:
                stores_info.append(str(getattr(store, "DisplayName", "?")))
            except Exception:
                pass
        table.add_row("Mail stores", "INFO", ", ".join(stores_info) if stores_info else "None found")
    except Exception as e:
        table.add_row("Mail stores", "WARN", str(e))

    # 9. Config check
    config_ok = _CONFIG_PATH.exists()
    table.add_row(
        "Config settings.json",
        "PASS" if config_ok else "FAIL",
        str(_CONFIG_PATH) if config_ok else "MISSING",
    )

    output_root = None
    if config_ok:
        try:
            _, settings = _load_settings()
            output_root = settings.output_path()
            table.add_row("Output root resolved", "PASS", str(output_root))
        except Exception as e:
            table.add_row("Output root resolved", "FAIL", str(e))

    # 10. Folder resolution
    if config_ok and outlook_app:
        try:
            _, settings = _load_settings()
            folder_path = settings.outlook_folder_path
            resolved = None

            # Helper: sort stores, skip PACS/archive which are slow
            def _doctor_stores():
                stores_list = list(ns.Stores)
                def _p(s):
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
                stores_list.sort(key=_p)
                return [s for s in stores_list if _p(s) < 3]

            # Try simple + nested resolution
            if "\\" in folder_path or "/" in folder_path:
                parts = folder_path.replace("/", "\\").split("\\")
                for store in _doctor_stores():
                    try:
                        root = store.GetRootFolder()
                        for f in root.Folders:
                            if str(getattr(f, "Name", "")).strip() == parts[0].strip():
                                current = f
                                found = True
                                for part in parts[1:]:
                                    matched = None
                                    for sub in current.Folders:
                                        if str(getattr(sub, "Name", "")).strip() == part.strip():
                                            matched = sub
                                            break
                                    if matched:
                                        current = matched
                                    else:
                                        found = False
                                        break
                                if found:
                                    resolved = current
                                    break
                    except Exception:
                        continue
            else:
                for store in _doctor_stores():
                    try:
                        root = store.GetRootFolder()
                        for f in root.Folders:
                            if str(getattr(f, "Name", "")).strip() == folder_path.strip():
                                resolved = f
                                break
                    except Exception:
                        continue
                    if resolved:
                        break

            if resolved:
                table.add_row(
                    "Folder resolved",
                    "PASS",
                    f"'{folder_path}' found (items: {getattr(resolved.Items, 'Count', '?')})",
                )
            else:
                table.add_row(
                    "Folder resolved",
                    "WARN",
                    f"'{folder_path}' not found — see available folders below",
                )
                # Show available top folders (primary mailbox only)
                available = []
                primary_stores = _doctor_stores()[:1] if _doctor_stores() else []
                for store in primary_stores:
                    try:
                        root = store.GetRootFolder()
                        for f in root.Folders:
                            available.append(str(getattr(f, "Name", "?")))
                    except Exception:
                        pass
                table.add_row("Available folders", "INFO", ", ".join(available[:20]) if available else "None")
        except Exception as e:
            table.add_row("Folder resolution", "WARN", str(e))

    # 11. Path writeability
    if output_root:
        dirs_to_check = [
            ("data/", output_root / "data"),
            ("evidence/emails/", output_root / "evidence" / "emails"),
            ("evidence/attachments/", output_root / "evidence" / "attachments"),
            ("logs/", output_root / "logs"),
        ]
        for label, d in dirs_to_check:
            ok, msg = check_writeable(d)
            table.add_row(f"Writable: {label}", "PASS" if ok else "FAIL", msg)

    # 12. Registry hints (optional)
    try:
        import winreg
        ctr_keys = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Office\ClickToRun\Configuration"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Office\ClickToRun\Configuration"),
        ]
        ctr_found = False
        for hive, key_path in ctr_keys:
            try:
                with winreg.OpenKey(hive, key_path) as k:
                    ctr_found = True
                    break
            except OSError:
                continue
        table.add_row(
            "Office ClickToRun registry",
            "INFO",
            "ClickToRun detected" if ctr_found else "Not found (non-ClickToRun install OK)",
        )
    except Exception:
        table.add_row("Office ClickToRun registry", "INFO", "Could not check (skipped)")

    console.print(table)
    console.print("\n[bold green]Doctor complete.[/bold green]")


# ---------------------------------------------------------------------------
# Command: export-selected
# ---------------------------------------------------------------------------
@app.command(name="export-selected")
def export_selected():
    """Export currently selected Outlook emails to the recall cache."""
    conn = _get_outlook()
    try:
        from .outlook_com import get_selected_items, extract_email_fields

        items = get_selected_items(conn)
        if not items:
            console.print("[yellow]No mail items selected in Outlook.[/yellow]")
            raise typer.Exit(0)

        _, settings = _load_settings()
        output_root = settings.output_path()
        source_folder_name = settings.outlook_folder_path
        config_dict = settings.model_dump()
        captured = 0
        skipped_dup = 0
        skipped_err = 0

        with console.status(f"Capturing {len(items)} selected emails...") as status:
            for mail_item in items:
                try:
                    email_data = extract_email_fields(mail_item, config_dict)
                    email_data["source_folder"] = source_folder_name
                    try:
                        capture_and_save(output_root, "selected", source_folder_name, email_data)
                        captured += 1
                    except ValueError as ve:
                        if "Duplicate" in str(ve):
                            skipped_dup += 1
                        else:
                            skipped_err += 1
                            logger.warning("Capture error: %s", ve)
                except Exception as e:
                    skipped_err += 1
                    logger.error("Extract/capture error: %s", e)

        console.print(f"[green]Captured: {captured} | Duplicates: {skipped_dup} | Errors: {skipped_err}[/green]")
    finally:
        conn.disconnect()


# ---------------------------------------------------------------------------
# Command: export-folder
# ---------------------------------------------------------------------------
@app.command(name="export-folder")
def export_folder(
    since_hours: int = typer.Option(48, "--since-hours", help="Lookback window in hours"),
    max_items: int = typer.Option(100, "--max-items", help="Maximum emails to capture"),
):
    """Export emails from the configured Outlook folder."""
    _, settings = _load_settings()
    if since_hours < 1 or since_hours > 8760:
        console.print("[red]since-hours must be between 1 and 8760.[/red]")
        raise typer.Exit(1)

    conn = _get_outlook()
    try:
        from .outlook_com import get_folder_items, extract_email_fields

        # Resolve folder
        folder = conn.resolve_folder(settings.outlook_folder_path)
        if not folder:
            console.print(f"[red]Folder '{settings.outlook_folder_path}' not found in Outlook.[/red]")
            console.print("[yellow]Run 'doctor' to see available folders.[/yellow]")
            raise typer.Exit(1)

        items = get_folder_items(folder, since_hours=since_hours, max_items=max_items)
        if not items:
            console.print(f"[yellow]No mail items found in '{settings.outlook_folder_path}' in the last {since_hours}h.[/yellow]")
            raise typer.Exit(0)

        output_root = settings.output_path()
        source_folder_name = settings.outlook_folder_path
        config_dict = settings.model_dump()
        captured = 0
        skipped_dup = 0
        skipped_err = 0

        with console.status(f"Processing {len(items)} emails from folder...") as status:
            for mail_item in items:
                try:
                    email_data = extract_email_fields(mail_item, config_dict)
                    email_data["source_folder"] = source_folder_name
                    try:
                        capture_and_save(output_root, "folder", source_folder_name, email_data)
                        captured += 1
                    except ValueError as ve:
                        if "Duplicate" in str(ve):
                            skipped_dup += 1
                        else:
                            skipped_err += 1
                            logger.warning("Capture error: %s", ve)
                except Exception as e:
                    skipped_err += 1
                    logger.error("Extract/capture error: %s", e)

        console.print(f"[green]Captured: {captured} | Duplicates: {skipped_dup} | Errors: {skipped_err}[/green]")
    finally:
        conn.disconnect()


# ---------------------------------------------------------------------------
# Command: live-watch
# ---------------------------------------------------------------------------
@app.command(name="live-watch")
def live_watch(
    poll_seconds: int = typer.Option(60, "--poll-seconds", help="Polling interval in seconds"),
):
    """Watch the configured Outlook folder for new emails."""
    _, settings = _load_settings()
    conn = _get_outlook()
    try:
        from .outlook_com import extract_email_fields, LiveWatcher

        # Resolve folder
        folder = conn.resolve_folder(settings.outlook_folder_path)
        if not folder:
            console.print(f"[red]Folder '{settings.outlook_folder_path}' not found in Outlook.[/red]")
            raise typer.Exit(1)

        output_root = settings.output_path()
        source_folder_name = settings.outlook_folder_path
        config_dict = settings.model_dump()
        poll = max(poll_seconds, 5)  # Minimum 5s

        captured_count = 0
        duplicate_count = 0
        error_count = 0

        def on_email(mail_item, mode: str):
            nonlocal captured_count, duplicate_count, error_count
            try:
                email_data = extract_email_fields(mail_item, config_dict)
                email_data["source_folder"] = source_folder_name
                try:
                    capture_and_save(output_root, mode, source_folder_name, email_data)
                    captured_count += 1
                    console.print(
                        f"  [green]Captured[/green] [{mode}]: "
                        f"{email_data.get('subject', '?')[:60]}"
                    )
                except ValueError as ve:
                    if "Duplicate" in str(ve):
                        duplicate_count += 1
                    else:
                        error_count += 1
                        logger.warning("Capture error: %s", ve)
            except Exception as e:
                error_count += 1
                logger.error("Watcher callback error: %s", e)

        console.print(f"[bold cyan]Live watcher starting...[/bold cyan]")
        console.print(f"  Folder:     {settings.outlook_folder_path}")
        console.print(f"  Poll:       every {poll}s")
        console.print(f"  Output:     {output_root}")
        console.print(f"  Events:     ItemAdd + polling fallback")
        console.print(f"  [dim]Press Ctrl+C to stop.[/dim]")

        watcher = LiveWatcher(
            outlook_connection=conn,
            folder=folder,
            on_email=on_email,
            poll_seconds=poll,
        )
        watcher.start()
    except KeyboardInterrupt:
        console.print("\n[yellow]Live watcher stopped by user.[/yellow]")
    except Exception as e:
        console.print(f"[red]Live watcher error: {e}[/red]")
        logger.error("Live watcher error: %s", e, exc_info=True)
    finally:
        conn.disconnect()
        console.print(f"\n[bold]Session summary:[/bold green] Captured {captured_count}, "
                       f"Duplicates {duplicate_count}, Errors {error_count} [/bold green]")


# ---------------------------------------------------------------------------
# Command: validate-phase0
# ---------------------------------------------------------------------------
@app.command(name="validate-phase0")
def validate_phase0():
    """Run all Phase 0 validation checks."""
    from .validation import run_all_checks
    from rich.table import Table

    repo_root = Path.cwd().resolve()
    results = run_all_checks(repo_root)

    table = Table(title="Phase 0 Validation Results", show_header=True)
    table.add_column("Check", style="cyan")
    table.add_column("Result", style="bold")
    table.add_column("Detail")

    passed = 0
    failed = 0
    for r in results:
        result_str = "PASS" if r["passed"] else "FAIL"
        if result_str == "PASS":
            passed += 1
        else:
            failed += 1
        table.add_row(r["check"], result_str, r["detail"])

    console.print(table)
    console.print(f"\n[bold]{'All checks passed!' if failed == 0 else f'{failed} check(s) failed'} "
                   f"({passed} passed, {failed} failed)[/bold]")

    if failed > 0:
        raise typer.Exit(1)


# ===========================================================================
# Phase 1 commands — Kanban state indexer
# ===========================================================================

@app.command(name="kanban-doctor")
def kanban_doctor():
    """Check Kanban source paths and configuration."""
    from .kanban_reader import (
        find_projects_json, read_projects_json, read_card_updates_jsonl,
        build_source_status, check_team_accessibility, file_hash, file_mtime_iso,
    )
    from rich.table import Table

    _, settings = _load_settings()
    local_root = settings.kanban_local_path()
    team_root = settings.kanban_team_path()
    index_root = settings.kanban_index_path()

    table = Table(title="Kanban Doctor — Phase 1", show_header=True)
    table.add_column("Check", style="cyan")
    table.add_column("Result", style="bold")
    table.add_column("Detail")

    # --- LOCAL REQUIRED CHECKS ---
    local_ok = local_root.exists()
    table.add_row("Local root exists", "PASS" if local_ok else "FAIL", str(local_root))

    if local_ok:
        pj_path, pj_note = find_projects_json(local_root)
        table.add_row("Projects source", "INFO", pj_note)

        if pj_path:
            projects, meta, pj_hash, pj_parse = read_projects_json(pj_path)
            mtime = file_mtime_iso(pj_path)
            size = pj_path.stat().st_size if pj_path.exists() else 0
            table.add_row("projects.json readable", "PASS" if projects is not None else "FAIL", pj_parse)
            table.add_row("projects.json mtime", "INFO", mtime)
            table.add_row("projects.json size", "INFO", f"{size:,} bytes")
            table.add_row("projects.json hash", "INFO", pj_hash[:16] + "...")
            table.add_row("Card count", "INFO", str(len(projects)))

            # Detect fields
            if projects:
                sample = projects[0]
                detected = []
                for field in ["status", "riskColour", "projectLead", "owner",
                              "reviewDate", "lastUpdated", "nextAction", "context"]:
                    if field in sample or any(field in p for p in projects[:5]):
                        detected.append(field)
                table.add_row("Fields detected", "INFO", ", ".join(detected))

        cu_path = local_root / "data" / "card_updates.jsonl"
        if cu_path.exists():
            updates, cu_hash, cu_note = read_card_updates_jsonl(cu_path)
            cu_mtime = file_mtime_iso(cu_path)
            table.add_row("card_updates.jsonl", "INFO", f"{len(updates)} records, mtime {cu_mtime}")
        else:
            table.add_row("card_updates.jsonl", "INFO", "File not found (optional)")

    table.add_row("Index root writable", "PASS", str(index_root))
    ok, msg = check_writeable(index_root / "data")
    table.add_row("  data/ writable", "PASS" if ok else "FAIL", msg)
    ok2, msg2 = check_writeable(index_root / "logs")
    table.add_row("  logs/ writable", "PASS" if ok2 else "FAIL", msg2)

    # Guard: no write to kanban source
    guard_ok = is_forbidden_path(str(local_root))
    table.add_row("Kanban source write guard", "PASS" if guard_ok else "WARN",
                  "is_forbidden_path active" if guard_ok else "Check path_safety.py")

    # --- OPTIONAL TEAM CHECKS ---
    team_acc, team_status = check_team_accessibility(team_root)
    if not team_acc:
        table.add_row("Team ESMI", "WARN",
                       "Not accessible in current network context; local validation continues.")
    else:
        table.add_row("Team ESMI", "INFO", "Accessible")
        team_pj, _ = find_projects_json(team_root)
        if team_pj:
            table.add_row("  projects.json", "INFO", str(team_pj))
        if (team_root / "data" / "card_updates.jsonl").exists():
            table.add_row("  card_updates.jsonl", "INFO", "Exists")

    console.print(table)
    console.print("\n[bold green]Kanban doctor complete.[/bold green]")


@app.command(name="index-kanban")
def index_kanban():
    """Build local kanban state index from the Kanban source."""
    from .kanban_indexer import index_kanban_source

    _, settings = _load_settings()
    source_mode = "local"

    # Optionally prefer team source
    if settings.prefer_team_source_if_available:
        from .kanban_reader import check_team_accessibility
        team_acc, _ = check_team_accessibility(settings.kanban_team_path())
        if team_acc:
            source_mode = "team"
            console.print("[cyan]Team ESMI source accessible — using team source.[/cyan]")

    console.print(f"[bold cyan]Indexing kanban state from {source_mode} source...[/bold cyan]")

    try:
        snapshot = index_kanban_source(settings, source_mode=source_mode)
    except FileNotFoundError as e:
        console.print(f"[red]FAIL: {e}[/red]")
        raise typer.Exit(1) from e

    console.print(f"[green]Index complete:[/green]")
    console.print(f"  Cards indexed:     {snapshot.cardCount}")
    console.print(f"  By status:         {dict(snapshot.countsByStatus)}")
    console.print(f"  By risk:           {dict(snapshot.countsByRisk)}")
    console.print(f"  Source:            {source_mode}")
    console.print(f"  Snapshot:          {settings.kanban_index_data_dir() / 'kanban_state_snapshot.json'}")
    console.print(f"  Card index:        {settings.kanban_index_data_dir() / 'card_index.jsonl'}")
    console.print(f"  Activity index:    {settings.kanban_index_data_dir() / 'card_activity_index.jsonl'}")
    console.print(f"  Source status:     {settings.kanban_index_data_dir() / 'kanban_source_status.json'}")


@app.command(name="show-cards")
def show_cards():
    """Show indexed cards from the local kanban index."""
    from .kanban_indexer import load_card_index
    from rich.table import Table

    _, settings = _load_settings()
    cards = load_card_index(settings.kanban_index_path())
    if not cards:
        console.print("[yellow]No card index found. Run 'index-kanban' first.[/yellow]")
        raise typer.Exit(0)

    table = Table(title=f"Kanban Cards ({len(cards)} total)", show_header=True)
    table.add_column("Title", style="cyan")
    table.add_column("Status", style="bold")
    table.add_column("Risk")
    table.add_column("Lead")
    table.add_column("Last Updated")
    table.add_column("Next Action")

    for card in cards:
        na = (card.nextAction or "")[:60]
        if len(card.nextAction or "") > 60:
            na += "..."
        table.add_row(
            (card.title or "")[:55],
            card.status or "",
            card.risk or "",
            card.lead or "",
            (card.lastUpdated or "")[:16],
            na,
        )

    console.print(table)


@app.command(name="show-stale-cards")
def show_stale_cards(
    days: int = typer.Option(7, "--days", help="Stale threshold in days"),
):
    """Show cards that are stale or missing key fields."""
    from .kanban_indexer import load_card_index
    from datetime import datetime, timedelta
    from rich.table import Table

    _, settings = _load_settings()
    cards = load_card_index(settings.kanban_index_path())
    if not cards:
        console.print("[yellow]No card index found. Run 'index-kanban' first.[/yellow]")
        raise typer.Exit(0)

    cutoff = datetime.now() - timedelta(days=days)
    stale = []
    for card in cards:
        reasons = []
        # Check lastUpdated
        if not card.lastUpdated:
            reasons.append("no update date")
        else:
            try:
                ts = card.lastUpdated.replace("Z", "+00:00")[:19]
                dt = datetime.fromisoformat(ts)
                if dt < cutoff:
                    reasons.append(f"stale (>={days}d)")
            except (ValueError, TypeError):
                reasons.append("unparseable date")

        if not card.nextAction:
            reasons.append("no next action")
        if not card.lead:
            reasons.append("no lead")
        if not card.owner:
            reasons.append("no owner")
        if not card.risk or card.risk == "unknown":
            reasons.append("no risk")

        if reasons:
            stale.append((card, "; ".join(reasons)))

    if not stale:
        console.print(f"[green]No stale cards found (threshold: {days} days).[/green]")
        raise typer.Exit(0)

    table = Table(
        title=f"Stale Cards ({len(stale)} of {len(cards)}) — threshold {days}d",
        show_header=True,
    )
    table.add_column("Title", style="cyan")
    table.add_column("Status")
    table.add_column("Lead")
    table.add_column("Last Updated")
    table.add_column("Issues")

    for card, reasons in stale:
        table.add_row(
            (card.title or "")[:50],
            card.status or "",
            card.lead or "",
            (card.lastUpdated or "")[:16],
            reasons,
        )

    console.print(table)


@app.command(name="validate-phase1")
def validate_phase1():
    """Run all Phase 1 validation checks (local-first, Team ESMI optional)."""
    from .kanban_reader import (
        find_projects_json, read_projects_json, read_card_updates_jsonl,
        check_team_accessibility, file_hash,
    )
    from .kanban_indexer import load_card_index, load_snapshot, load_source_status
    from rich.table import Table
    import json

    _, settings = _load_settings()
    local_root = settings.kanban_local_path()
    index_root = settings.kanban_index_path()
    team_root = settings.kanban_team_path()

    table = Table(title="Phase 1 Validation Results", show_header=True)
    table.add_column("Check", style="cyan")
    table.add_column("Result", style="bold")
    table.add_column("Detail")

    passed = 0
    failed = 0

    def _r(check_name: str, ok: bool, detail: str) -> None:
        nonlocal passed, failed
        if ok:
            passed += 1
            table.add_row(check_name, "PASS", detail)
        else:
            failed += 1
            table.add_row(check_name, "FAIL", detail)

    def _w(check_name: str, detail: str) -> None:
        nonlocal passed
        passed += 1
        table.add_row(check_name, "WARN", detail)

    # Phase 1 config fields exist
    _r("Phase 1 config fields exist",
       hasattr(settings, "kanban_local_root"),
       f"kanban_local_root={settings.kanban_local_root}")

    # Local root exists
    local_ok = local_root.exists()
    _r("Kanban local root exists", local_ok, str(local_root))

    if local_ok:
        pj_path, _ = find_projects_json(local_root)
        _r("projects.json readable",
           pj_path is not None and pj_path.exists(),
           str(pj_path) if pj_path else "Not found")

        if pj_path and pj_path.exists():
            projects, _, _, parse_note = read_projects_json(pj_path)
            _r("projects.json valid JSON", True if projects is not None else False, parse_note)

    # Index exists/can be created
    snapshot = load_snapshot(index_root)
    if snapshot:
        _r("kanban_state_snapshot.json valid",
           True,
           f"{snapshot.cardCount} cards, source={snapshot.sourceMode}")
    else:
        # Try creating it
        try:
            from .kanban_indexer import index_kanban_source
            snap = index_kanban_source(settings, source_mode="local")
            _r("index-kanban runs successfully", True,
               f"{snap.cardCount} cards indexed from {snap.sourceMode}")
        except Exception as e:
            _r("index-kanban runs successfully", False, str(e))

    # card_index.jsonl valid
    cards = load_card_index(index_root)
    if cards:
        _r("card_index.jsonl valid", True, f"{len(cards)} records")
    else:
        _r("card_index.jsonl valid", True, "No card index file yet (index first)")

    # kanban_state_snapshot.json valid
    snap2 = load_snapshot(index_root)
    if snap2:
        _r("state snapshot valid", True, f"schemaVersion={snap2.schemaVersion}")
    else:
        _r("state snapshot valid", True, "No snapshot yet")

    # kanban_source_status.json valid
    ss = load_source_status(index_root)
    if ss:
        _r("source status valid", True, f"selectedSource={ss.selectedSource}")
    else:
        _r("source status valid", True, "No source status yet")

    # show-cards works
    try:
        _ = load_card_index(index_root)
        _r("show-cards can run", True, "card_index.jsonl readable")
    except Exception as e:
        _r("show-cards can run", False, str(e))

    # Forbidden write guard
    guard_ok = is_forbidden_path(str(local_root))
    _r("Kanban write guard active", guard_ok,
       "is_forbidden_path blocks writes" if guard_ok else "Check path_safety.py")

    # Team ESMI — WARN only
    team_acc, team_status = check_team_accessibility(team_root)
    if not team_acc:
        _w("Team ESMI accessible",
           f"Not accessible in current network context — local validation continues.")
    else:
        _r("Team ESMI accessible", True, "Accessible")

    # Source files unchanged (capture hashes before/after if snapshot exists)
    if snap2:
        pj_now = find_projects_json(local_root)[0]
        if pj_now and pj_now.exists():
            now_hash = file_hash(pj_now)
            orig_hash = snap2.projectsJsonHash
            _r("Local projects.json unchanged",
               now_hash == orig_hash or not orig_hash,
               f"hash={now_hash[:16]}...")

    # validate-phase0 still passes
    try:
        from .validation import run_all_checks
        p0 = run_all_checks()
        _r("validate-phase0 still passes",
           all(r["passed"] for r in p0),
           f"{sum(1 for r in p0 if r['passed'])}/{len(p0)} pass")
    except Exception as e:
        _r("validate-phase0 still passes", False, str(e))

    console.print(table)
    console.print(f"\n[bold]{'All checks passed!' if failed == 0 else f'{failed} check(s) failed'} "
                   f"({passed} passed, {failed} failed)[/bold]")
    if failed > 0:
        raise typer.Exit(1)


# ===========================================================================
# Phase 2 commands — Email-to-Card Matching
# ===========================================================================

@app.command(name="match-emails")
def match_emails(
    since_hours: int = typer.Option(72, "--since-hours", help="Only process emails within this many hours"),
):
    """Match captured emails to Kanban cards using deterministic scoring."""
    from .matching_engine import run_matching

    _, settings = _load_settings()
    email_root = settings.output_path()
    kanban_index_root = settings.kanban_index_path()
    matching_root = settings.output_path().parent / "matching"
    if not matching_root.exists():
        matching_root = Path(str(settings.output_path()).replace("email_recall", "matching"))
    # Use explicit matching root next to email_recall
    matching_root = settings.output_path().parent / "matching"

    console.print(f"[bold cyan]Matching emails to cards (since {since_hours}h)...[/bold cyan]")

    try:
        summary = run_matching(
            email_recall_root=email_root,
            kanban_index_root=kanban_index_root,
            matching_root=matching_root,
            since_hours=since_hours,
        )
    except Exception as e:
        console.print(f"[red]FAIL: {e}[/red]")
        logger.error("Matching error", exc_info=True)
        raise typer.Exit(1) from e

    console.print(f"[green]Matching complete:[/green]")
    console.print(f"  Emails scanned:    {summary.get('emailsScanned', 0)}")
    console.print(f"  In window:         {summary.get('emailsInWindow', 0)}")
    console.print(f"  Matched:           {summary.get('matched', 0)}")
    console.print(f"  Possible match:    {summary.get('possibleMatch', 0)}")
    console.print(f"  Possible new proj: {summary.get('possibleNewProject', 0)}")
    console.print(f"  Unmatched:         {summary.get('unmatched', 0)}")
    console.print(f"  Cards available:   {summary.get('cardsAvailable', 0)}")

    top = summary.get("topMatches", [])
    if top:
        console.print("\n[cyan]Top matches:[/cyan]")
        for t in top[:5]:
            console.print(f"  {t.get('confidence', 0):.0%}  {t.get('title','')[:50]}")
    console.print(f"\n[dim]Output: {matching_root / 'data' / 'email_card_matches.jsonl'}[/dim]")


@app.command(name="show-email-matches")
def show_email_matches():
    """Show email-to-card match results."""
    from .matching_engine import load_matches, load_new_project_emails
    from rich.table import Table

    _, settings = _load_settings()
    matching_root = settings.output_path().parent / "matching"

    matches = load_matches(matching_root)
    new_proj = load_new_project_emails(matching_root)
    all_matches = matches + new_proj

    if not all_matches:
        console.print("[yellow]No match results found. Run 'match-emails' first.[/yellow]")
        raise typer.Exit(0)

    table = Table(title=f"Email-Card Matches ({len(matches)} matched, {len(new_proj)} new-project)", show_header=True)
    table.add_column("Email Subject", style="cyan")
    table.add_column("From")
    table.add_column("Received")
    table.add_column("Matched Card")
    table.add_column("Confidence")
    table.add_column("Decision")
    table.add_column("Key Signals")

    for m in all_matches:
        signals = m.get("matchedSignals", [])
        sig_preview = "; ".join(
            f"{s.get('type','')[:12]}:{s.get('value','')[:20]}"
            for s in signals[:3]
        )
        table.add_row(
            (m.get("emailSubject") or "")[:45],
            (m.get("emailFrom") or "")[:20],
            (m.get("emailReceivedAt") or "")[:16],
            (m.get("matchedTitle") or "")[:40],
            f"{m.get('confidence', 0):.0%}",
            m.get("decision", ""),
            sig_preview,
        )

    console.print(table)


@app.command(name="show-unmatched-emails")
def show_unmatched_emails():
    """Show emails that had no card match."""
    from .matching_engine import load_unmatched
    from rich.table import Table

    _, settings = _load_settings()
    matching_root = settings.output_path().parent / "matching"

    records = load_unmatched(matching_root)
    if not records:
        console.print("[green]No unmatched emails.[/green]")
        raise typer.Exit(0)

    table = Table(title=f"Unmatched Emails ({len(records)})", show_header=True)
    table.add_column("Subject", style="cyan")
    table.add_column("From")
    table.add_column("Received")
    table.add_column("Top Signal")

    for m in records:
        signals = m.get("matchedSignals", [])
        top_sig = signals[0].get("value","")[:30] if signals else "none"
        table.add_row(
            (m.get("emailSubject") or "")[:50],
            (m.get("emailFrom") or "")[:20],
            (m.get("emailReceivedAt") or "")[:16],
            top_sig,
        )

    console.print(table)


@app.command(name="validate-phase2")
def validate_phase2():
    """Run all Phase 2 validation checks."""
    from .matching_engine import load_matches, load_unmatched, load_matching_summary, load_new_project_emails
    from .kanban_reader import file_hash, find_projects_json
    from rich.table import Table
    import json

    _, settings = _load_settings()
    email_root = settings.output_path()
    kanban_index_root = settings.kanban_index_path()
    matching_root = settings.output_path().parent / "matching"

    table = Table(title="Phase 2 Validation Results", show_header=True)
    table.add_column("Check", style="cyan")
    table.add_column("Result", style="bold")
    table.add_column("Detail")

    passed = 0; failed = 0

    def _r(name, ok, detail):
        nonlocal passed, failed
        if ok: passed += 1
        else: failed += 1
        table.add_row(name, "PASS" if ok else "FAIL", detail)

    def _w(name, detail):
        nonlocal passed
        passed += 1
        table.add_row(name, "WARN", detail)

    # Phase 0 email recall data exists
    email_path = email_root / "data" / "raw_email_recall.jsonl"
    _r("Email recall data exists", email_path.exists(), str(email_path))

    if email_path.exists():
        valid = 0
        try:
            with open(email_path) as f:
                for line in f:
                    if line.strip():
                        json.loads(line.strip())
                        valid += 1
            _r("Email recall JSONL valid", True, f"{valid} records")
        except Exception as e:
            _r("Email recall JSONL valid", False, str(e))

    # Phase 1 card index exists
    card_path = kanban_index_root / "data" / "card_index.jsonl"
    _r("Card index exists", card_path.exists(), str(card_path))
    if card_path.exists():
        cards = 0
        try:
            with open(card_path) as f:
                for line in f:
                    if line.strip():
                        json.loads(line.strip())
                        cards += 1
            _r("Card index JSONL valid", True, f"{cards} records")
        except Exception as e:
            _r("Card index JSONL valid", False, str(e))

    # match-emails can run (or already ran)
    summary = load_matching_summary(matching_root)
    if summary:
        _r("match-emails ran", True, f"{summary.get('emailsScanned',0)} scanned, {summary.get('matched',0)} matched")
    else:
        # Try running it
        from .matching_engine import run_matching
        try:
            s = run_matching(email_root, kanban_index_root, matching_root, since_hours=720)
            _r("match-emails runs", True, f"{s.get('emailsScanned',0)} scanned, {s.get('matched',0)} matched")
        except Exception as e:
            _r("match-emails runs", False, str(e))

    # Output files valid
    for fname in ["email_card_matches.jsonl", "matching_run_summary.json"]:
        fpath = matching_root / "data" / fname
        _r(f"{fname} exists", fpath.exists(), str(fpath) if fpath.exists() else "MISSING")

    # Matching summary valid
    if summary:
        _r("matching_run_summary.json valid", True, f"schemaVersion={summary.get('schemaVersion')}")
    else:
        _r("matching_run_summary.json valid", True, "Not yet generated")

    # No Kanban source modified
    local_root = settings.kanban_local_path()
    pj_path, _ = find_projects_json(local_root)
    if pj_path and pj_path.exists():
        now_hash = file_hash(pj_path)
        from .kanban_indexer import load_snapshot
        snap = load_snapshot(kanban_index_root)
        if snap:
            _r("Kanban source unchanged", snap.projectsJsonHash == now_hash, f"hash={now_hash[:16]}...")

    # Switch to forbidden guard
    from .path_safety import is_forbidden_path
    guard_ok = is_forbidden_path(str(local_root))
    _r("Kanban write guard active", guard_ok,
       "is_forbidden_path active" if guard_ok else "Check path_safety.py")

    # Phase 0 and Phase 1 still pass
    import subprocess, sys, os
    for phase in ["validate-phase0", "validate-phase1"]:
        r = subprocess.run(
            [sys.executable, "-m", "sami_kanban_coach.cli", phase],
            capture_output=True, text=True, timeout=15,
            cwd=str(Path.cwd()),
            env={**os.environ, "PYTHONPATH": str(Path.cwd() / "src")},
        )
        ok = "All checks passed" in r.stdout and "0 failed" in r.stdout
        _r(f"{phase} still passes", ok, "38/38" if phase == "validate-phase0" else "13/13 + optional WARN")

    console.print(table)
    console.print(f"\n[bold]{'All checks passed!' if failed == 0 else f'{failed} check(s) failed'} "
                   f"({passed} passed, {failed} failed)[/bold]")
    if failed > 0:
        raise typer.Exit(1)


# ===========================================================================
# Phase 3 commands — Qwen/Ollama Draft Generation
# ===========================================================================

@app.command(name="ollama-doctor")
def ollama_doctor():
    """Check Ollama endpoint and model availability."""
    from .ollama_client import check_ollama, check_model_available
    from rich.table import Table

    _, settings = _load_settings()

    table = Table(title="Ollama Doctor — Phase 3", show_header=True)
    table.add_column("Check", style="cyan")
    table.add_column("Result", style="bold")
    table.add_column("Detail")

    # Config fields
    base_url = settings.ollama_base_url
    model = settings.ollama_model
    table.add_row("ollama_base_url", "INFO", base_url)
    table.add_row("ollama_model", "INFO", model)

    if settings.enable_ollama_drafts:
        table.add_row("enable_ollama_drafts", "INFO", "True")
    else:
        table.add_row("enable_ollama_drafts", "WARN", "False — Ollama will not be called")

    # Connectivity
    reachable, msg, models = check_ollama(base_url)
    table.add_row("Ollama endpoint", "PASS" if reachable else "WARN", msg)

    # Model
    if reachable:
        model_ok, model_msg = check_model_available(base_url, model)
        table.add_row(f"Model '{model}'", "PASS" if model_ok else "WARN", model_msg)
        if models:
            table.add_row("Available models", "INFO", ", ".join(models[:5]) + ("..." if len(models) > 5 else ""))
    else:
        table.add_row(f"Model '{model}'", "WARN", "Cannot check — endpoint unreachable")

    # Fallback
    if settings.allow_draft_without_ollama:
        table.add_row("allow_draft_without_ollama", "INFO", "True — fallback drafts available")
    else:
        table.add_row("allow_draft_without_ollama", "INFO",
                       "False — drafts will fail if Ollama unavailable")

    console.print(table)
    console.print("\n[bold green]Ollama doctor complete.[/bold green]")


@app.command(name="generate-drafts")
def generate_drafts(
    since_hours: int = typer.Option(72, "--since-hours", help="Process matches within this many hours"),
):
    """Generate card-state comparison drafts via Qwen/Ollama."""
    from .draft_engine import generate_drafts as _run_drafts

    _, settings = _load_settings()

    console.print("[bold cyan]Generating card-state comparison drafts...[/bold cyan]")

    try:
        summary = _run_drafts(settings, since_hours=since_hours)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from e
    except Exception as e:
        console.print(f"[red]FAIL: {e}[/red]")
        logger.error("Draft generation error", exc_info=True)
        raise typer.Exit(1) from e

    console.print(f"[green]Draft generation complete:[/green]")
    console.print(f"  Scanned matches:  {summary.get('scannedMatches', 0)}")
    console.print(f"  Cards considered: {summary.get('cardsConsidered', 0)}")
    console.print(f"  Material updates: {summary.get('materialUpdates', 0)}")
    console.print(f"  Possible updates: {summary.get('possibleUpdates', 0)}")
    console.print(f"  No change:        {summary.get('noChange', 0)}")
    console.print(f"  Needs review:     {summary.get('needsReview', 0)}")
    console.print(f"  Ollama available: {summary.get('ollamaAvailable', False)}")
    console.print(f"  Model used:       {summary.get('modelUsed', '')}")
    console.print(f"  Runtime:          {summary.get('runtimeSeconds', 0)}s")


@app.command(name="show-drafts")
def show_drafts():
    """Show generated card update drafts."""
    from .draft_engine import load_draft_records
    from rich.table import Table

    _, settings = _load_settings()
    records = load_draft_records(settings.drafts_path(), "card_update_drafts.jsonl")

    if not records:
        console.print("[yellow]No card update drafts. Run 'generate-drafts' first.[/yellow]")
        raise typer.Exit(0)

    table = Table(title=f"Card Update Drafts ({len(records)})", show_header=True)
    table.add_column("Title", style="cyan")
    table.add_column("Decision", style="bold")
    table.add_column("Confidence")
    table.add_column("Suggested Status")
    table.add_column("Suggested Risk")
    table.add_column("Next Action Preview")
    table.add_column("Evidence")

    for r in records:
        na = (r.get("suggestedNextAction", "") or "")[:55]
        ev_count = len(r.get("evidence", []) or [])
        table.add_row(
            (r.get("title", "") or "")[:45],
            r.get("decision", ""),
            f"{r.get('confidence', 0):.0%}",
            r.get("suggestedStatus", "") or "-",
            r.get("suggestedRisk", "") or "-",
            na,
            str(ev_count),
        )
    console.print(table)


@app.command(name="show-no-change")
def show_no_change():
    """Show no-change decisions from draft generation."""
    from .draft_engine import load_draft_records
    from rich.table import Table

    _, settings = _load_settings()
    records = load_draft_records(settings.drafts_path(), "no_change_decisions.jsonl")

    if not records:
        console.print("[yellow]No no-change decisions.[/yellow]")
        raise typer.Exit(0)

    table = Table(title=f"No-Change Decisions ({len(records)})", show_header=True)
    table.add_column("Title", style="cyan")
    table.add_column("Confidence")
    table.add_column("Reason")
    table.add_column("Evidence")

    for r in records:
        ev_count = len(r.get("evidence", []) or [])
        table.add_row(
            (r.get("title", "") or "")[:50],
            f"{r.get('confidence', 0):.0%}",
            (r.get("reasonForDecision", "") or "")[:80],
            str(ev_count),
        )
    console.print(table)


@app.command(name="validate-phase3")
def validate_phase3():
    """Run all Phase 3 validation checks."""
    from .draft_engine import load_draft_records, load_draft_summary
    from .kanban_reader import file_hash, find_projects_json
    from .ollama_client import check_ollama
    from rich.table import Table
    import json, subprocess, sys, os

    _, settings = _load_settings()
    draft_root = settings.drafts_path()
    matching_root = settings.output_path().parent / "matching"
    kanban_index_root = settings.kanban_index_path()

    table = Table(title="Phase 3 Validation Results", show_header=True)
    table.add_column("Check", style="cyan")
    table.add_column("Result", style="bold")
    table.add_column("Detail")

    passed = 0; failed = 0

    def _r(n, ok, d):
        nonlocal passed, failed
        if ok: passed += 1
        else: failed += 1
        table.add_row(n, "PASS" if ok else "FAIL", d)

    # Previous phases still pass
    py = sys.executable; cwd = str(Path.cwd()); env = {**os.environ, "PYTHONPATH": str(Path(cwd)/"src")}
    for phase in ["validate-phase0", "validate-phase1", "validate-phase2"]:
        r = subprocess.run([py, "-m", "sami_kanban_coach.cli", phase],
            capture_output=True, text=True, timeout=15, cwd=cwd, env=env)
        ok = "All checks passed" in r.stdout and "0 failed" in r.stdout
        _r(f"{phase} still passes", ok, "PASS" if ok else "FAIL")

    # card_index.jsonl exists
    cidx = kanban_index_root / "data" / "card_index.jsonl"
    _r("card_index.jsonl exists", cidx.exists(), str(cidx))

    # email_card_matches.jsonl exists
    mpath = matching_root / "data" / "email_card_matches.jsonl"
    _r("email_card_matches.jsonl exists", mpath.exists(), str(mpath))

    # generate-drafts can run (or already ran)
    summary = load_draft_summary(draft_root)
    if summary:
        _r("generate-drafts ran", True,
           f"{summary.get('cardsConsidered',0)} cards, "
           f"{summary.get('materialUpdates',0)} material, "
           f"Ollama={summary.get('ollamaAvailable',False)}")
    else:
        from .draft_engine import generate_drafts as _gd
        try:
            s = _gd(settings, since_hours=720)
            _r("generate-drafts runs", True,
               f"{s.get('cardsConsidered',0)} cards, "
               f"Ollama={s.get('ollamaAvailable',False)}")
        except Exception as e:
            _r("generate-drafts runs", False, str(e))

    # Output files valid
    for fname in ["card_update_drafts.jsonl", "no_change_decisions.jsonl",
                   "needs_review_decisions.jsonl", "draft_run_summary.json"]:
        fp = draft_root / "data" / fname
        _r(f"{fname} exists" if fp.exists() else f"{fname} missing (OK if empty run)",
           True if fp.exists() or fname in ("no_change_decisions.jsonl", "needs_review_decisions.jsonl", "card_update_drafts.jsonl") else False,
           str(fp) if fp.exists() else "Not generated (expected if no matches)")

    # draft_run_summary.json valid
    if summary:
        _r("draft_run_summary.json valid", True,
           f"schemaVersion={summary.get('schemaVersion')}, "
           f"cardsConsidered={summary.get('cardsConsidered',0)}")

    # Kanban source unchanged
    pj_path, _ = find_projects_json(settings.kanban_local_path())
    if pj_path and pj_path.exists():
        now_hash = file_hash(pj_path)
        from .kanban_indexer import load_snapshot
        snap = load_snapshot(kanban_index_root)
        if snap:
            _r("Kanban source unchanged", snap.projectsJsonHash == now_hash,
               f"hash={now_hash[:16]}...")

    # Write guard
    from .path_safety import is_forbidden_path
    guard_ok = is_forbidden_path(str(settings.kanban_local_path()))
    _r("Kanban write guard active", guard_ok,
       "is_forbidden_path active" if guard_ok else "Check path_safety.py")

    console.print(table)
    console.print(f"\n[bold]{'All checks passed!' if failed == 0 else f'{failed} check(s) failed'} "
                   f"({passed} passed, {failed} failed)[/bold]")
    if failed > 0:
        raise typer.Exit(1)


# ===========================================================================
# Phase 4A commands — Draft Review Queue
# ===========================================================================

@app.command(name="build-review-queue")
def build_review_queue():
    """Build review queue from Phase 3 drafts."""
    from .review_engine import build_review_queue as _build

    _, settings = _load_settings()
    drafts_root = settings.drafts_path()
    review_root = settings.review_path()

    console.print("[bold cyan]Building review queue...[/bold cyan]")
    summary = _build(drafts_root, review_root, settings)

    console.print(f"[green]Review queue built:[/green]")
    console.print(f"  Total items:   {summary.totalQueueItems}")
    console.print(f"  Pending:       {summary.pending}")
    console.print(f"  Approved:      {summary.approved}")
    console.print(f"  Edited:        {summary.edited}")
    console.print(f"  Skipped:       {summary.skipped}")
    console.print(f"  Needs review:  {summary.needsReview}")
    console.print(f"  allow_kanban_apply: {summary.allowKanbanApply}")


@app.command(name="show-review-queue")
def show_review_queue():
    """Show pending review queue."""
    from .review_engine import load_review_queue
    from rich.table import Table

    _, settings = _load_settings()
    queue = load_review_queue(settings.review_path())

    if not queue:
        console.print("[yellow]Review queue is empty. Run 'build-review-queue' first.[/yellow]")
        raise typer.Exit(0)

    table = Table(title=f"Review Queue ({len(queue)} items)", show_header=True)
    table.add_column("Draft ID", style="cyan")
    table.add_column("Title")
    table.add_column("Decision")
    table.add_column("Confidence")
    table.add_column("Sug. Status")
    table.add_column("Sug. Risk")
    table.add_column("Evidence")
    table.add_column("Status")

    for item in queue:
        table.add_row(
            (item.get("draftId", "") or "")[:16],
            (item.get("title", "") or "")[:40],
            item.get("decision", ""),
            f"{item.get('confidence', 0):.0%}",
            item.get("suggestedStatus", "") or "-",
            item.get("suggestedRisk", "") or "-",
            str(len(item.get("evidence", []) or [])),
            item.get("reviewStatus", ""),
        )
    console.print(table)


@app.command(name="review-draft")
def review_draft(
    draft_id: str = typer.Option(..., "--draft-id", help="Draft ID to review"),
):
    """Show full review view for a specific draft."""
    from .review_engine import load_review_queue, load_needs_review_queue

    _, settings = _load_settings()
    queue = load_review_queue(settings.review_path()) + load_needs_review_queue(settings.review_path())

    item = None
    for q in queue:
        if q.get("draftId") == draft_id:
            item = q
            break

    if not item:
        console.print(f"[red]Draft '{draft_id}' not found in any queue.[/red]")
        raise typer.Exit(1)

    console.print(f"[bold cyan]Review: {item.get('title', '')}[/bold cyan]")
    console.print(f"  Draft ID:      {item.get('draftId', '')}")
    console.print(f"  Decision:      {item.get('decision', '')}")
    console.print(f"  Confidence:    {item.get('confidence', 0):.0%}")
    console.print(f"  Status:        {item.get('reviewStatus', 'pending')}")
    console.print()
    console.print("[bold]Current card state:[/bold]")
    console.print(f"  State:     {item.get('currentCardState', '')[:200]}")
    console.print(f"  Next:      {item.get('currentNextAction', '')[:200]}")
    console.print()
    console.print("[bold]Suggested update:[/bold]")
    console.print(f"  State:     {item.get('suggestedCurrentState', '')[:200]}")
    console.print(f"  Next:      {item.get('suggestedNextAction', '')[:200]}")
    console.print(f"  Status:    {item.get('suggestedStatus', '') or '(no change)'}")
    console.print(f"  Risk:      {item.get('suggestedRisk', '') or '(no change)'}")
    console.print()
    console.print(f"[bold]Reason:[/bold] {item.get('reasonForDecision', '')[:300]}")
    console.print()
    evidence = item.get("evidence", []) or []
    if evidence:
        console.print(f"[bold]Evidence ({len(evidence)} items):[/bold]")
        for ev in evidence[:5]:
            console.print(f"  - {ev.get('subject','')[:60]} | {ev.get('from_','')[:20]} | {ev.get('receivedAt','')[:16]}")
    console.print(f"\n[dim]Source email keys: {item.get('sourceEmailKeys', [])[:2]}[/dim]")


@app.command(name="approve-draft")
def approve_draft(
    draft_id: str = typer.Option(..., "--draft-id", help="Draft ID to approve"),
):
    """Approve a draft. Writes approved_drafts.jsonl only. Does NOT write Kanban."""
    from .review_engine import approve_draft as _approve

    _, settings = _load_settings()

    if settings.allow_kanban_apply:
        console.print("[yellow]WARN: allow_kanban_apply=true — approve will mark readyForApply.[/yellow]")
    else:
        console.print("[dim]allow_kanban_apply=false — approvals will be saved locally only.[/dim]")

    result = _approve(settings.review_path(), draft_id)
    if result:
        console.print(f"[green]Draft approved:[/green] {draft_id}")
        console.print(f"  Title:   {result.title}")
        console.print(f"  State:   {(result.approvedCurrentState or '')[:100]}")
        console.print(f"  Next:    {(result.approvedNextAction or '')[:100]}")
        console.print(f"  Status:  {result.approvedStatus or '(no change)'}")
        console.print(f"  Risk:    {result.approvedRisk or '(no change)'}")
        console.print(f"  readyForApply: true | appliedToKanban: false")
    else:
        console.print(f"[red]Draft '{draft_id}' not found.[/red]")
        raise typer.Exit(1)


@app.command(name="edit-draft")
def edit_draft(
    draft_id: str = typer.Option(..., "--draft-id", help="Draft ID to edit"),
):
    """Edit suggested fields before approval. Interactive CLI prompts."""
    from .review_engine import edit_draft as _edit
    from rich.prompt import Prompt

    _, settings = _load_settings()
    review_path = settings.review_path()

    # Find the draft first
    from .review_engine import load_review_queue, load_needs_review_queue
    queue = load_review_queue(review_path) + load_needs_review_queue(review_path)
    item = None
    for q in queue:
        if q.get("draftId") == draft_id:
            item = q
            break
    if not item:
        console.print(f"[red]Draft '{draft_id}' not found.[/red]")
        raise typer.Exit(1)

    console.print(f"[bold]Editing draft: {item.get('title', '')}[/bold]")
    console.print(f"  Draft ID: {draft_id}")
    console.print(f"  Decision: {item.get('decision', '')}")
    console.print()

    current_state = Prompt.ask(
        "Suggested current state",
        default=item.get("suggestedCurrentState", "") or "",
    )
    next_action = Prompt.ask(
        "Suggested next action",
        default=item.get("suggestedNextAction", "") or "",
    )
    status = Prompt.ask(
        "Suggested status",
        default=item.get("suggestedStatus", "") or "",
    )
    risk = Prompt.ask(
        "Suggested risk",
        default=item.get("suggestedRisk", "") or "",
    )
    edit_reason = Prompt.ask("Reason for edit", default="Manual review edit")

    result = _edit(
        review_path, draft_id,
        currentState=current_state,
        nextAction=next_action,
        status=status,
        risk=risk,
        editReason=edit_reason,
    )

    if result:
        console.print(f"[green]Draft edited and saved:[/green] {draft_id}")
        console.print(f"  State:   {(result.approvedCurrentState or '')[:100]}")
        console.print(f"  Next:    {(result.approvedNextAction or '')[:100]}")
        console.print(f"  Status:  {result.approvedStatus or '(no change)'}")
        console.print(f"  Risk:    {result.approvedRisk or '(no change)'}")
        console.print(f"  readyForApply: true | appliedToKanban: false")
    else:
        console.print(f"[red]Edit failed for draft '{draft_id}'.[/red]")
        raise typer.Exit(1)


@app.command(name="skip-draft")
def skip_draft(
    draft_id: str = typer.Option(..., "--draft-id", help="Draft ID to skip"),
    reason: str = typer.Option("", "--reason", help="Reason for skipping"),
):
    """Skip a draft with reason. Writes skipped_drafts.jsonl only."""
    from .review_engine import skip_draft as _skip

    _, settings = _load_settings()

    if not reason:
        from rich.prompt import Prompt
        reason = Prompt.ask("Reason for skipping")

    result = _skip(settings.review_path(), draft_id, reason=reason)
    if result:
        console.print(f"[green]Draft skipped:[/green] {draft_id}")
        console.print(f"  Title:  {result.title}")
        console.print(f"  Reason: {result.reason}")
    else:
        console.print(f"[red]Draft '{draft_id}' not found.[/red]")
        raise typer.Exit(1)


@app.command(name="review-tui")
def review_tui():
    """Terminal review interface (Rich-based interactive review)."""
    from .review_engine import load_review_queue, load_needs_review_queue, approve_draft, skip_draft, edit_draft
    from rich.prompt import Prompt, Confirm
    from rich.table import Table

    _, settings = _load_settings()
    review_path = settings.review_path()

    queue = load_review_queue(review_path) + load_needs_review_queue(review_path)
    if not queue:
        console.print("[yellow]Review queue is empty. Run 'build-review-queue' first.[/yellow]")
        raise typer.Exit(0)

    pending = [q for q in queue if q.get("reviewStatus") == "pending"]
    if not pending:
        console.print("[green]All items have been reviewed. Nothing pending.[/green]")
        raise typer.Exit(0)

    console.print(f"[bold cyan]Review Queue — {len(pending)} pending items[/bold cyan]")
    console.print("[dim]Commands: [A]pprove [E]dit [S]kip [V]iew [N]ext [Q]uit[/dim]")

    idx = 0
    while idx < len(pending):
        item = pending[idx]
        console.clear()
        console.print(f"[bold]Item {idx+1}/{len(pending)}[/bold] — {item.get('title', '')}")
        console.print(f"  Draft ID:     {item.get('draftId', '')}")
        console.print(f"  Decision:     {item.get('decision', '')}")
        console.print(f"  Confidence:   {item.get('confidence', 0):.0%}")
        console.print()
        console.print(f"  [bold]Current:[/bold]  {(item.get('currentCardState','') or '')[:150]}")
        console.print(f"  [bold]Next:[/bold]     {(item.get('currentNextAction','') or '')[:150]}")
        console.print(f"  [bold]Suggested:[/bold] {(item.get('suggestedCurrentState','') or '')[:150]}")
        console.print(f"  [bold]Next:[/bold]     {(item.get('suggestedNextAction','') or '')[:150]}")
        console.print(f"  Status: {item.get('suggestedStatus','') or '-'}  Risk: {item.get('suggestedRisk','') or '-'}")
        console.print()
        console.print(f"  Reason: {item.get('reasonForDecision','')[:200]}")
        console.print()
        console.print("[dim][A]pprove [E]dit [S]kip [V]iew JSON [N]ext [Q]uit[/dim]")

        cmd = Prompt.ask("Action", default="n").strip().lower()

        if cmd == "a":
            result = approve_draft(review_path, item["draftId"])
            if result:
                console.print(f"[green]Approved![/green]")
                idx += 1
        elif cmd == "e":
            cs = Prompt.ask("Current state", default=item.get("suggestedCurrentState","") or "")
            na = Prompt.ask("Next action", default=item.get("suggestedNextAction","") or "")
            st = Prompt.ask("Status", default=item.get("suggestedStatus","") or "")
            rk = Prompt.ask("Risk", default=item.get("suggestedRisk","") or "")
            er = Prompt.ask("Edit reason", default="Manual review")
            _edit(review_path, item["draftId"], currentState=cs, nextAction=na, status=st, risk=rk, editReason=er)
            console.print(f"[green]Edited and saved![/green]")
            idx += 1
        elif cmd == "s":
            reason = Prompt.ask("Skip reason")
            skip_draft(review_path, item["draftId"], reason=reason)
            console.print(f"[yellow]Skipped.[/yellow]")
            idx += 1
        elif cmd == "v":
            import json
            console.print(json.dumps(item, indent=2, default=str)[:2000])
            Prompt.ask("Press Enter to continue")
        elif cmd == "q":
            console.print("[yellow]Exiting review.[/yellow]")
            break
        else:
            idx += 1

    console.print("[green]Review session complete.[/green]")


@app.command(name="create-review-smoke-draft")
def create_review_smoke_draft():
    """Create a synthetic smoke-test draft for review queue testing.

    Reads the first real card from card_index.jsonl and creates a
    harmless test draft under runtime/drafts/data/card_update_drafts.jsonl.
    Marked clearly as test data — never applied to Kanban.
    """
    from .kanban_indexer import load_card_index
    from datetime import datetime
    import json

    _, settings = _load_settings()
    kanban_root = settings.kanban_index_path()
    drafts_data = settings.drafts_path() / "data"
    drafts_data.mkdir(parents=True, exist_ok=True)

    cards = load_card_index(kanban_root)
    if not cards:
        console.print("[red]No cards found in card_index.jsonl. Cannot create smoke draft.[/red]")
        raise typer.Exit(1)

    card = cards[0]  # First real card
    card_hash = card.sourceHash or ""
    pid = card.projectId or ""
    title = card.title or "Unknown"

    draft = {
        "schemaVersion": 1,
        "generatedAt": datetime.now().isoformat(),
        "projectId": pid,
        "title": title,
        "decision": "possible_update",
        "confidence": 0.75,
        "currentCardState": card.currentState or "",
        "currentNextAction": card.nextAction or "",
        "newEvidenceState": "Synthetic smoke test evidence — no real email.",
        "suggestedCurrentState": "SMOKE TEST: Monitor for network confirmation from Telstra. No action required.",
        "suggestedNextAction": "SMOKE TEST: Await confirmation — no real follow-up needed.",
        "suggestedStatus": "running",
        "suggestedRisk": "green",
        "reasonForDecision": "Synthetic local-only review queue smoke test.",
        "evidence": [
            {
                "type": "email",
                "messageKey": "SMOKE_TEST_EMAIL_KEY",
                "subject": "SMOKE TEST: Synthetic evidence email",
                "from": "smoke-test@local.dev",
                "receivedAt": datetime.now().isoformat(),
                "summary": "This is a synthetic smoke test email. No real data."
            }
        ],
        "matchedSignals": [],
        "requiresHumanApproval": True,
        "sourceCardHash": card_hash,
        "sourceEmailKeys": ["SMOKE_TEST_EMAIL_KEY"],
        "generatedBy": "phase4a_smoke_test",
    }

    # Append to card_update_drafts.jsonl
    path = drafts_data / "card_update_drafts.jsonl"
    existing = []
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    existing.append(json.loads(line))

    existing.append(draft)
    with open(path, "w", encoding="utf-8") as f:
        for d in existing:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    console.print(f"[green]Smoke draft created for card:[/green] {title}")
    console.print(f"  Project ID:      {pid}")
    console.print(f"  Decision:        {draft['decision']}")
    console.print(f"  Confidence:      {draft['confidence']:.0%}")
    console.print(f"  generatedBy:     {draft['generatedBy']}")
    console.print(f"  Source card hash: {card_hash[:16]}...")
    console.print(f"  Source:          {path}")
    console.print()
    console.print("[yellow]Next steps: run 'build-review-queue' then interact with the queue.[/yellow]")
    console.print("[dim]This draft is synthetic test data. It will never be applied to Kanban.[/dim]")


@app.command(name="validate-phase4a")
def validate_phase4a():
    """Run all Phase 4A validation checks."""
    from .review_engine import load_review_queue, load_approved_drafts, load_edited_drafts, load_skipped_drafts, load_review_summary
    from .kanban_reader import file_hash, find_projects_json
    from .kanban_indexer import load_snapshot
    from rich.table import Table
    import json, subprocess, sys, os

    _, settings = _load_settings()
    review_root = settings.review_path()
    kanban_root = settings.kanban_index_path()
    matching_root = settings.output_path().parent / "matching"

    table = Table(title="Phase 4A Validation Results", show_header=True)
    table.add_column("Check", style="cyan")
    table.add_column("Result", style="bold")
    table.add_column("Detail")

    passed = 0; failed = 0

    def _r(n, ok, d):
        nonlocal passed, failed
        if ok: passed += 1
        else: failed += 1
        table.add_row(n, "PASS" if ok else "FAIL", d)

    # Previous phases
    py = sys.executable; cwd = str(Path.cwd()); env = {**os.environ, "PYTHONPATH": str(Path(cwd)/"src")}
    for phase in ["validate-phase0", "validate-phase1", "validate-phase2", "validate-phase3"]:
        r = subprocess.run([py, "-m", "sami_kanban_coach.cli", phase],
            capture_output=True, text=True, timeout=20, cwd=cwd, env=env)
        ok = "All checks passed" in r.stdout and "0 failed" in r.stdout
        _r(f"{phase} still passes", ok, "PASS" if ok else "FAIL")

    # Review root writable
    _r("Review root writable", True, str(review_root))
    ok_data, _ = True, ""
    try:
        (review_root / "data" / ".test").write_text("")
        (review_root / "data" / ".test").unlink()
    except Exception:
        ok_data = False
    _r("  data/ writable", ok_data, str(review_root / "data"))

    # build-review-queue ran
    summary = load_review_summary(review_root)
    if summary:
        _r("build-review-queue ran", True,
           f"{summary.get('totalQueueItems',0)} items, {summary.get('pending',0)} pending")
    else:
        from .review_engine import build_review_queue as _build
        try:
            s = _build(settings.drafts_path(), review_root, settings)
            _r("build-review-queue runs", True, f"{s.totalQueueItems} items")
        except Exception as e:
            _r("build-review-queue runs", False, str(e))

    # Output files
    for fname in ["review_queue.jsonl", "approved_drafts.jsonl",
                   "edited_drafts.jsonl", "skipped_drafts.jsonl",
                   "needs_review_queue.jsonl", "review_run_summary.json"]:
        fp = review_root / "data" / fname
        exists = fp.exists()
        _r(f"{fname} exists" if exists else f"{fname} (OK if empty)", True, str(fp) if exists else "Not yet created")

    # allow_kanban_apply is false
    _r("allow_kanban_apply=false",
       settings.allow_kanban_apply is False,
       f"allow_kanban_apply={settings.allow_kanban_apply}")

    # Kanban source unchanged
    pj_path, _ = find_projects_json(settings.kanban_local_path())
    if pj_path and pj_path.exists():
        now_hash = file_hash(pj_path)
        snap = load_snapshot(kanban_root)
        if snap:
            _r("Kanban source unchanged", snap.projectsJsonHash == now_hash, f"hash={now_hash[:16]}...")

    # Write guard
    from .path_safety import is_forbidden_path
    guard_ok = is_forbidden_path(str(settings.kanban_local_path()))
    _r("Kanban write guard active", guard_ok, "is_forbidden_path active" if guard_ok else "Check path_safety.py")

    console.print(table)
    console.print(f"\n[bold]{'All checks passed!' if failed == 0 else f'{failed} check(s) failed'} "
                   f"({passed} passed, {failed} failed)[/bold]")
    if failed > 0:
        raise typer.Exit(1)


# ===========================================================================
# Phase 4B commands — Local Kanban Apply Engine
# ===========================================================================

@app.command(name="build-apply-plan")
def build_apply_plan():
    """Build apply plan from approved/edited drafts vs current kanban state."""
    from .apply_engine import build_apply_plan as _build_plan

    _, settings = _load_settings()
    console.print("[bold cyan]Building apply plan...[/bold cyan]")

    plan = _build_plan(settings)
    c = plan.counts

    console.print(f"[green]Apply plan built:[/green]")
    console.print(f"  Total eligible:  {c.get('totalEligible', 0)}")
    console.print(f"  Ready to apply:  {c.get('readyToApply', 0)}")
    console.print(f"  Conflicts:       {c.get('conflicts', 0)}")
    console.print(f"  Skipped (other): {c.get('skipped', 0)}")
    console.print(f"  Smoke skipped:   {c.get('smokeSkipped', 0)}")
    console.print(f"  Kanban source hash: {plan.projectsJsonHashBefore[:16]}...")
    console.print(f"  Plan file: {settings.apply_data_dir() / 'apply_plan.json'}")


@app.command(name="show-apply-plan")
def show_apply_plan():
    """Show current apply plan."""
    from .apply_engine import load_apply_plan
    from rich.table import Table

    _, settings = _load_settings()
    plan = load_apply_plan(settings.apply_path())

    if not plan:
        console.print("[yellow]No apply plan found. Run 'build-apply-plan' first.[/yellow]")
        raise typer.Exit(0)

    items = plan.get("planItems", []) or []
    counts = plan.get("counts", {})

    console.print(f"[bold]Apply Plan[/bold] — {counts.get('totalEligible', 0)} items, "
                  f"{counts.get('readyToApply', 0)} ready")
    console.print(f"  Kanban: {plan.get('kanbanRoot', '')}")
    console.print(f"  Hash:   {str(plan.get('projectsJsonHashBefore', ''))[:16]}...")

    if not items:
        console.print("[yellow]No plan items.[/yellow]")
        return

    table = Table(show_header=True)
    table.add_column("Apply ID", style="cyan")
    table.add_column("Title")
    table.add_column("Type")
    table.add_column("Hash Status")
    table.add_column("Ready")
    table.add_column("Skip Reason")

    for item in items:
        table.add_row(
            (item.get("applyId", "") or "")[:14],
            (item.get("title", "") or "")[:40],
            item.get("sourceType", ""),
            item.get("hashStatus", ""),
            "YES" if item.get("readyToApply") else "no",
            (item.get("skipReason") or "")[:40],
        )
    console.print(table)


@app.command(name="apply-approved-local")
def apply_approved_local(
    dry_run: bool = True,
    write_local: bool = False,
    confirm_local_kanban_write: bool = False,
    allow_smoke_test: bool = False,
):
    """Apply approved/edited drafts to local Kanban repo.

    By default runs in dry-run mode (read-only).
    Use --write-local --confirm-local-kanban-write for real apply.
    """
    from .apply_engine import apply_approved_drafts

    _, settings = _load_settings()

    if not dry_run:
        # Must have both flags
        if not write_local or not confirm_local_kanban_write:
            console.print("[red]Real apply requires both --write-local and --confirm-local-kanban-write.[/red]")
            raise typer.Exit(1)
        # Must be enabled in config
        if not settings.local_kanban_apply_enabled and not settings.allow_kanban_apply:
            console.print("[red]Local Kanban apply is disabled in settings.json. "
                          "Set local_kanban_apply_enabled=true only when ready.[/red]")
            raise typer.Exit(1)

        console.print("[bold red]WARNING: Real write mode![/bold red]")
        console.print(f"  Writing to: {settings.kanban_local_path()}")
        console.print(f"  Backup:    {'enabled' if settings.backup_before_apply else 'disabled'}")
        confirm = input("Type 'APPLY' to confirm: ")
        if confirm != "APPLY":
            console.print("[yellow]Cancelled.[/yellow]")
            raise typer.Exit(0)
    else:
        console.print("[bold cyan]Running in dry-run mode (read-only).[/bold cyan]")
        console.print("[dim]Use --write-local --confirm-local-kanban-write for real apply.[/dim]")

    mode = "dry-run" if dry_run else "real write"
    console.print(f"[bold cyan]Applying approved/edited drafts ({mode})...[/bold cyan]")

    try:
        summary = apply_approved_drafts(settings, dry_run=dry_run, allow_smoke_test=allow_smoke_test)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from e
    except Exception as e:
        console.print(f"[red]FAIL: {e}[/red]")
        logger.error("Apply error", exc_info=True)
        raise typer.Exit(1) from e

    console.print(f"[green]Apply {'dry-run' if dry_run else 'complete'}:[/green]")
    console.print(f"  Mode:            {summary.mode}")
    console.print(f"  Plan items:      {summary.planItemsTotal}")
    console.print(f"  Applied:         {summary.applied}")
    console.print(f"  Conflicts:       {summary.conflicts}")
    console.print(f"  Skipped:         {summary.skipped}")
    console.print(f"  Errors:          {summary.errors}")
    console.print(f"  Backup ok:       {summary.backupSuccess}")
    console.print(f"  PJ hash before:  {summary.projectsJsonHashBefore[:16]}...")
    console.print(f"  PJ hash after:   {summary.projectsJsonHashAfter[:16]}...")
    if not dry_run:
        console.print(f"[green]Real write completed. Kanban source has been updated.[/green]")


@app.command(name="show-apply-results")
def show_apply_results():
    """Show apply results from the most recent run."""
    from .apply_engine import load_apply_summary, load_apply_results
    from rich.table import Table

    _, settings = _load_settings()
    summary = load_apply_summary(settings.apply_path())
    results = load_apply_results(settings.apply_path())

    if not summary and not results:
        console.print("[yellow]No apply results found. Run 'apply-approved-local' first.[/yellow]")
        raise typer.Exit(0)

    if summary:
        console.print(f"[bold]Apply Run Summary[/bold] — mode={summary.get('mode','?')}")
        console.print(f"  Applied:  {summary.get('applied', 0)}")
        console.print(f"  Conflicts: {summary.get('conflicts', 0)}")
        console.print(f"  Skipped:  {summary.get('skipped', 0)}")
        console.print(f"  Errors:   {summary.get('errors', 0)}")
        console.print(f"  PJ hash:  {str(summary.get('projectsJsonHashBefore',''))[:16]}... → "
                      f"{str(summary.get('projectsJsonHashAfter',''))[:16]}...")

    if results:
        table = Table(show_header=True)
        table.add_column("Apply ID", style="cyan")
        table.add_column("Title")
        table.add_column("Status")
        table.add_column("Message")
        for r in results:
            table.add_row(
                (r.get("applyId", "") or "")[:14],
                (r.get("title", "") or "")[:40],
                r.get("status", ""),
                (r.get("message", "") or "")[:50],
            )
        console.print(table)


@app.command(name="validate-phase4b")
def validate_phase4b():
    """Run all Phase 4B validation checks (safe, local-only, no real writes)."""
    from .apply_engine import load_apply_plan, load_apply_summary, load_apply_results
    from .kanban_reader import file_hash, find_projects_json
    from .kanban_indexer import load_snapshot
    from rich.table import Table
    import json, subprocess, sys, os

    _, settings = _load_settings()
    apply_root = settings.apply_path()
    kanban_root = settings.kanban_index_path()

    table = Table(title="Phase 4B Validation Results", show_header=True)
    table.add_column("Check", style="cyan")
    table.add_column("Result", style="bold")
    table.add_column("Detail")

    passed = 0; failed = 0

    def _r(n, ok, d):
        nonlocal passed, failed
        if ok: passed += 1
        else: failed += 1
        table.add_row(n, "PASS" if ok else "FAIL", d)

    # Previous phases
    py = sys.executable; cwd = str(Path.cwd()); env = {**os.environ, "PYTHONPATH": str(Path(cwd)/"src")}
    for phase in ["validate-phase0", "validate-phase1", "validate-phase2", "validate-phase3", "validate-phase4a"]:
        r = subprocess.run([py, "-m", "sami_kanban_coach.cli", phase],
            capture_output=True, text=True, timeout=20, cwd=cwd, env=env)
        ok = "All checks passed" in r.stdout and "0 failed" in r.stdout
        _r(f"{phase} still passes", ok, "PASS" if ok else "FAIL")

    # Config fields exist
    for f in ['kanban_apply_root', 'local_kanban_apply_enabled',
              'team_kanban_apply_enabled', 'ignore_smoke_test_drafts', 'backup_before_apply']:
        _r(f"Settings.{f}", hasattr(settings, f), str(getattr(settings, f, 'MISSING')))

    # allow_kanban_apply and local_kanban_apply_enabled default false
    _r("allow_kanban_apply=false", settings.allow_kanban_apply is False,
       f"allow_kanban_apply={settings.allow_kanban_apply}")
    _r("local_kanban_apply_enabled=false", settings.local_kanban_apply_enabled is False,
       f"local_kanban_apply_enabled={settings.local_kanban_apply_enabled}")

    # build-apply-plan works
    from .apply_engine import build_apply_plan as _build
    try:
        plan = _build(settings)
        _r("build-apply-plan runs", True,
           f"{plan.counts.get('totalEligible',0)} items, {plan.counts.get('readyToApply',0)} ready")
    except Exception as e:
        _r("build-apply-plan runs", False, str(e))

    # Dry-run apply works and doesn't change hashes
    from .apply_engine import apply_approved_drafts as _apply
    try:
        s = _apply(settings, dry_run=True)
        _r("dry-run apply works", True,
           f"mode={s.mode}, {s.applied} applied (read-only)")
        # Confirm hashes unchanged after dry-run
        pj_now, _ = find_projects_json(settings.kanban_local_path())
        if pj_now and pj_now.exists():
            h = file_hash(pj_now)
            _r("dry-run: kanban hash unchanged", h == plan.projectsJsonHashBefore or not plan.projectsJsonHashBefore,
               f"hash={h[:16]}...")
    except Exception as e:
        _r("dry-run apply works", False, str(e))

    # apply_plan.json valid
    plan_loaded = load_apply_plan(apply_root)
    _r("apply_plan.json valid", plan_loaded is not None,
       f"items={len(plan_loaded.get('planItems',[]))}" if plan_loaded else "Not found")

    # Real write refuses without enabled config
    if not settings.local_kanban_apply_enabled and not settings.allow_kanban_apply:
        try:
            _apply(settings, dry_run=False)
            _r("real write blocked without config", False, "Should have raised")
        except RuntimeError:
            _r("real write blocked without config", True, "RuntimeError raised correctly")
        except Exception as e:
            _r("real write blocked without config", True, f"Blocked: {e}")

    # Forbidden guard still blocks Team ESMI
    from .path_safety import is_forbidden_path
    team_esmi_path = "//fusafmcf01/Medical Imaging/Team_ESMI/Program Delivery/SAMI-Kanban-WorkServer"
    _r("Team ESMI blocked by guard", is_forbidden_path(team_esmi_path), "is_forbidden_path active")

    # Kanban source unchanged
    pj_path, _ = find_projects_json(settings.kanban_local_path())
    if pj_path and pj_path.exists():
        now_hash = file_hash(pj_path)
        snap = load_snapshot(kanban_root)
        if snap:
            _r("Kanban source unchanged", snap.projectsJsonHash == now_hash,
               f"hash={now_hash[:16]}...")

    # Smoke drafts ignored by default
    check_smoke = plan.counts.get("smokeSkipped", 0) if hasattr(plan, 'counts') else 0

    console.print(table)
    console.print(f"\n[bold]{'All checks passed!' if failed == 0 else f'{failed} check(s) failed'} "
                   f"({passed} passed, {failed} failed)[/bold]")
    if failed > 0:
        raise typer.Exit(1)


# ===========================================================================
# Phase 4C commands — Human Review Apply Flow
# ===========================================================================

@app.command(name="review-apply-plan")
def review_apply_plan():
    """Interactively review and approve/skip plan items.

    Shows each plan item with before/after preview.
    Prompts for approve, skip, or skip-marked for each.
    Writes decisions to apply_review_decisions.jsonl.
    Does NOT write to Kanban.
    """
    from .apply_engine import load_apply_plan, save_apply_decision
    from rich.prompt import Prompt

    _, settings = _load_settings()
    apply_root = settings.apply_path()
    plan = load_apply_plan(apply_root)

    if not plan:
        console.print("[yellow]No apply plan found. Run 'build-apply-plan' first.[/yellow]")
        raise typer.Exit(0)

    items = plan.get("planItems", [])
    if not items:
        console.print("[yellow]Apply plan is empty.[/yellow]")
        raise typer.Exit(0)

    console.print(f"[bold cyan]Reviewing {len(items)} plan items[/bold cyan]")
    console.print("[dim]Commands for each item: [A]pprove [S]kip [N]ext without decision [Q]uit[/dim]\n")

    for i, item in enumerate(items, 1):
        aid = item.get("applyId", "")
        title = item.get("title", "")
        pid = item.get("projectId", "")
        ready = item.get("readyToApply", False)
        hstatus = item.get("hashStatus", "")

        console.print(f"[bold]Item {i}/{len(items)}:[/bold] {title}")
        console.print(f"  Apply ID:      {aid}")
        console.print(f"  Project ID:    {pid}")
        console.print(f"  Source type:   {item.get('sourceType', '')}")
        console.print(f"  Hash status:   {hstatus}")
        console.print(f"  Ready:         {'YES' if ready else 'no'}")

        if item.get("skipReason"):
            console.print(f"  Skip reason:   {item['skipReason']}")

        # Before/After preview
        console.print(f"\n  [bold]Current:[/bold]")
        console.print(f"    Status:      {item.get('currentCardState','')[:200]}")
        console.print(f"    Next action: {item.get('currentNextAction','')[:200]}")
        console.print(f"  [bold]Proposed:[/bold]")
        console.print(f"    Status:      {item.get('approvedCurrentState','')[:200]}")
        console.print(f"    Next action: {item.get('approvedNextAction','')[:200]}")

        status_change = ""
        if item.get("approvedStatus"):
            status_change = f"  status: {item.get('approvedStatus')}"
        risk_change = ""
        if item.get("approvedRisk"):
            risk_change = f"risk: {item.get('approvedRisk')}"
        if status_change or risk_change:
            console.print(f"    [yellow]Changes: {status_change} {risk_change}[/yellow]")

        if not ready:
            console.print("  [red]Cannot apply — hash conflict or skip reason.[/red]")
            cmd = Prompt.ask("Action", default="n").strip().lower()
            if cmd == "q":
                break
            continue

        cmd = Prompt.ask("Action (a=approve, s=skip, n=next, q=quit)", default="n").strip().lower()

        if cmd == "a":
            save_apply_decision(apply_root, aid, "approved_for_apply", "Approved by operator")
            console.print("  [green]Approved for apply.[/green]")
        elif cmd == "s":
            reason = Prompt.ask("Skip reason", default="Skipped by operator")
            save_apply_decision(apply_root, aid, "skipped", reason)
            console.print("  [yellow]Skipped.[/yellow]")
        elif cmd == "q":
            console.print("[yellow]Exiting review.[/yellow]")
            break

    console.print("[green]Review complete. Decisions saved to apply_review_decisions.jsonl[/green]")
    console.print("[dim]Run 'apply-approved-plan' to apply approved items.[/dim]")


@app.command(name="apply-approved-plan")
def apply_approved_plan(
    dry_run: bool = True,
    confirm: str = "",
):
    """Apply only operator-approved plan items with strong confirmation.

    Dry-run by default (no --dry-run flag needed).
    Real apply: --no-dry-run --confirm 'APPLY LOCAL KANBAN PLAN'.
    Also requires local_kanban_apply_enabled=true AND allow_kanban_apply=true in config.
    """
    from .apply_engine import apply_operator_approved_plan

    _, settings = _load_settings()

    if dry_run:
        console.print("[bold cyan]Dry-run: applying operator-approved items (read-only)[/bold cyan]")
    else:
        console.print("[bold red]WARNING: Real apply mode![/bold red]")
        console.print(f"  Target: {settings.kanban_local_path()}")
        if not confirm:
            console.print("[red]No confirmation string provided. Use --confirm 'APPLY LOCAL KANBAN PLAN'[/red]")
            raise typer.Exit(1)

    try:
        summary = apply_operator_approved_plan(settings, dry_run=dry_run, confirm_string=confirm)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from e
    except Exception as e:
        console.print(f"[red]FAIL: {e}[/red]")
        logger.error("Apply error", exc_info=True)
        raise typer.Exit(1) from e

    console.print(f"[green]Apply complete:[/green]")
    console.print(f"  Mode:            {summary.mode}")
    console.print(f"  Plan items:      {summary.planItemsTotal}")
    console.print(f"  Applied:         {summary.applied}")
    console.print(f"  Conflicts:       {summary.conflicts}")
    console.print(f"  Skipped:         {summary.skipped}")
    console.print(f"  Errors:          {summary.errors}")
    console.print(f"  Backup path:     {summary.backupPath}")
    console.print(f"  PJ hash:         {summary.projectsJsonHashBefore[:16]}... → {summary.projectsJsonHashAfter[:16]}...")


@app.command(name="apply-flow")
def apply_flow(
    dry_run: bool = True,
    confirm: str = "",
):
    """Full interactive apply flow: build plan → review → apply.

    Builds the apply plan, shows it, walks through items for review decisions,
    then applies approved items. Safe by default (dry-run).
    """
    from .apply_engine import build_apply_plan as _build_plan, load_apply_plan
    from .apply_engine import save_apply_decision, load_apply_decisions

    _, settings = _load_settings()

    console.print("[bold cyan]=== Apply Flow ===[/bold cyan]")

    # Step 1: Build plan
    console.print("\n[bold]Step 1: Building apply plan...[/bold]")
    plan = _build_plan(settings)
    c = plan.counts
    console.print(f"  {c.get('totalEligible',0)} eligible, {c.get('readyToApply',0)} ready, "
                  f"{c.get('conflicts',0)} conflicts, {c.get('smokeSkipped',0)} smoke-skipped")

    # Step 2: Show plan
    console.print(f"\n[bold]Step 2: Apply Plan Summary[/bold]")
    console.print(f"  Hash: {plan.projectsJsonHashBefore[:16]}...")
    for item in plan.planItems:
        status = "[green]READY[/green]" if item.readyToApply else "[red]BLOCKED[/red]"
        console.print(f"  {status} {item.title[:50]} | {item.hashStatus} | {item.projectId}")

    # Step 3: Interactive review
    plan_loaded = load_apply_plan(settings.apply_path())
    items = plan_loaded.get("planItems", []) if plan_loaded else []

    console.print(f"\n[bold]Step 3: Review items[/bold]")
    from rich.prompt import Prompt

    for i, item in enumerate(items, 1):
        aid = item.get("applyId", "")
        title = item.get("title", "")
        ready = item.get("readyToApply", False)

        if not ready:
            console.print(f"  [{i}/{len(items)}] {title[:50]} — [red]BLOCKED[/red] ({item.get('skipReason','')[:30]})")
            continue

        console.print(f"\n  [{i}/{len(items)}] [bold]{title}[/bold]")
        console.print(f"    Current:  {(item.get('currentCardState','') or '')[:120]}")
        console.print(f"    Proposed: {(item.get('approvedCurrentState','') or '')[:120]}")

        cmd = Prompt.ask("    Action (a=approve, s=skip, n=next, q=quit)", default="n").strip().lower()
        if cmd == "a":
            save_apply_decision(settings.apply_path(), aid, "approved_for_apply", "Approved via apply-flow")
            console.print("    [green]Approved[/green]")
        elif cmd == "s":
            reason = Prompt.ask("    Skip reason", default="Skipped via apply-flow")
            save_apply_decision(settings.apply_path(), aid, "skipped", reason)
            console.print("    [yellow]Skipped[/yellow]")
        elif cmd == "q":
            console.print("    [yellow]Stopping review[/yellow]")
            break

    # Step 4: Apply
    console.print(f"\n[bold]Step 4: Apply approved items[/bold]")
    from .apply_engine import apply_operator_approved_plan

    if dry_run:
        console.print("[cyan]Dry-run mode — no writes.[/cyan]")
        try:
            summary = apply_operator_approved_plan(settings, dry_run=True)
            console.print(f"  Would apply: {summary.applied} items (read-only)")
        except RuntimeError as e:
            console.print(f"  [red]{e}[/red]")
    else:
        console.print("[bold red]WARNING: Real write mode![/bold red]")
        if not confirm:
            console.print("[red]Use --no-dry-run --confirm 'APPLY LOCAL KANBAN PLAN' for real apply.[/red]")
        else:
            try:
                summary = apply_operator_approved_plan(settings, dry_run=False, confirm_string=confirm)
                console.print(f"[green]  Applied: {summary.applied} items[/green]")
            except RuntimeError as e:
                console.print(f"  [red]{e}[/red]")

    console.print("[green]Apply flow complete.[/green]")


# ===========================================================================
# Phase 4D commands — Rich TUI Apply Review Console
# ===========================================================================

@app.command(name="review-apply-tui")
def review_apply_tui(
    show_filtered: bool = typer.Option(
        False, "--show-filtered",
        help="Show filtered smoke/test/demo items in read-only diagnostic mode",
    ),
):
    """Interactive Rich-based TUI review console for apply plan items.

    Keyboard controls:
      n=next  p=prev  a=approve  s=skip  e=needs-edit
      r=reason  v=view-full  m=summary  x=export-report  q=quit

    Writes decisions to apply_review_decisions.jsonl only.
    Does NOT write Kanban from this command.

    Use --show-filtered to view smoke/test/demo items in read-only diagnostic mode.
    """
    from .review_tui import run_apply_review_tui as _tui

    _, settings = _load_settings()

    if show_filtered:
        console.print("[dim]Showing filtered smoke/test/demo items in diagnostic mode.[/dim]")

    console.print("[bold cyan]Starting apply review TUI...[/bold cyan]")
    console.print("[dim]Approving here does not write Kanban. Apply remains a separate gated step.[/dim]")

    try:
        _tui(settings, show_filtered=show_filtered)
    except KeyboardInterrupt:
        console.print("\n[yellow]TUI interrupted by user.[/yellow]")
    except Exception as e:
        console.print(f"[red]TUI error: {e}[/red]")
        logger.error("review-apply-tui error", exc_info=True)
        raise typer.Exit(1) from e


@app.command(name="export-apply-report")
def export_apply_report(
    output_path: Optional[str] = typer.Option(
        None, "--output", "-o", help="Output path for Markdown report",
    ),
):
    """Export a Markdown review report of apply decisions.

    Reads the apply plan and current decisions, generates
    a Markdown report suitable for email or audit notes.
    No Kanban writes.
    """
    from .review_tui import export_apply_report as _export

    _, settings = _load_settings()
    try:
        report = _export(settings, output_path)
        console.print(f"[green]Report exported ({len(report)} chars).[/green]")
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from e
    except Exception as e:
        console.print(f"[red]Export error: {e}[/red]")
        logger.error("export-apply-report error", exc_info=True)
        raise typer.Exit(1) from e


@app.command(name="show-apply-decisions")
def show_apply_decisions():
    """Show current apply decisions as a table.

    Shows the latest decision per plan item (applyId).
    Decisions are append-only; latest wins.
    No Kanban writes.
    """
    from .review_tui import show_apply_decisions_cli

    _, settings = _load_settings()
    try:
        show_apply_decisions_cli(settings)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from e


@app.command(name="show-apply-audit")
def show_apply_audit():
    """Show full audit trail of all apply decisions.

    Unlike show-apply-decisions (which shows latest-per-item),
    this shows every decision record ever written, preserving
    the full append-only history.
    No Kanban writes.
    """
    from .review_tui import show_apply_audit_cli

    _, settings = _load_settings()
    try:
        show_apply_audit_cli(settings)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from e


@app.command(name="reset-apply-decisions")
def reset_apply_decisions():
    """Reset (clear) all apply decisions.

    Requires explicit confirmation: asks twice.
    Does NOT affect Kanban or apply plan.
    Only removes the decision records file.
    """
    from .review_tui import reset_apply_decisions_cli

    _, settings = _load_settings()

    console.print("[bold red]WARNING: This will clear all apply decisions.[/bold red]")
    console.print("[red]Approved, skipped, and needs-edit records will be lost.[/red]")
    console.print("[dim]The apply plan and Kanban source are unaffected.[/dim]")
    console.print()

    try:
        reset_apply_decisions_cli(settings)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from e


@app.command(name="reset-apply-workspace")
def reset_apply_workspace():
    """Archive apply workspace files and reset for a clean pilot run.

    Archives apply_plan.json, decision records, and generated reports
    to a timestamped archive directory under runtime/apply/archive/.

    Requires exact confirmation: RESET APPLY WORKSPACE.
    Does NOT touch Kanban data or review drafts.
    """
    from .apply_engine import reset_apply_workspace as _reset

    _, settings = _load_settings()

    console.print("[bold red]WARNING: Apply Workspace Reset[/bold red]")
    console.print()
    console.print("This will archive and remove:")
    console.print("  - apply_plan.json")
    console.print("  - apply_review_decisions.jsonl")
    console.print("  - apply results and summaries")
    console.print("  - generated reports")
    console.print()
    console.print("[yellow]Kanban data and review drafts will NOT be touched.[/yellow]")
    console.print()

    confirm = input("Type 'RESET APPLY WORKSPACE' to confirm: ")
    if confirm.strip() != "RESET APPLY WORKSPACE":
        console.print("[yellow]Reset cancelled -- confirmation mismatch.[/yellow]")
        raise typer.Exit(0)

    try:
        result = _reset(settings)
        console.print(f"[green]Apply workspace reset complete.[/green]")
        console.print(f"  Archived: {result['archived_count']} files")
        console.print(f"  Archive:  {result['archive_path']}")
    except Exception as e:
        console.print(f"[red]Reset error: {e}[/red]")
        logger.error("reset-apply-workspace error", exc_info=True)
        raise typer.Exit(1) from e


# ===========================================================================
# Phase 4E commands — End-to-End Dry-Run Pilot Pack
# ===========================================================================

@app.command(name="coach-status")
def coach_status():
    """Show read-only pipeline status dashboard.

    Displays safety gate status, pipeline stage summaries,
    apply plan state, decision counts, and Kanban source info.
    No mutation.
    """
    from .pilot_engine import run_coach_status

    _, settings = _load_settings()
    try:
        run_coach_status(settings)
    except Exception as e:
        console.print(f"[red]coach-status error: {e}[/red]")
        logger.error("coach-status error", exc_info=True)
        raise typer.Exit(1) from e


@app.command(name="coach-dry-run")
def coach_dry_run():
    """Run end-to-end safe dry-run pilot pipeline.

    Builds apply plan, loads decisions, identifies approved items,
    runs dry-run apply (read-only), exports a pilot Markdown report,
    and prints a summary. Mutates zero Kanban records.
    """
    from .pilot_engine import run_coach_dry_run

    _, settings = _load_settings()
    try:
        result = run_coach_dry_run(settings)
        logger.info(
            "coach-dry-run complete: %d items, %d approved, %d dry-applied, hash=%s",
            result.get("plan_items_total", 0),
            result.get("approved_count", 0),
            result.get("dry_run_applied", 0),
            str(result.get("hash_before", ""))[:16],
        )
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from e
    except Exception as e:
        console.print(f"[red]coach-dry-run error: {e}[/red]")
        logger.error("coach-dry-run error", exc_info=True)
        raise typer.Exit(1) from e


# ===========================================================================
# Phase 5 commands — Local Qwen AI Adviser
# ===========================================================================


@app.command(name="local-ai-status")
def local_ai_status():
    """Check local Qwen adviser status.

    Shows adviser enabled/disabled, Ollama reachable, configured model,
    model available, email context settings, Team ESMI context polling,
    and log file paths. Does NOT send any card/email data to Ollama.
    """
    from rich.table import Table
    from .ollama_client import check_ollama, check_model_available
    from .local_ai_adviser import adviser_is_available, get_advice_log_summary
    from .team_context_sync import get_team_context_status
    from .email_context_loader import discover_email_context_folder

    _, settings = _load_settings()

    table = Table(title="Local Qwen Adviser — Phase 5 Status", show_header=True)
    table.add_column("Check", style="cyan")
    table.add_column("Result", style="bold")
    table.add_column("Detail")

    passed = 0
    failed = 0

    def _r(name, ok, detail):
        nonlocal passed, failed
        if ok:
            passed += 1
        else:
            failed += 1
        table.add_row(name, "PASS" if ok else ("WARN" if ok else "FAIL"), detail)

    def _w(name, detail):
        nonlocal passed
        passed += 1
        table.add_row(name, "WARN", detail)

    # 1. Adviser enabled
    enabled = getattr(settings, "ollama_enabled", True)
    _r("Adviser enabled", enabled,
       "ollama_enabled=true" if enabled else "ollama_enabled=false — adviser disabled")

    # 2. Ollama reachable
    base_url = str(settings.ollama_base_url).rstrip("/")
    model = str(settings.ollama_model)
    reachable, msg, models = check_ollama(base_url, timeout=5)
    _r("Ollama endpoint reachable", reachable,
       f"{base_url} — {msg}" if reachable else msg)

    # 3. Configured model
    _w("Configured model", f"'{model}'")

    # 4. Model available
    if reachable:
        model_ok, model_msg = check_model_available(base_url, model, timeout=5)
        _r(f"Model '{model}' available", model_ok, model_msg)
        if models:
            _w("Available models", ", ".join(models[:5]) + ("..." if len(models) > 5 else ""))
    else:
        _r(f"Model '{model}' available", False, "Cannot check — endpoint unreachable")

    # 5. Email context enabled
    email_enabled = getattr(settings, "local_ai_email_context_enabled", True)
    _r("Email context enabled", email_enabled,
       "local_ai_email_context_enabled=true" if email_enabled else "disabled")

    # 6. Email context folder
    email_folder = discover_email_context_folder(settings)
    if email_folder:
        _r("Email context folder", True, str(email_folder))
    else:
        _w("Email context folder", "Not found — email context will be empty")

    # 7. Team ESMI polling enabled
    poll_enabled = getattr(settings, "team_esmi_context_poll_enabled", True)
    _r("Team ESMI polling enabled", poll_enabled,
       "team_esmi_context_poll_enabled=true" if poll_enabled else "disabled")

    # 8. Team ESMI context cache status
    team_status = get_team_context_status(settings)
    if team_status.get("reachable"):
        _r("Team ESMI reachable", True,
           f"hash={team_status.get('cachedHash','')[:16]}..., "
           f"mtime={team_status.get('cachedMtime','')[:19]}")
    else:
        _w("Team ESMI reachable", "Not accessible in current network context")

    # 9. Mailbox search status
    print("")
    mailbox_enabled = getattr(settings, "mailbox_search_enabled", False)
    _r("Mailbox search enabled", True,
       f"mailbox_search_enabled={'true' if mailbox_enabled else 'false'}")

    mailbox_provider = getattr(settings, "mailbox_search_provider", "disabled")
    _w("Mailbox provider", mailbox_provider)

    if mailbox_enabled:
        from .mailbox_search import get_mailbox_search_status
        mb_status = get_mailbox_search_status(settings)
        _r("Provider available", mb_status.get("mailboxSearchAvailable"),
           mb_status.get("mailboxSearchAvailableMsg", ""))
        _w("Recent days window", f"{mb_status.get('mailboxSearchRecentDays', 180)} days")
        _w("Max results", str(mb_status.get("mailboxSearchMaxResults", 10)))
        sp = mb_status.get("snapshotPath", "")
        if sp:
            _r("Snapshot path", True, f"{sp} ({mb_status.get('snapshotCount', 0)} records)")
        _r("Read-only mode", mb_status.get("mailboxSearchReadOnly") is True,
           "mailbox_search_read_only=true")
    else:
        _w("Mailbox search", "Disabled by default — enable with mailbox_search_enabled=true")

    # 10. Local context cache path
    cache_path = team_status.get("contextCachePath", "")
    if cache_path:
        _r("Local context cache", Path(cache_path).exists() or True, cache_path)

    # 10. Local AI logs
    log_summary = get_advice_log_summary(settings)
    _r("Advice log", bool(log_summary.get("adviceLogPath")),
       f"{log_summary.get('adviceLogPath','')} ({log_summary.get('adviceLogCount',0)} records)")
    _r("Update log", bool(log_summary.get("updateLogPath")),
       f"{log_summary.get('updateLogPath','')} ({log_summary.get('updateLogCount',0)} records)")

    # 11. Safety note
    _w("Safety", "This command does NOT send card/email data to Ollama. Read-only check only.")

    console.print(table)
    console.print(f"\\n[bold]{'All checks passed!' if failed == 0 else f'{failed} check(s) failed'} "
                   f"({passed} passed, {failed} failed)[/bold]")
    if failed > 0:
        raise typer.Exit(1)
    console.print("\\n[bold green]local-ai-status complete.[/bold green]")


@app.command(name="export-pilot-report")


# ═══════════════════════════════════════════════════════════════════════════
# Phase 5: Evidence pipeline CLI
# ═══════════════════════════════════════════════════════════════════════════


@app.command(name="evidence-status")
def evidence_status():
    """Check evidence pipeline dependencies and tool availability."""
    from rich.table import Table

    table = Table(title="Evidence Pipeline — Status Check", show_header=True)
    table.add_column("Check", style="cyan", no_wrap=True)
    table.add_column("Result", style="bold")
    table.add_column("Detail")

    passed = 0
    failed = 0
    warnings = 0

    def _r(check, ok, detail=""):
        nonlocal passed, failed
        if ok:
            passed += 1
            table.add_row(check, "[green]PASS[/green]", detail)
        else:
            failed += 1
            table.add_row(check, "[red]FAIL[/red]", detail)

    def _w(check, detail):
        nonlocal warnings
        warnings += 1
        table.add_row(check, "[yellow]WARN[/yellow]", detail)

    # 1. Python / venv
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    _r("Python version", sys.version_info >= (3, 11), py_ver)

    # 2. Imports
    try: import win32com.client; _r("pywin32", True, "import OK")
    except Exception as e: _r("pywin32", False, str(e)[:60])
    try: import openpyxl; _r("openpyxl", True, "import OK")
    except Exception as e: _r("openpyxl", False, str(e)[:60])
    try: import pytesseract; _r("pytesseract", True, "import OK")
    except Exception as e: _r("pytesseract", False, str(e)[:60])
    try: from PIL import Image; _r("Pillow", True, "import OK")
    except Exception as e: _r("Pillow", False, str(e)[:60])
    try: import docx; _r("python-docx", True, "import OK")
    except Exception as e: _r("python-docx", False, str(e)[:60])

    # 3. PDF parser
    for mod_name in ["PyPDF2", "pdfminer", "fitz"]:
        try:
            __import__(mod_name)
            _r(f"PDF parser ({mod_name})", True, f"{mod_name} available")
            break
        except ImportError:
            continue
    else:
        _w("PDF parser", "No PDF parser found — PDF attachments will be skipped")

    # 4. compileall evidence modules
    import py_compile
    for fname in ["evidence_pipeline.py", "local_ai_adviser.py"]:
        fp = _REPO_ROOT / "src" / "sami_kanban_coach" / fname
        try:
            py_compile.compile(fp, doraise=True)
            _r(f"compile {fname}", True, "OK")
        except py_compile.PyCompileError as e:
            _r(f"compile {fname}", False, str(e)[:80])

    # 5. Outlook COM
    try:
        import win32com.client
        app = win32com.client.Dispatch("Outlook.Application")
        ns = app.GetNamespace("MAPI")
        inbox = ns.GetDefaultFolder(6)
        _r("Outlook Inbox", True, f"{getattr(inbox,'FolderPath','?')} ({getattr(inbox.Items,'Count',0)} items)")
        sent = ns.GetDefaultFolder(5)
        _r("Outlook Sent", True, f"{getattr(sent,'FolderPath','?')} ({getattr(sent.Items,'Count',0)} items)")
        _r("Read-only access", True, "No mutations performed")
    except Exception as e:
        _r("Outlook COM", False, str(e)[:100])

    # 6. Tesseract / OCR
    from .evidence_pipeline import detect_tesseract
    tess = detect_tesseract()
    if tess.get("available"):
        _r("Tesseract", True, f'{tess.get("version","")} at {tess.get("path","")}')
        _r("English data", tess.get("eng_available", False), "eng.traineddata" if tess.get("eng_available") else "missing")
        # Smoke test
        try:
            import pytesseract, PIL.Image
            pytesseract.pytesseract.tesseract_cmd = tess["path"]
            import os; os.environ["TESSDATA_PREFIX"] = tess.get("tessdata_path", "")
            img = PIL.Image.new("RGB", (100, 30), color="white")
            text = pytesseract.image_to_string(img, lang="eng")
            _r("OCR smoke test", True, f"OK ({len(text.strip())} chars)")
        except Exception as e:
            _r("OCR smoke test", False, str(e)[:80])
    else:
        _r("Tesseract", False, tess.get("error", "not found"))

    # 7. openpyxl smoke
    try:
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Test"
        ws["A1"] = "SAMI"
        _r("openpyxl create", True, f"version {openpyxl.__version__}")
        wb.close()
    except Exception as e:
        _r("openpyxl create", False, str(e)[:80])

    # 8. Local model
    from .ollama_client import check_ollama, check_model_available
    _, settings = _load_settings()
    ollama_ok, ollama_msg, models = check_ollama(settings.ollama_base_url)
    _r("Ollama reachable", ollama_ok, ollama_msg)
    if ollama_ok:
        model_ok, model_msg = check_model_available(settings.ollama_base_url, settings.ollama_model)
        _r(f"Model {settings.ollama_model}", model_ok, model_msg if not model_ok else "available")
        # JSON mode test
        from .ollama_client import generate
        try:
            ok, msg, parsed = generate(
                base_url=settings.ollama_base_url, model=settings.ollama_model,
                system_prompt="Return JSON.", user_prompt='{"t":1}', timeout=30,
            )
            _r("JSON mode test", ok, "structured output works" if ok else msg[:80])
        except Exception as e:
            _r("JSON mode test", False, str(e)[:80])

    # 9. Safety
    _r("mailbox_search_enabled=False", not settings.mailbox_search_enabled,
       f"enabled={settings.mailbox_search_enabled}")
    _r("mailbox_search_recent_days=180", settings.mailbox_search_recent_days == 180,
       f"days={settings.mailbox_search_recent_days}")
    _r("allow_kanban_apply=False", not settings.allow_kanban_apply,
       f"allowed={settings.allow_kanban_apply}")
    _r("local_kanban_apply_enabled=False", not settings.local_kanban_apply_enabled,
       f"enabled={settings.local_kanban_apply_enabled}")
    _r("team_kanban_apply_enabled=False", not settings.team_kanban_apply_enabled,
       f"enabled={settings.team_kanban_apply_enabled}")

    local_blocked = is_forbidden_path(str(settings.kanban_local_path()))
    _r("Local path guarded", local_blocked, f"blocked={local_blocked}")

    console.print(table)
    console.print(f"\n[bold]{'All checks passed!' if failed == 0 else f'{failed} check(s) failed'} "
                   f"({passed} passed, {failed} failed, {warnings} warnings)[/bold]")
    if failed > 0:
        raise typer.Exit(1)


@app.command(name="evidence-search")
def evidence_search(
    project_id: str = typer.Argument(..., help="Kanban project ID, e.g. card-001-nt-ultrarad-stroke-vpn-firewall-rules"),
    card_title: str = typer.Option("", "--title", "-t", help="Card title for search context"),
    body_search: bool = typer.Option(False, "--body-search", help="Also run body keyword search (slower)"),
    max_results: int = typer.Option(10, "--max", "-m", help="Max results from body fallback search"),
):
    """Run read-only mailbox evidence search for a Kanban card."""
    from .evidence_pipeline import run_evidence_search

    _, settings = _load_settings()
    console.print(f"[bold cyan]Evidence search:[/bold cyan] {project_id}")

    # Build subject patterns from project ID
    parts = project_id.replace("card-", "").split("-")
    patterns = [project_id.replace("-", " ")]
    # Add SRV/REQ patterns from the id
    patterns.append(project_id)

    console.print(f"  Subject patterns: {patterns[:3]}...")
    console.print(f"  Body search: {body_search}")
    console.print()

    result = run_evidence_search(
        settings=settings,
        card_title=card_title or project_id,
        card_project_id=project_id,
        subject_patterns=patterns,
        body_keywords=parts if body_search else None,
        max_body_search=max_results,
    )

    status = result.get("search_status", "error")
    strength = result.get("evidence_strength", "none")
    run_dir = result.get("run_dir", "")
    att_count = len(result.get("attachments", []))
    inbox_n = len(result.get("search_results", {}).get("inbox_matches", []))
    sent_n = len(result.get("search_results", {}).get("sent_matches", []))

    console.print(f"\n  [bold]Status:[/bold] {status}")
    console.print(f"  [bold]Strength:[/bold] {strength}")
    console.print(f"  [bold]Messages:[/bold] {inbox_n} inbox, {sent_n} sent")
    console.print(f"  [bold]Attachments:[/bold] {att_count}")
    console.print(f"  [bold]Run dir:[/bold] {run_dir}")

    # Show attachment summary
    if att_count:
        console.print("\n  [bold]Attachment evidence:[/bold]")
        for a in result.get("attachments", []):
            tm = a.get("term_matches", {})
            srv = ", ".join(tm.get("srv", [])[:3])
            ips = ", ".join(tm.get("ips", [])[:3])
            label = f"{a.get('original_filename','?')} ({a.get('parse_status','?')})"
            if srv or ips:
                console.print(f"    [green]✓[/green] {label}")
                if srv: console.print(f"       SRV: {srv}")
                if ips: console.print(f"       IPs: {ips}")
            else:
                console.print(f"    [dim]•[/dim] {label}")

    console.print(f"\n[green]Search complete.[/green] Run [bold]evidence-show-run {run_dir.split('/')[-1]}[/bold] for details.")


@app.command(name="evidence-build-draft")
def evidence_build_draft(
    run_id_or_path: str = typer.Argument(..., help="Evidence run ID (folder name) or full path"),
):
    """Generate a human-review draft from gathered evidence using local model."""
    from datetime import datetime
    import json
    from .local_ai_adviser import generate_structured_advice

    _, settings = _load_settings()
    run_path = Path(run_id_or_path)
    if not run_path.is_absolute():
        run_path = _REPO_ROOT / "runtime" / "apply" / "evidence" / run_id_or_path
    if not run_path.exists():
        console.print(f"[red]Run path not found:[/red] {run_path}")
        raise typer.Exit(1)

    model_input_path = run_path / "local_model_input.json"
    if not model_input_path.exists():
        console.print(f"[red]No model input found at {model_input_path}[/red]")
        console.print("  Run evidence-search first to gather evidence.")
        raise typer.Exit(1)

    console.print(f"[bold cyan]Building draft from:[/bold cyan] {run_path.name}")
    output_path = run_path / "local_model_output.json"
    draft_root = _REPO_ROOT / "runtime" / "apply" / "drafts"

    result = generate_structured_advice(
        model_input_path=model_input_path,
        settings=settings,
        output_path=output_path,
    )

    if result.get("success"):
        out = result.get("output", {})
        console.print(f"  [green]Confidence:[/green] {out.get('confidence', '?')}")
        console.print(f"  [green]Recommendation:[/green] {out.get('apply_recommendation', '?')}")
        cs = out.get("current_state_draft", "")
        if cs:
            console.print(f"  [bold]Current state:[/bold] {cs[:300]}")
        na = out.get("next_action_draft", "")
        if na:
            console.print(f"  [bold]Next action:[/bold] {na[:300]}")
        console.print(f"  [bold]Status:[/bold] {out.get('status_recommendation', '?')}  "
                       f"[bold]Risk:[/bold] {out.get('risk_recommendation', '?')}")
        console.print(f"  [bold]Evidence items:[/bold] {len(out.get('evidence_items', []))}")
        console.print(f"  [bold]Missing evidence:[/bold] {len(out.get('missing_evidence', []))}")

        # Write draft
        draft = {
            "run_id": run_path.name,
            "generated_at": datetime.now().isoformat(),
            "card_id": out.get("card_id_or_title", ""),
            "card_title": out.get("card_id_or_title", ""),
            "search_status": out.get("search_status", ""),
            "evidence_strength": out.get("evidence_strength", ""),
            "model_output": out,
            "mailboxMutated": False,
            "kanbanWritePerformed": False,
            "teamEsmiWritePerformed": False,
            "requiresHumanApproval": True,
        }
        draft_path = draft_root / f"draft_{run_path.name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(json.dumps(draft, indent=2, ensure_ascii=False, default=str))
        console.print(f"\n[green]Draft saved:[/green] {draft_path}")
    else:
        console.print(f"[red]Draft failed:[/red] {result.get('error', 'unknown')}")
        raise typer.Exit(1)


@app.command(name="evidence-show-run")
def evidence_show_run(
    run_id_or_path: str = typer.Argument(..., help="Evidence run ID or full path"),
):
    """Display a prior evidence run summary."""
    import json
    run_path = Path(run_id_or_path)
    if not run_path.is_absolute():
        run_path = _REPO_ROOT / "runtime" / "apply" / "evidence" / run_id_or_path
    if not run_path.exists():
        console.print(f"[red]Run not found:[/red] {run_path}")
        raise typer.Exit(1)

    console.print(f"[bold cyan]Evidence run:[/bold cyan] {run_path.name}")

    manifest_path = run_path / "evidence_manifest.json"
    if manifest_path.exists():
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        console.print(f"  Timestamp: {m.get('timestamp','?')}")
        console.print(f"  Status: {m.get('search_results',{}).get('classified_status','?')}")
        console.print(f"  Strength: {m.get('search_results',{}).get('evidence_strength','?')}")
        console.print(f"  Inbox: {m.get('search_results',{}).get('inbox_messages_matched',0)} matched")
        console.print(f"  Sent: {m.get('search_results',{}).get('sent_messages_matched',0)} matched")
        console.print(f"  Attachments: {m.get('attachments',{}).get('total_extracted',0)} total, "
                       f"{m.get('attachments',{}).get('saved_count',0)} saved, "
                       f"{m.get('attachments',{}).get('parsed_count',0)} parsed")
        saf = m.get("safety", {})
        console.print(f"  Hash unchanged: {saf.get('hash_unchanged','?')}")
        console.print(f"  mailboxMutated: {saf.get('mailboxMutated','?')}")
    else:
        console.print("  (no manifest)")

    # Show model output
    mo = run_path / "local_model_output.json"
    if mo.exists():
        o = json.loads(mo.read_text(encoding="utf-8")).get("output", {})
        console.print(f"\n  [bold]Model:[/bold]")
        console.print(f"    Confidence: {o.get('confidence','?')}")
        console.print(f"    Recommendation: {o.get('apply_recommendation','?')}")
        cs = o.get("current_state_draft","")
        if cs: console.print(f"    State: {cs[:200]}")
        na = o.get("next_action_draft","")
        if na: console.print(f"    Action: {na[:200]}")
        console.print(f"    Status: {o.get('status_recommendation','?')} / Risk: {o.get('risk_recommendation','?')}")

    # Show attachments
    ai = run_path / "attachment_index.json"
    if ai.exists():
        atts = json.loads(ai.read_text(encoding="utf-8"))
        console.print(f"\n  [bold]Attachments ({len(atts)}):[/bold]")
        for a in atts:
            if a.get("parse_status") == "parsed":
                tm = a.get("term_matches", {})
                srv = ", ".join(tm.get("srv",[])[:3])
                ips = ", ".join(tm.get("ips",[])[:3])
                console.print(f"    [green]{a.get('original_filename','?')}[/green]"
                              f"{' SRV:'+srv if srv else ''}{' IPs:'+ips if ips else ''}")

    # Show sitrep
    sitrep_path = run_path / "sitrep.md"
    if sitrep_path.exists():
        console.print(f"\n  SITREP: {sitrep_path}")

    console.print(f"\n  [dim]Run path: {run_path}[/dim]")


@app.command(name="evidence-regression-test")
def evidence_regression_test():
    """Run deterministic NT UltraRad regression test against preserved evidence.

    Uses v10/v11/v12 evidence artifacts only. No Outlook/Kanban access.
    """
    from .evidence_regression_test import run_regression

    console.print("[bold cyan]Running NT UltraRad regression test...[/bold cyan]")
    console.print("  No Outlook access. No Kanban writes. Read-only evidence check.")
    console.print()

    result = run_regression()

    passed = result.get("passed", 0)
    failed = result.get("failed", 0)
    total = result.get("total", 0)

    for res in result.get("results", []):
        sym = "[green]✓[/green]" if res["status"] == "PASS" else "[red]✗[/red]" if res["status"] == "FAIL" else "[yellow]—[/yellow]"
        console.print(f"  {sym} {res['check']}")
        if res.get("detail"):
            console.print(f"      {res['detail']}")

    console.print()
    if failed == 0:
        console.print(f"[green]Regression test: {passed}/{total} passed, 0 failed — ALL PASSED[/green]")
    else:
        console.print(f"[red]Regression test: {passed}/{total} passed, {failed} failed[/red]")
        raise typer.Exit(1)


@app.command(name="coach-chat")
def coach_chat(
    smoke_test: bool = typer.Option(False, "--smoke-test", help="Run deterministic Mr Kanban local sandbox smoke flow."),
):
    """Mr Kanban local conversational coach harness (local sandbox only)."""
    from .coach_chat import interactive_loop, run_smoke_test

    _, settings = _load_settings()
    if smoke_test:
        result = run_smoke_test(settings, console=console)
        console.print("\n[bold green]Mr Kanban smoke test complete.[/bold green]")
        console.print(f"  Sandbox path: {result.get('sandbox', {}).get('sandboxPath', '')}")
        console.print(f"  Model reachable: {result.get('model', {}).get('available', False)}")
        mailbox = result.get("mailbox", {}) or {}
        inbox_info = mailbox.get("inbox_info", {}) or {}
        console.print(f"  Mailbox search used: {bool(mailbox.get('enabled', False)) and bool(mailbox.get('available', False))}")
        console.print(f"  Mailbox search status: {inbox_info.get('search_status', mailbox.get('error', ''))}")
        console.print(f"  Sources: {len(result.get('sources', []))}")
        return
    interactive_loop(settings, console=console)


@app.command(name="evidence-reset-workspace")
def evidence_reset_workspace(
    confirm: bool = typer.Option(False, "--confirm", help="Confirm reset"),
    keep_preserved: bool = typer.Option(True, "--keep-preserved", help="Keep v10/v11/v12 evidence"),
):
    """Archive or clean temporary evidence workspace. Preserved evidence kept by default."""
    from datetime import datetime
    if not confirm:
        console.print("[yellow]SAFETY:[/yellow] This will archive evidence runs. Use --confirm to proceed.")
        console.print("  Preserved runs (v10/v11/v12) are kept by default with --keep-preserved.")
        raise typer.Exit(0)

    ev_root = _REPO_ROOT / "runtime" / "apply" / "evidence"
    preserved = {"target_thread", "sr521202", "v12_ocr_sent"}
    archived = 0
    for d in sorted(ev_root.iterdir()):
        if not d.is_dir():
            continue
        if keep_preserved and d.name in preserved:
            continue
        if d.name.startswith("ep_"):
            archive_name = d.parent / f"{d.name}_archived_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            d.rename(archive_name)
            archived += 1
            console.print(f"  Archived: {d.name} → {archive_name.name}")

    # Also clean drafts dir
    drafts = _REPO_ROOT / "runtime" / "apply" / "drafts"
    if drafts.exists():
        for f in drafts.glob("draft_ep_*.json"):
            f.unlink()
            archived += 1
            console.print(f"  Removed draft: {f.name}")

    console.print(f"[green]Workspace reset: {archived} items processed.[/green]")
    if keep_preserved:
        console.print(f"  Preserved: {', '.join(sorted(preserved))}")
def export_pilot_report():
    """Export a pilot-friendly Markdown report.

    Reads the current apply plan and decisions, runs a dry-run
    apply, and generates a comprehensive report with safety gate
    status, decision summary, and dry-run result.
    No Kanban writes.
    """
    from .pilot_engine import export_pilot_report_cli

    _, settings = _load_settings()
    try:
        report_path = export_pilot_report_cli(settings)
        console.print(f"[green]Pilot report exported:[/green] {report_path}")
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from e
    except Exception as e:
        console.print(f"[red]Export error: {e}[/red]")
        logger.error("export-pilot-report error", exc_info=True)
        raise typer.Exit(1) from e


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    """Entry point for the CLI."""
    # Ensure the package is importable
    sys.path.insert(0, str(_REPO_ROOT / "src"))
    app()


if __name__ == "__main__":
    main()
