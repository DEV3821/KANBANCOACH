# SAMI Kanban Coach — Phase 0: Outlook Email Recall Cache

A local Windows tool that captures Outlook classic COM emails into a C drive recall cache for later Kanban/Qwen comparison.

## Purpose

Build a trusted, locally-stored evidence cache of emails from a nominated Outlook folder. Future phases will compare current Kanban card state + recent email evidence + files to suggest card updates for human approval.

## Phase 0 Scope

**What Phase 0 Does:**
- Checks your Outlook environment (classic COM) is available.
- Captures selected Outlook emails to local evidence files.
- Captures all recent emails from a configured Outlook folder.
- Runs a live watcher that detects new emails via COM events + polling fallback.
- Saves full email evidence (JSON, body text, HTML, headers, .msg).
- Deduplicates by Internet Message ID.
- Appends JSONL recall records.
- Never modifies, moves, or marks emails.

**What Phase 0 Does NOT Do:**
- No Kanban card reading or writing.
- No Ollama/Qwen calls.
- No GUI/TUI.
- No network path writes (Team ESMI / SAMI-Kanban-WorkServer are forbidden).
- No email modifications of any kind.

## Requirements

- **Windows 10+** with **Microsoft 365 classic Outlook** installed and open.
- **Python 3.11+**
- Classic Outlook COM automation (New Outlook / web Outlook not supported).

## How to Install

1. Open a terminal in the repo root:
   ```
   C:\Tools\SAMI Kanban Coach>
   ```
2. Run the install script:
   ```
   scripts\install_requirements.bat
   ```
   This creates a `.venv`, installs `pywin32`, `typer`, `rich`, `pydantic`, and runs the doctor check.

Or manually:

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## How to Create the Kanban Intake Folder

In Outlook, create a folder named `Kanban Intake` (or configure a different path in `config\settings.json`). This is the folder the watcher monitors.

## How to Run Doctor

Check your Outlook COM setup before doing anything else:

```
scripts\run_doctor.bat
```

Or directly:

```
python -m sami_kanban_coach.cli doctor
```

## How to Export Selected Messages

Select one or more emails in Outlook, then run:

```
scripts\run_export_selected.bat
```

Or directly:

```
python -m sami_kanban_coach.cli export-selected
```

## How to Export a Folder

Export all recent emails from the configured folder:

```
scripts\run_export_folder.bat
```

Or directly (customise hours and max items):

```
python -m sami_kanban_coach.cli export-folder --since-hours 48 --max-items 100
```

## How to Run Live Watcher

Monitor the configured folder continuously for new emails:

```
scripts\run_live_watcher.bat
```

Or directly:

```
python -m sami_kanban_coach.cli live-watch --poll-seconds 60
```

Press **Ctrl+C** to stop.

## Where Evidence Files Are Stored

All email data is under `runtime\email_recall\`:

```
runtime\email_recall\
  data\
    raw_email_recall.jsonl     — Append-only JSONL log
    processed_ids.json         — Deduplication key store
  evidence\
    emails\YYYY-MM-DD\
      <safe-subject-hash>\
        email.json             — Full metadata
        body.txt               — Plain text body
        body.html              — HTML body (if enabled)
        headers.txt            — Transport headers (if enabled)
        original.msg           — MSG copy (if enabled)
    attachments\YYYY-MM-DD\
      <messageKey>\            — Saved attachments (if enabled)
  logs\
    email_recall.log           — Application log
```

## Safety Limitations

- The doctor command never modifies Outlook, mail, or Kanban files.
- Export commands are read-only: no mark-read, move, delete, archive, flag, or send.
- The live watcher is read-only: it captures and logs only.
- Explicit guardrails prevent writes to `C:\Tools\SAMI-Kanban-WorkServer` or `\\fusafmcf01\Medical Imaging\Team_ESMI\Program Delivery\SAMI-Kanban-WorkServer`.
- Classic Outlook must be running; New Outlook (olk.exe) does not support COM automation.

## Next Phases

- **Phase 1:** Kanban state polling (read-only).
- **Phase 2:** Email-to-card matching.
- **Phase 3:** Qwen comparison and draft suggestions.
- **Phase 4:** Human review and apply workflow.
