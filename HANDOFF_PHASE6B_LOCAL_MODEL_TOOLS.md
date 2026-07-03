# Phase 6B Mr Kanban — Local Model Tool Handoff

## Baseline

| Field | Value |
|-------|-------|
| **Commit** | `17e82d7` |
| **Message** | Polish Mr Kanban review TUI and generalise mailbox thread counts |
| **Branch** | `main` |
| **Remote** | `origin` → `https://github.com/DEV3821/KANBANCOACH.git` |
| **Status** | `ahead 2` (two local commits: `17e82d7`, `234d09f`) |
| **Repo path** | `C:\Tools\SAMI Kanban Coach` |

## Local Model (Qwen/Ollama)

- **Endpoint:** `http://127.0.0.1:11434`
- **Configured model:** `qwen3:8b`
- **Available models:** `qwen3:8b`, `llama3.2:3b`
- **Ollama reachable:** ✅ Yes (verified via `local-ai-status`, 16/16 PASS)
- **Note:** Response can be slow (smoke test timed out at 60s)

## Safety Gates (All Verified)

| Gate | Status |
|------|--------|
| `allow_kanban_apply` | **disabled** |
| `local_kanban_apply_enabled` | **disabled** |
| `team_kanban_apply_enabled` | **disabled** |
| `mailbox_search_enabled` | **disabled** (must be explicitly enabled) |
| `mailbox_write_enabled` | **disabled** |
| `team_esmi_write_enabled` | **disabled** |
| `kanban_apply_target` | `local_sandbox` (safe) |
| `ignore_smoke_test_drafts` | `enabled` (filters test data) |
| `backup_before_apply` | `enabled` (backup before writes) |
| `mailbox_search_read_only` | `true` (read-only) |

**Kanban source hash:** `3e01e9af3d0daa875e40c20d66fc1ac2ead2b5140a9cb1795decdccc4ffca595` — **unchanged** from baseline.

## Safe Command Reference

### Read-only / Status Commands
```powershell
.\venv\Scripts\python.exe -m sami_kanban_coach.cli --help
.\venv\Scripts\python.exe -m sami_kanban_coach.cli coach-status      # Pipeline status dashboard
.\venv\Scripts\python.exe -m sami_kanban_coach.cli local-ai-status    # Qwen/Ollama health (16 checks)
.\venv\Scripts\python.exe -m sami_kanban_coach.cli evidence-status    # Evidence pipeline deps
.\venv\Scripts\python.exe -m sami_kanban_coach.cli ollama-doctor      # Ollama endpoint check
.\venv\Scripts\python.exe -m sami_kanban_coach.cli kanban-doctor      # Kanban source paths
```

### Read-only View Commands
```powershell
.\venv\Scripts\python.exe -m sami_kanban_coach.cli show-cards
.\venv\Scripts\python.exe -m sami_kanban_coach.cli show-stale-cards
.\venv\Scripts\python.exe -m sami_kanban_coach.cli show-drafts
.\venv\Scripts\python.exe -m sami_kanban_coach.cli show-review-queue
.\venv\Scripts\python.exe -m sami_kanban_coach.cli show-apply-plan
.\venv\Scripts\python.exe -m sami_kanban_coach.cli show-apply-decisions
.\venv\Scripts\python.exe -m sami_kanban_coach.cli show-apply-audit
.\venv\Scripts\python.exe -m sami_kanban_coach.cli show-email-matches
.\venv\Scripts\python.exe -m sami_kanban_coach.cli show-unmatched-emails
.\venv\Scripts\python.exe -m sami_kanban_coach.cli show-no-change
.\venv\Scripts\python.exe -m sami_kanban_coach.cli show-apply-results
```

### Review TUI / Interactive
```powershell
.\venv\Scripts\python.exe -m sami_kanban_coach.cli review-tui          # Rich TUI for review queue
.\venv\Scripts\python.exe -m sami_kanban_coach.cli review-draft <id>    # Full view of single draft
.\venv\Scripts\python.exe -m sami_kanban_coach.cli review-apply-tui     # Rich TUI for apply plan
.\venv\Scripts\python.exe -m sami_kanban_coach.cli review-apply-plan    # CLI interactive apply review
```

### Safe Write Commands (writes to review/apply files only — NOT Kanban)
```powershell
.\venv\Scripts\python.exe -m sami_kanban_coach.cli approve-draft <id>
.\venv\Scripts\python.exe -m sami_kanban_coach.cli skip-draft <id>
.\venv\Scripts\python.exe -m sami_kanban_coach.cli edit-draft <id>
.\venv\Scripts\python.exe -m sami_kanban_coach.cli build-apply-plan
.\venv\Scripts\python.exe -m sami_kanban_coach.cli export-apply-report
```

### Safe Pipeline Commands
```powershell
.\venv\Scripts\python.exe -m sami_kanban_coach.cli coach-chat --smoke-test   # Sandbox smoke test
.\venv\Scripts\python.exe -m sami_kanban_coach.cli coach-dry-run              # End-to-end dry-run
.\venv\Scripts\python.exe -m sami_kanban_coach.cli validate-phase4a           # Phase 4A checks
.\venv\Scripts\python.exe -m sami_kanban_coach.cli validate-phase4b           # Phase 4B checks
```

### Evidence Search (read-only mailbox search when enabled)
```powershell
.\venv\Scripts\python.exe -m sami_kanban_coach.cli evidence-search <card_id>
.\venv\Scripts\python.exe -m sami_kanban_coach.cli evidence-show-run <run_id>
.\venv\Scripts\python.exe -m sami_kanban_coach.cli evidence-regression-test
```

### Conversational Coach (local sandbox only)
```powershell
.\venv\Scripts\python.exe -m sami_kanban_coach.cli coach-chat          # Interactive session
.\venv\Scripts\python.exe -m sami_kanban_coach.cli coach-chat --smoke-test  # Automated smoke test
```
Chat commands: `/status`, `/sources`, `/draft`, `/apply-local` (requires `APPLY LOCAL`), `/undo`, `/reset-local`, `/search`, `/card`, `/evidence`, `/help`, `/exit`

## Forbidden Actions (without explicit review)

| Action | Risk |
|--------|------|
| `apply-approved-local` | Writes to local Kanban source — requires explicit `--no-dry-run` |
| `apply-approved-plan` | Writes to Kanban with strong confirmation — requires all gates enabled |
| `apply-flow` | Full interactive apply flow |
| `reset-apply-workspace` | Archives apply workspace — confirm before running |
| `reset-apply-decisions` | Deletes all decision records — irreversible |
| `export-folder` / `export-selected` | Exports real Outlook emails — scoped to configured folder |
| `live-watch` | Polls Outlook folder continuously |
| Enabling `mailbox_search_enabled` | Must be explicitly enabled in config |
| Enabling `allow_kanban_apply` | Must be explicitly enabled — dangerous |

## Operator Workflow

1. **Check status:** `coach-status`
2. **Check AI health:** `local-ai-status`
3. **View pipeline:** `show-drafts` → `show-review-queue` → `show-apply-plan`
4. **Review:** `review-tui` or `review-apply-tui` (Rich TUI with SAMI teal branding)
5. **Decide:** approve/skip/needs-edit via TUI or CLI
6. **Build plan:** `build-apply-plan` (if drafts updated)
7. **Export report:** `export-apply-report`
8. **Dry-run:** `coach-dry-run` (safe end-to-end)
9. **Apply (gated):** Only with explicit gates enabled and `--no-dry-run`

## Review TUI (SAMI Teal Branding)

The review TUI (`review-tui` and `review-apply-tui`) uses SAMI teal (`#008C95`) styling:
- Header panels with SAMI teal borders and title
- Card details and before/after comparison in teal panels
- Recommendation queue in teal-bordered table
- Summary table with teal title
- Full views, export prompts, and session messages in teal

All existing workflow and layout is preserved — only visual styling changed.

## Verification Results (this session)

| Check | Result |
|-------|--------|
| `compileall src/sami_kanban_coach` | ✅ PASS |
| `sami_kanban_coach.cli --help` | ✅ PASS |
| `coach-status` | ✅ PASS |
| `review-tui --help` | ✅ PASS |
| `local-ai-status` (16 checks) | ✅ 16/16 PASS |
| `show-apply-plan` | ✅ PASS |
| `show-apply-decisions` | ✅ PASS |
| `show-review-queue` | ✅ PASS |
| `show-apply-audit` | ✅ PASS |
| Temp verification script (23 checks) | ✅ 23/23 PASS |
| Safety gates (6 critical) | ✅ All disabled |
| Kanban source hash | ✅ Unchanged (`3e01e9af...`) |
| `.venv` repaired | ✅ Pointed to sshawbadmin uv python |

## Remaining Risks

1. **Ollama slow response:** Smoke test timed out at 60s. Qwen model may need more time or a faster model.
2. **Team ESMI offline:** Network path to `\\fusafmcf01\Medical Imaging\Team_ESMI\` is unreachable from current session — expected when off VPN/network.
3. **`.venv` path dependency:** The venv was repaired to use `sshawbadmin`'s uv Python. If Hermes switches user profiles again, the `pyvenv.cfg` may need updating.
4. **No live write test performed:** The `apply-approved-local` and `apply-approved-plan` paths have not been tested with live data — they require flag gating.
5. **Mailbox search untested:** Mailbox search remains disabled by default. If enabled, Outlook COM must be running and accessible.

## Previous Commit History (not yet pushed)

```
17e82d7 Polish Mr Kanban review TUI and generalise mailbox thread counts
234d09f Phase 6A Mr Kanban local coach polish
```

## Push Status

**Not yet pushed.** Two local commits ahead of `origin/main`. Ready to push after this handoff document is committed.

## Pending for Phase 6C

- Verify mailbox search end-to-end when explicitly enabled
- Test `coach-chat --smoke-test` with longer timeout
- Test `coach-dry-run` pipeline
- Evaluate Qwen model response quality across multiple cards
- Consider pushing to GitHub after Phase 6B handoff commit
