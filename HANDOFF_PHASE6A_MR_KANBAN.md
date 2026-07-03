# Phase 6A Mr Kanban Handoff

## Run
`.\.venv\Scripts\python.exe -m sami_kanban_coach.cli coach-chat`

## Smoke
`.\.venv\Scripts\python.exe -m sami_kanban_coach.cli coach-chat --smoke-test`

## Safety
- Team ESMI writes disabled.
- Mailbox writes disabled.
- Email context search is read-only.
- Live/local production Kanban apply gates stay disabled.
- Local sandbox apply only, with exact `APPLY LOCAL` confirmation.

## Sandbox
Default sandbox path: `C:\Tools\SAMI Kanban Coach\runtime\local_kanban_sandbox`

## Current working scenario
General local Kanban questions, source display, draft display/build, apply-local, and undo. NT UltraRad VPN/firewall is the evidence-rich smoke fixture/calibration example only; Mr Kanban must remain general-purpose across all SAMI cards.

## Important commands
- `.\.venv\Scripts\python.exe -m compileall src`
- `.\.venv\Scripts\python.exe -m sami_kanban_coach.cli evidence-status`
- `.\.venv\Scripts\python.exe -m sami_kanban_coach.cli coach-chat --help`
- `.\.venv\Scripts\python.exe -m sami_kanban_coach.cli coach-chat --smoke-test`
- `git diff --check`

## Do not touch
- No Team ESMI writes.
- No mailbox writes.
- No broad refactors.
- No UX/layout redesign.
- Do not make Mr Kanban NT-only.

## Known limitations
This is still a local conversational harness. Behaviour quality should be reviewed card-by-card before expanding scope. If Team ESMI is offline, hash display reports unavailable/offline rather than inventing a value.

## Next recommended DeepSeek task
Review general behaviour quality and edge cases across multiple SAMI cards without expanding scope or redesigning the TUI.
