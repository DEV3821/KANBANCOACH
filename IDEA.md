# SAMI Kanban Coach — Project Goal, System Instructions, and Technical Specification

## 1. Project purpose

SAMI Kanban Coach is a local Windows assistant for keeping the SAMI Project Portfolio Kanban accurate, current, and evidence-backed.

The long-term goal is:

Current Kanban card state
+ recent Outlook email evidence
+ dropped/project files
+ project folder contents
+ card update history
= suggested daily Kanban updates for human review and approval.

The system must behave as a governance assistant, not an autonomous bot.

Core rule:

AI drafts.
Human approves.
Kanban saves.

The app must never silently update the Kanban based on AI output.

## 2. Current repo

Repo root:

C:\Tools\SAMI Kanban Coach

This is a fresh repo.

This repo is separate from the existing Kanban web app repo.

Do not modify the Kanban repo during Phase 0.

Forbidden paths for Phase 0:

C:\Tools\SAMI-Kanban-WorkServer

\\fusafmcf01\Medical Imaging\Team_ESMI\Program Delivery\SAMI-Kanban-WorkServer

Phase 0 must not write to either of those paths.

## 3. Current build phase

We are building Phase 0 only.

Phase 0 is the Outlook email copy-out / recall foundation.

Phase 0 objective:

Classic Outlook desktop
→ Outlook COM watcher/exporter
→ local C drive recall cache
→ full email JSON/evidence files
→ no Kanban writes
→ no Qwen/Ollama yet.

Phase 0 must create a reliable local evidence cache of relevant emails. Later phases will compare this email cache against live Kanban card state.

## 4. Future phases for context only

Do not build these yet unless explicitly instructed later.

### Phase 1 — Kanban state polling

Read current Kanban files:

C:\Tools\SAMI-Kanban-WorkServer\data\projects.json
C:\Tools\SAMI-Kanban-WorkServer\data\card_updates.jsonl

Build local card index:

C:\Tools\SAMI Kanban Coach\runtime\kanban_index\card_index.jsonl

Purpose:
The app always knows the current board state before suggesting changes.

### Phase 2 — Email-to-card matching

Use captured email JSON/evidence to identify likely related Kanban cards.

Example:

Email mentions monitor tender specs.
Existing card is SAMI Diagnostic Monitor Replacement.
System links email to that card with confidence and evidence.

### Phase 3 — Qwen/Ollama comparison drafts

Use local Ollama/Qwen to compare:

current card state
against
new email/file evidence

and decide whether there is a material update.

The model should not merely summarise emails. It must answer:

Given the current card state and this new evidence, has the real project state changed enough to update the card?

### Phase 4 — Human review/apply

Interactive CLI/TUI review.

User can:

Approve
Edit
Skip
Create possible new card draft
Copy evidence
Open file/folder

Only approved updates may write to Kanban JSON.

### Phase 5 — File intake and recall

Allow dropped files to be routed into Kanban project folders.

Allow project folder search/indexing.

Allow copying project files out locally for working copies.

Do not build this in Phase 0.

## 5. Non-negotiable safety rules

During Phase 0, the app must never:

- Modify Outlook messages.
- Move Outlook messages.
- Mark Outlook messages as read.
- Send email.
- Delete email.
- Archive email.
- Flag email.
- Categorise email.
- Create Outlook folders.
- Modify Kanban JSON.
- Modify Team ESMI files.
- Call Ollama.
- Call Qwen.
- Summarise or interpret emails using AI.
- Upload anything externally.
- Use Microsoft Graph.
- Require admin tenant approval.

Phase 0 is read/copy only.

The only allowed writes are under:

C:\Tools\SAMI Kanban Coach\runtime\email_recall

and normal repo files created under:

C:\Tools\SAMI Kanban Coach

## 6. Outlook requirement

Phase 0 uses classic Outlook desktop COM automation.

The user will keep classic Outlook open during work hours.

New Outlook is not supported for Phase 0 because classic COM automation is required.

The system must include a doctor command that checks:

- Is classic Outlook running?
- Is new Outlook likely running?
- Can Python/pywin32 dispatch Outlook.Application?
- Can MAPI namespace be opened?
- What Outlook version/build is reported?
- What profile/store roots are visible?
- Can the configured folder be resolved?

If only new Outlook appears to be running or COM dispatch fails, show a clear message:

New Outlook does not support the classic Outlook COM automation required by this Phase 0 watcher. Use classic Outlook.

Do not attempt Microsoft Graph in Phase 0.

## 7. Target local email recall root

All captured email evidence must go under:

C:\Tools\SAMI Kanban Coach\runtime\email_recall

Required runtime layout:

C:\Tools\SAMI Kanban Coach\
  README.md
  requirements.txt
  pyproject.toml
  config\
    settings.example.json
    settings.json
  runtime\
    email_recall\
      data\
        raw_email_recall.jsonl
        processed_ids.json
      evidence\
        emails\
      attachments\
      logs\
        email_recall.log
  scripts\
    install_requirements.bat
    run_doctor.bat
    run_export_selected.bat
    run_export_folder.bat
    run_live_watcher.bat
    validate_phase0.bat
  src\
    sami_kanban_coach\
      __init__.py
      cli.py
      outlook_com.py
      storage.py
      models.py
      config.py
      logging_setup.py
      path_safety.py
      validation.py

## 8. Technology stack

Use:

- Python 3.11+
- pywin32
- typer
- rich
- pydantic
- pathlib
- standard library logging
- atomic file writes where appropriate

requirements.txt:

pywin32
typer
rich
pydantic

Use Typer for commands.

Use Rich for readable tables/status output.

Use pydantic for schema validation where helpful.

Use pathlib everywhere.

The repo path contains spaces:

C:\Tools\SAMI Kanban Coach

All scripts and code must handle this safely.

## 9. CLI commands

Required commands:

doctor

python -m sami_kanban_coach.cli doctor

export-selected

python -m sami_kanban_coach.cli export-selected

export-folder

python -m sami_kanban_coach.cli export-folder --since-hours 48 --max-items 100

live-watch

python -m sami_kanban_coach.cli live-watch --poll-seconds 60

validate-phase0

python -m sami_kanban_coach.cli validate-phase0

## 10. Config

Create:

config\settings.example.json
config\settings.json

Default config:

{
  "outlook_folder_path": "Kanban Intake",
  "output_root": "C:\\Tools\\SAMI Kanban Coach\\runtime\\email_recall",
  "save_msg_copy": true,
  "save_body_html": true,
  "save_headers": true,
  "save_attachments": false,
  "max_body_chars": 80000,
  "default_since_hours": 48,
  "default_max_items": 100,
  "poll_seconds": 60
}

The configured Outlook folder should usually be:

Kanban Intake

Support both:

Kanban Intake

and nested paths like:

Mailbox - Brian Shaw\Kanban Intake

Do not create the Outlook folder automatically. The user will create it manually in Outlook.

## 11. Doctor command specification

The doctor command is the first major deliverable.

It must print a Rich table:

Check | Result | Detail

Results should be:

PASS
WARN
FAIL

Doctor checks:

1. Python version.

2. pywin32 import.

3. Windows process detection:
   - Is OUTLOOK.EXE running?
   - Is new Outlook likely running, for example olk.exe or similar if detectable?
   - Report classic Outlook running yes/no.
   - Report new Outlook likely running yes/no.
   - Report if both appear to be running.

4. Outlook COM dispatch:
   - Try win32com.client.Dispatch("Outlook.Application").
   - If dispatch succeeds, report PASS.
   - If dispatch fails, report FAIL.
   - If new Outlook appears to be running and COM fails, explain that new Outlook is unsupported.

5. Outlook metadata if COM works:
   - Application.Name
   - Application.Version
   - Application.ProductCode if available
   - Session.CurrentProfileName if available

6. MAPI namespace:
   - Call GetNamespace("MAPI").
   - Confirm namespace opens.

7. Top-level stores:
   - List top-level mailbox/store names only.
   - Do not dump mailbox contents.
   - Do not recurse entire mailbox.

8. Config:
   - settings.json exists.
   - settings.json is valid JSON.
   - output_root resolves correctly.

9. Folder resolution:
   - Resolve configured outlook_folder_path.
   - Support simple folder name.
   - Support nested folder path.
   - If folder not found, print available top-level stores and direct child folders only.
   - Do not recurse the whole mailbox by default.

10. Runtime path writeability:
   - data folder writable.
   - evidence folder writable.
   - logs folder writable.
   - attachments folder writable if attachments enabled.

11. Optional Office registry hints:
   - Try safe Office ClickToRun registry reads under HKLM/HKCU if available.
   - Do not fail if unavailable.
   - Log as optional diagnostics only.

Doctor must never modify Outlook, mail, Kanban, Team ESMI, or network files.

Doctor pass/warn/fail rules:

PASS:
- Classic Outlook COM is available.
- MAPI namespace opens.
- Config is valid.
- Output folders writable.
- Configured folder resolves.

WARN:
- Outlook not currently open, but COM can start/connect.
- Configured Kanban Intake folder is missing, but Outlook COM works.
- Optional registry hints unavailable.

FAIL:
- Outlook.Application COM dispatch fails.
- MAPI namespace cannot open.
- Config unreadable.
- Output root not writable.

## 12. Email capture modes

Support three capture modes:

selected
folder
live

### Selected export

Command:

python -m sami_kanban_coach.cli export-selected

Behaviour:

- Reads currently selected Outlook items.
- Captures only MailItem objects.
- Skips meetings/tasks/reports/non-mail items.
- Logs skipped item type/class.
- Does not alter selected items.
- Does not move, mark, archive, delete, flag, categorise, or send anything.

### Folder export

Command:

python -m sami_kanban_coach.cli export-folder --since-hours 48 --max-items 100

Behaviour:

- Reads configured Outlook folder.
- Sorts newest first.
- Default window: last 48 hours.
- Default max items: 100.
- Do not scan entire mailbox by default.
- Do not recurse subfolders in Phase 0.
- Use filtering/restrict-style behaviour where practical.
- Never alter mailbox state.

### Live watcher

Command:

python -m sami_kanban_coach.cli live-watch --poll-seconds 60

Behaviour:

- Watches configured Outlook folder.
- Use Outlook Items.ItemAdd event if feasible via pywin32.
- Also run fallback polling every poll_seconds.
- Default poll interval: 60 seconds.
- Polling should scan recent items only and rely on dedupe.
- Keep running until Ctrl+C.
- Show startup status.
- Show captured/skipped counts.
- Log all activity.

Live watcher should be robust. Outlook COM events can be missed in some situations, so polling fallback is required.

## 13. Dedupe design

Each captured email must have a stable messageKey.

Preferred messageKey order:

1. InternetMessageID if available.
2. EntryID if available.
3. SHA256 hash of subject + sender + received timestamp.

Store processed keys in:

runtime\email_recall\data\processed_ids.json

If an email is already captured:

- Do not append raw_email_recall.jsonl.
- Do not rewrite evidence.
- Print/log “already captured”.
- Continue safely.

processed_ids.json should be written atomically.

## 14. Evidence saved for each email

For every captured email, create:

runtime\email_recall\evidence\emails\YYYY-MM-DD\<safe-subject-hash>\

Save:

email.json
body.txt
body.html if enabled/available
headers.txt if enabled/available
original.msg if save_msg_copy is true

The JSONL file is an index. The evidence folder is the source evidence copy.

The system should preserve enough evidence that later Qwen/Kanban comparison can cite and reason over the email without needing to go back to Outlook.

## 15. Attachment behaviour

Default:

save_attachments = false

Even when attachments are not saved, capture metadata:

- index
- name
- extension
- size
- savedPath null

If save_attachments is true, save attachments to:

runtime\email_recall\attachments\YYYY-MM-DD\<messageKey>\

Rules:

- Sanitize filenames.
- Never overwrite same-name files silently.
- Append counter if needed.
- Store saved relative path in the JSON record.

## 16. Required JSONL schema

Append one JSON object per captured email to:

runtime\email_recall\data\raw_email_recall.jsonl

Required schema:

{
  "schemaVersion": 1,
  "source": "outlook_com",
  "captureMode": "live|poll|selected|folder",
  "messageKey": "...",
  "internetMessageId": "...",
  "entryId": "...",
  "conversationId": "...",
  "subject": "...",
  "senderName": "...",
  "senderEmail": "...",
  "to": ["..."],
  "cc": ["..."],
  "receivedAt": "ISO datetime string",
  "sentOn": "ISO datetime string or null",
  "capturedAt": "ISO datetime string",
  "sourceFolder": "Kanban Intake",
  "bodyTextPath": "relative path from output_root",
  "bodyHtmlPath": "relative path or null",
  "headersPath": "relative path or null",
  "msgPath": "relative path or null",
  "attachments": [
    {
      "index": 1,
      "name": "...",
      "extension": ".pdf",
      "size": 12345,
      "savedPath": null
    }
  ],
  "bodyPreview": "first 500 chars only",
  "bodyCharCount": 12345,
  "evidenceFolder": "relative path from output_root",
  "processed": false,
  "kanbanLinked": false,
  "notes": []
}

bodyPreview must be capped to 500 characters.

bodyText may be capped according to max_body_chars, but bodyCharCount should reflect the original captured body length where possible.

Use safe getters for Outlook fields. Missing or unavailable fields must not crash capture.

## 17. Outlook field handling

Capture where available:

- Subject
- SenderName
- SenderEmailAddress
- To
- CC
- ReceivedTime
- SentOn
- EntryID
- ConversationID
- InternetMessageID via PropertyAccessor if available
- Transport headers via PropertyAccessor if available
- Body
- HTMLBody
- Attachments metadata

Transport headers should be attempted, but failure should not break export.

Save .msg using Outlook SaveAs. Prefer Unicode MSG where practical.

Normalize datetimes to ISO strings.

## 18. Path safety

Path rules:

- Use pathlib everywhere.
- Must handle spaces in repo path.
- Sanitize invalid Windows path characters:
  < > : " / \ | ? *
- Limit folder and file name length.
- Add hash suffix to avoid collisions.
- Do not overwrite same-name files silently.
- Use relative paths from output_root in JSONL records.
- Use atomic writes for email.json and processed_ids.json.
- raw_email_recall.jsonl can be append-only but should flush safely.

Evidence folder naming:

YYYY-MM-DD\<safe-subject-prefix>-<short-hash>

Example:

runtime\email_recall\evidence\emails\2026-07-02\monitor-replacement-tender-specs-a1b2c3d4\

## 19. Logging

Log to:

runtime\email_recall\logs\email_recall.log

Log:

- startup config
- Python version
- Outlook process detection
- Outlook COM connection result
- Outlook version/build
- profile/store info
- folder resolution result
- scanned count
- captured count
- duplicate skipped count
- non-mail skipped count
- errors with exception type/message
- evidence paths created
- validation results

Console output should be readable using Rich.

Do not dump full email bodies to console.

## 20. Batch scripts

Create scripts:

### scripts\install_requirements.bat

Behaviour:

- cd to repo root
- create .venv if missing
- install requirements
- run doctor
- pause

### scripts\run_doctor.bat

Behaviour:

- cd to repo root
- activate .venv
- run doctor
- pause

### scripts\run_export_selected.bat

Behaviour:

- cd to repo root
- activate .venv
- run export-selected
- pause

### scripts\run_export_folder.bat

Behaviour:

- cd to repo root
- activate .venv
- run export-folder --since-hours 48 --max-items 100
- pause

### scripts\run_live_watcher.bat

Behaviour:

- cd to repo root
- activate .venv
- run live-watch --poll-seconds 60
- pause on exit/error

### scripts\validate_phase0.bat

Behaviour:

- cd to repo root
- activate .venv
- run validate-phase0
- pause

All batch files must handle the repo path with spaces.

## 21. Validation command

Command:

python -m sami_kanban_coach.cli validate-phase0

Validation should check:

- Repo structure exists.
- Config exists/readable.
- requirements importable.
- Output folders writable.
- processed_ids.json valid if present.
- raw_email_recall.jsonl valid JSONL if present.
- Evidence folder paths referenced by JSONL exist where applicable.
- Doctor can run without crashing.
- No known Kanban paths were touched by this project.
- Forbidden path guard is present.

Validation should print a Rich table:

Check | Result | Detail

It should not require actual email capture to pass structural validation.

## 22. Forbidden path guard

Implement explicit constants for forbidden paths:

C:\Tools\SAMI-Kanban-WorkServer

\\fusafmcf01\Medical Imaging\Team_ESMI\Program Delivery\SAMI-Kanban-WorkServer

Any write helper should reject writes under these paths during Phase 0.

Phase 0 must not write to Kanban or Team ESMI.

## 23. README requirements

README.md must document:

- Project purpose.
- Phase 0 scope.
- What the tool does.
- What it does not do.
- Requirement for classic Outlook.
- How to manually create the Outlook folder “Kanban Intake”.
- How to install.
- How to run doctor.
- How to export selected messages.
- How to export from folder.
- How to run live watcher.
- Where evidence files are stored.
- Where JSONL records are stored.
- How dedupe works.
- Safety limitations.
- Future phases.

README should make clear:

Phase 0 captures evidence only. It does not update Kanban.

## 24. Implementation style

Before editing:

- Inspect the repo.
- Confirm whether files already exist.
- Do not overwrite user work without checking.
- Create small, testable modules.
- Keep Phase 0 narrow.
- Prefer clear, boring, reliable code over clever code.
- Log useful errors.
- Fail safely.
- Never hide exceptions silently.
- Do not add unrelated frameworks.
- Do not build GUI/TUI yet.
- Do not introduce Ollama/Qwen yet.

Suggested module responsibilities:

### config.py

- Load settings.
- Create default config if missing.
- Validate paths.
- Resolve output_root.

### logging_setup.py

- Configure console/log file logging.

### path_safety.py

- Sanitize filenames.
- Build safe evidence paths.
- Prevent forbidden writes.
- Atomic write helpers.

### models.py

- Pydantic models for settings, email record, attachment metadata, doctor results.

### outlook_com.py

- Outlook COM connection.
- Process detection.
- Doctor metadata collection.
- Folder resolution.
- Selected items extraction.
- Folder export.
- Live watcher/event logic.
- Safe Outlook field getters.

### storage.py

- Dedupe.
- processed_ids.json handling.
- Save evidence files.
- Save .msg.
- Save body/header/html.
- Append JSONL.
- Attachment metadata/save.

### cli.py

- Typer commands.
- Rich output.

### validation.py

- Phase 0 validation checks.

## 25. Manual test plan

After implementation, test in this order:

1. Run:

scripts\install_requirements.bat

2. Run:

scripts\run_doctor.bat

Expected:
- Classic Outlook COM detected.
- Outlook version shown.
- MAPI opens.
- Kanban Intake folder resolution either passes or gives clear warning.

3. Create Outlook folder manually if needed:

Kanban Intake

4. Select one email in Outlook.

5. Run:

scripts\run_export_selected.bat

Expected:
- raw_email_recall.jsonl has one record.
- Evidence folder created.
- body.txt created.
- email.json created.
- original.msg created if enabled.
- Log updated.

6. Run selected export again on the same email.

Expected:
- Duplicate skipped.
- JSONL not appended.
- Evidence not rewritten.

7. Move or copy a test email into Kanban Intake.

8. Run:

scripts\run_export_folder.bat

Expected:
- Captures recent folder emails.
- Respects max-items and since-hours.
- Skips duplicates.

9. Run:

scripts\run_live_watcher.bat

Then copy a new test email into Kanban Intake.

Expected:
- Live watcher captures it.
- Polling fallback also safe due to dedupe.

10. Run:

scripts\validate_phase0.bat

Expected:
- Phase 0 validation passes or reports clear warnings.

11. Confirm no files were modified under:

C:\Tools\SAMI-Kanban-WorkServer

or:

\\fusafmcf01\Medical Imaging\Team_ESMI\Program Delivery\SAMI-Kanban-WorkServer

## 26. Definition of done for Phase 0

Phase 0 is complete when:

- Repo structure exists.
- requirements.txt exists.
- pyproject.toml exists.
- README.md exists.
- settings.example.json and settings.json exist.
- Doctor command works.
- Outlook version/classic COM detection works.
- MAPI namespace check works.
- Folder resolution works or gives clear warning.
- Selected email export works.
- Folder export works.
- Live watcher works with event attempt plus polling fallback.
- JSONL recall file is created.
- Evidence folders are created.
- body.txt is saved.
- email.json is saved.
- original.msg is saved when enabled.
- Header capture attempted safely.
- Attachment metadata captured.
- Duplicate protection works.
- Logs are readable.
- Batch launchers work.
- validate-phase0 works.
- No Kanban repo or Team ESMI files are modified.
- Final report lists files created, commands to run, validation status, and known limitations.

## 27. Final report format

When done, provide a concise SITREP:

SITREP — SAMI Kanban Coach Phase 0

Created:
- list files/modules/scripts

Implemented:
- doctor
- export-selected
- export-folder
- live-watch
- storage/dedupe/evidence
- validation

Validation:
- command run
- pass/fail/warnings

How to run:
- scripts\run_doctor.bat
- scripts\run_export_selected.bat
- scripts\run_export_folder.bat
- scripts\run_live_watcher.bat

Safety:
- Confirm no Kanban repo files modified.
- Confirm no Team ESMI files modified.
- Confirm no Outlook writes are performed.

Known limitations:
- Requires classic Outlook.
- New Outlook unsupported.
- Phase 0 does not call Qwen/Ollama.
- Phase 0 does not update Kanban.
- Phase 0 does not scan whole mailbox by default.

## 28. Strategic design reminder

This project exists to support governed SAMI portfolio maintenance.

The final intended workflow is:

Relevant email lands
→ user moves/copies it to Kanban Intake
→ local watcher captures full evidence
→ daily intake app reads current Kanban state
→ Qwen compares current card state against new evidence
→ app suggests material updates
→ user approves/edits/skips
→ only approved updates write to Kanban

Keep Phase 0 focused on the first part:

Relevant email lands
→ user moves/copies it to Kanban Intake
→ local watcher captures full evidence to C drive.
