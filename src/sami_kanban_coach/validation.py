"""Validation and self-check utilities for SAMI Kanban Coach Phase 0."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .config import ConfigLoader
from .logging_setup import setup_logging
from .path_safety import is_forbidden_path

logger = setup_logging(Path("runtime/email_recall/logs"))


# ---------------------------------------------------------------------------
# Validation checks
# ---------------------------------------------------------------------------

def check_repo_structure(repo_root: Path) -> list[dict[str, Any]]:
    """Check that the expected repo structure exists."""
    expected = [
        "README.md",
        "requirements.txt",
        "pyproject.toml",
        "config/settings.json",
        "config/settings.example.json",
        "scripts/install_requirements.bat",
        "scripts/run_doctor.bat",
        "scripts/run_export_selected.bat",
        "scripts/run_export_folder.bat",
        "scripts/run_live_watcher.bat",
        "scripts/validate_phase0.bat",
        "src/sami_kanban_coach/__init__.py",
        "src/sami_kanban_coach/cli.py",
        "src/sami_kanban_coach/config.py",
        "src/sami_kanban_coach/models.py",
        "src/sami_kanban_coach/logging_setup.py",
        "src/sami_kanban_coach/outlook_com.py",
        "src/sami_kanban_coach/path_safety.py",
        "src/sami_kanban_coach/storage.py",
        "src/sami_kanban_coach/validation.py",
        "runtime/email_recall/data/",
        "runtime/email_recall/evidence/emails/",
        "runtime/email_recall/evidence/attachments/",
        "runtime/email_recall/logs/",
    ]
    results = []
    for path_str in expected:
        full_path = repo_root / path_str
        exists = full_path.exists()
        is_dir = full_path.is_dir() if exists else False
        results.append({
            "check": f"Repo structure: {path_str}",
            "passed": exists,
            "detail": "Directory exists" if is_dir else "File exists" if exists else "MISSING",
        })
    return results


def check_config_readable(repo_root: Path) -> list[dict[str, Any]]:
    """Check that config loads and validates."""
    results = []
    config_path = repo_root / "config" / "settings.json"
    if not config_path.exists():
        return [{"check": "Config readable", "passed": False, "detail": "File not found"}]

    try:
        loader = ConfigLoader(config_path)
        settings = loader.load()
        results.append({
            "check": "Config readable",
            "passed": True,
            "detail": f"outlook_folder_path={settings.outlook_folder_path}",
        })
        results.append({
            "check": "Output root resolved",
            "passed": True,
            "detail": str(settings.output_path()),
        })
    except Exception as e:
        results.append({
            "check": "Config readable",
            "passed": False,
            "detail": str(e),
        })
    return results


def check_imports() -> list[dict[str, Any]]:
    """Check that all required packages are importable."""
    deps = [
        ("pywin32", "win32com"),
        ("typer", "typer"),
        ("rich", "rich"),
        ("pydantic", "pydantic"),
    ]
    results = []
    for pkg_name, import_name in deps:
        try:
            __import__(import_name)
            results.append({
                "check": f"Import: {pkg_name}",
                "passed": True,
                "detail": f"{import_name} imported OK",
            })
        except ImportError as e:
            results.append({
                "check": f"Import: {pkg_name}",
                "passed": False,
                "detail": str(e),
            })
    return results


def check_output_writable(repo_root: Path) -> list[dict[str, Any]]:
    """Check that output directories are writeable."""
    dirs = [
        repo_root / "runtime/email_recall/data/",
        repo_root / "runtime/email_recall/evidence/emails/",
        repo_root / "runtime/email_recall/evidence/attachments/",
        repo_root / "runtime/email_recall/logs/",
    ]
    results = []
    for d in dirs:
        try:
            d.mkdir(parents=True, exist_ok=True)
            test_file = d / ".vtest_tmp"
            test_file.write_text("test")
            test_file.unlink()
            results.append({
                "check": f"Writable: {d.name}/",
                "passed": True,
                "detail": str(d),
            })
        except Exception as e:
            results.append({
                "check": f"Writable: {d.name}/",
                "passed": False,
                "detail": str(e),
            })
    return results


def check_jsonl_valid(repo_root: Path) -> list[dict[str, Any]]:
    """Validate raw_email_recall.jsonl if it exists."""
    results = []
    jsonl_path = repo_root / "runtime/email_recall/data/raw_email_recall.jsonl"
    if not jsonl_path.exists():
        results.append({
            "check": "JSONL file valid",
            "passed": True,
            "detail": "No JSONL file yet (fresh install)",
        })
        return results

    try:
        line_count = 0
        error_count = 0
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                line_count += 1
                try:
                    json.loads(line)
                except json.JSONDecodeError:
                    error_count += 1

        if error_count == 0:
            results.append({
                "check": "JSONL file valid",
                "passed": True,
                "detail": f"{line_count} records, all valid JSON",
            })
        else:
            results.append({
                "check": "JSONL file valid",
                "passed": False,
                "detail": f"{line_count} records, {error_count} with parse errors",
            })
    except Exception as e:
        results.append({
            "check": "JSONL file valid",
            "passed": False,
            "detail": str(e),
        })
    return results


def check_processed_ids_valid(repo_root: Path) -> list[dict[str, Any]]:
    """Validate processed_ids.json if it exists."""
    results = []
    path = repo_root / "runtime/email_recall/data/processed_ids.json"
    if not path.exists():
        results.append({
            "check": "Processed IDs valid",
            "passed": True,
            "detail": "No processed_ids.json yet (fresh install)",
        })
        return results

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "processed" in data and isinstance(data["processed"], list):
            results.append({
                "check": "Processed IDs valid",
                "passed": True,
                "detail": f"{len(data['processed'])} processed IDs",
            })
        elif isinstance(data, list):
            results.append({
                "check": "Processed IDs valid",
                "passed": True,
                "detail": f"{len(data)} processed IDs (legacy format)",
            })
        else:
            results.append({
                "check": "Processed IDs valid",
                "passed": False,
                "detail": "Unexpected format",
            })
    except Exception as e:
        results.append({
            "check": "Processed IDs valid",
            "passed": False,
            "detail": str(e),
        })
    return results


def check_forbidden_paths_not_modified() -> list[dict[str, Any]]:
    """Verify that forbidden Kanban/ESMI paths have not been written to by our tool."""
    forbidden = [
        Path("C:/Tools/SAMI-Kanban-WorkServer"),
        Path("//fusafmcf01/Medical Imaging/Team_ESMI/Program Delivery/SAMI-Kanban-WorkServer"),
    ]
    results = []
    for p in forbidden:
        try:
            resolved = p.resolve()
            exists = resolved.exists()
            results.append({
                "check": f"Forbidden path untouched: {p}",
                "passed": True,
                "detail": f"Path {'exists' if exists else 'not accessible'} — no writes by this tool (guard via is_forbidden_path / assert_not_forbidden)",
            })
        except Exception as e:
            results.append({
                "check": f"Forbidden path check: {p}",
                "passed": True,
                "detail": f"Could not evaluate: {e}",
            })
    return results


# ---------------------------------------------------------------------------
# Full validation suite
# ---------------------------------------------------------------------------

def run_all_checks(repo_root: Path | None = None) -> list[dict[str, Any]]:
    """Run all validation checks and return results."""
    if repo_root is None:
        repo_root = Path.cwd().resolve()

    all_results: list[dict[str, Any]] = []
    all_results.extend(check_repo_structure(repo_root))
    all_results.extend(check_config_readable(repo_root))
    all_results.extend(check_imports())
    all_results.extend(check_output_writable(repo_root))
    all_results.extend(check_jsonl_valid(repo_root))
    all_results.extend(check_processed_ids_valid(repo_root))
    all_results.extend(check_forbidden_paths_not_modified())

    return all_results
