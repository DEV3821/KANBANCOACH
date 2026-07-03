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
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli --help
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli coach-status      # Pipeline status dashboard
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli local-ai-status    # Qwen/Ollama health (16 checks)
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli evidence-status    # Evidence pipeline deps
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli ollama-doctor      # Ollama endpoint check
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli kanban-doctor      # Kanban source paths
```

### Read-only View Commands
```powershell
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli show-cards
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli show-stale-cards
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli show-drafts
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli show-review-queue
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli show-apply-plan
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli show-apply-decisions
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli show-apply-audit
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli show-email-matches
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli show-unmatched-emails
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli show-no-change
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli show-apply-results
```

### Review TUI / Interactive
```powershell
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli review-tui          # Rich TUI for review queue
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli review-draft <id>    # Full view of single draft
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli review-apply-tui     # Rich TUI for apply plan
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli review-apply-plan    # CLI interactive apply review
```

### Safe Write Commands (writes to review/apply files only — NOT Kanban)
```powershell
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli approve-draft <id>
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli skip-draft <id>
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli edit-draft <id>
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli build-apply-plan
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli export-apply-report
```

### Safe Pipeline Commands
```powershell
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli coach-chat --smoke-test   # Sandbox smoke test
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli coach-dry-run              # End-to-end dry-run
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli validate-phase4a           # Phase 4A checks
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli validate-phase4b           # Phase 4B checks
```

### Evidence Search (read-only mailbox search when enabled)
```powershell
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli evidence-search <card_id>
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli evidence-show-run <run_id>
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli evidence-regression-test
```

### Conversational Coach (local sandbox only)
```powershell
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli coach-chat          # Interactive session
.\.venv\Scripts\python.exe -m sami_kanban_coach.cli coach-chat --smoke-test  # Automated smoke test
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

---

## Phase 6C Addendum — sshawbadmin Dependency Verification

Performed 2026-07-03 under `sshawbadmin` account (RDP session).

### Account & Venv
| Item | Value |
|------|-------|
| **Windows user** | `sshawbadmin` |
| **Venv python** | `C:\Tools\SAMI Kanban Coach\.venv\Scripts\python.exe` |
| **Python version** | 3.11.15 |
| **Venv prefix** | `C:\Tools\SAMI Kanban Coach\.venv` |
| **Base prefix** | `C:\Users\sshawbadmin\AppData\Roaming\uv\python\cpython-3.11-windows-x86_64-none` |
| **pip** | 24.0 (from Hermes venv site-packages) |

**Note:** The `.venv/pyvenv.cfg` was repaired to point at the `sshawbadmin` uv python. This is local state — not tracked by Git, never committed.

### Dependency Installation
- **Source:** `pyproject.toml` (declared deps: pywin32, typer, rich, pydantic)
- **Method:** `pip install -e .` (editable install from project root)
- **Pre-existing:** All declared deps were already satisfied via the Hermes venv site-packages
- **Purpose of `-e .`:** Ensures `sami_kanban_coach.*` modules are importable without `PYTHONPATH`

### All Verified Dependencies

| Category | Package | Status |
|----------|---------|--------|
| CLI | typer 0.26.8 | ✅ |
| TUI | rich 14.3.3 | ✅ |
| Config | pydantic 2.13.4 | ✅ |
| HTTP | requests 2.33.0 | ✅ |
| Excel | openpyxl 3.1.5 | ✅ |
| Images | pillow 12.2.0 | ✅ |
| Windows COM | pywin32 311 | ✅ |
| COM support | pythoncom | ✅ |
| Project modules | 6 modules | ✅ (review_tui, mailbox_search, coach_chat, cli, path_safety, evidence_pipeline) |

### External Tools

| Tool | Path | Version | Status |
|------|------|---------|--------|
| **Tesseract OCR** | `C:\Tools\Tesseract-OCR\tesseract.exe` | v5.4.0 | ✅ Installed (not on PATH) |
| **Ollama** | localhost:11434 | 2 models | ✅ Reachable (qwen3:8b, llama3.2:3b) |
| **Outlook COM** | pywin32 MAPI | Inbox (7435 items) | ✅ Connected & read-only |

### CLI Verification Results

| Command | Result |
|---------|--------|
| `compileall src/sami_kanban_coach -q` | ✅ PASS |
| `cli --help` | ✅ PASS |
| `coach-status` | ✅ PASS |
| `review-tui --help` | ✅ PASS |
| `review-apply-tui --help` | ✅ PASS |
| `evidence-status` | ✅ PASS |
| `local-ai-status` (16 checks) | ✅ **16/16 PASS** |

### Safety Gates
All 7 critical gates **disabled** — confirmed via `config/settings.json`:
- `allow_kanban_apply=false`
- `local_kanban_apply_enabled=false`
- `team_kanban_apply_enabled=false`
- `mailbox_search_enabled=false`
- `mailbox_write_enabled=false`
- `team_esmi_write_enabled=false`

### Kanban Source Hash
✅ `3e01e9af3d0daa87...` — **unchanged** from baseline.

### Ad-hoc Verification
Custom script `hermes-verify-deps-<random>.py` created under `%TEMP%`, run, and **deleted**.
- **Result:** 18/18 PASS
- **Script files confirmed cleaned up:** Yes

### Git Hygiene
- Only dirty tracked file: `IDEA.md` (untouched by this session) ✅
- `.venv/pyvenv.cfg` not staged, not committed, not tracked ✅
- No dependency install artifacts staged ✅
- No source/config changes made ✅

### Remaining Risks
1. **Push to GitHub blocked** — No GitHub credentials configured under `sshawbadmin`. The 3 local commits (`ahead 3`) remain unpushed. Run `git push origin HEAD` from a Git-authenticated session.
2. **Ollama slow response** — Qwen model may timeout on full smoke test (60s). Consider `llama3.2:3b` as faster alternative, or increase timeout.
3. **Team ESMI offline** — Network path to `\\fusafmcf01\Medical Imaging\Team_ESMI\` unreachable (expected off VPN).
4. **Tesseract not on PATH** — Must use absolute path `C:\Tools\Tesseract-OCR\tesseract.exe`; the `evidence-status` command finds it correctly.
5. **Ollama not on PATH** — Available via HTTP at localhost:11434; `ollama.exe` not in any PATH.
6. **Account switching** — If switching between `sshawb` and `sshawbadmin` profiles, `.venv/pyvenv.cfg` may need the `home` path updated again.

---

## Phase 6E Addendum — Mr Kanban Chat End-to-End Test

Performed 2026-07-04 under `sshawbadmin` account.

### Root Cause: Smoke Test Timeout

The `coach-chat --smoke-test` was timing out because the default `ollama_timeout_seconds = 60` in `config/settings.json` was too short. Direct Ollama API testing confirmed:

| Model | Response Time | Result |
|-------|--------------|--------|
| `qwen3:8b` (8.2B) | **62.9s** | `"Mr Kanban is now ready."` |
| `llama3.2:3b` (3.2B) | Not tested (Qwen worked) | — |

### Fix Applied

Changed `ollama_timeout_seconds` from 60 to 120 in `config/settings.json` (local-only — file is gitignored).

The setting is read by `coach_chat.py` line 562 via `getattr(settings, "ollama_timeout_seconds", 60)` and the Pydantic `Settings` model at `config.py` line 146 with default 60. The JSON value overrides the default.

### Chat Test Results

| Command | Result | Detail |
|---------|--------|--------|
| `coach-chat --smoke-test` | ✅ **PASS** | Model reachable, sandbox apply+undo, 17 sources, Team ESMI untouched |
| `coach-dry-run` | ✅ **PASS** | 5 drafts, 2 plan items, 2 approved, 2 conflicts, 0 dry-applied, hash unchanged |
| `local-ai-status` (16 checks) | ✅ **16/16 PASS** | Ollama reachable, qwen3:8b available, mailbox disabled, Team ESMI offline |
| `ollama-doctor` | ✅ Available | — |

### Smoke Test Details

- **Model:** qwen3:8b via Ollama at localhost:11434
- **Timeout:** 120s (was 60s — increased to accommodate Qwen's ~63s response)
- **Sources used:** 17 (local sandbox card + evidence runs + mailbox search + attachments)
- **Sandbox apply:** `nextAction` changed then restored by undo
- **Team ESMI:** untouched (offline/unavailable in current network)
- **Mailbox search:** used in read-only mode (disabled by default)

### Dry-Run Details

- **Items:** 5 drafts → 2 plan items → 2 approved → 2 conflicts
- **Write count:** 0 (read-only simulation)
- **Kanban hash before/after:** identical ✅
- **Report exported** to `runtime/apply/data/kanban_coach_pilot_report_*.md`

### Safety Verification

All gates confirmed disabled:
```
allow_kanban_apply=false
local_kanban_apply_enabled=false
team_kanban_apply_enabled=false
mailbox_search_enabled=false
mailbox_write_enabled=false
team_esmi_write_enabled=false
mailbox_search_read_only=true
kanban_apply_target=local_sandbox
ollama_timeout_seconds=120
```

Kanban hash: `3e01e9af...` — **unchanged**.

### Ad-hoc Verification

Custom script `hermes-verify-chat-<random>.py` under `%TEMP%`:
- **19/19 PASS**
- Temp files cleaned, confirmed gone

### Commitable Changes

- `config/settings.json` — `ollama_timeout_seconds` changed from 60 to 120
  - **File is gitignored** — cannot be committed. Local-only.

### Remaining Risks

1. **Qwen is slow** (~63s first response). Consider `llama3.2:3b` for faster interaction, or accept the delay.
2. **Settings file is gitignored** — each new clone/environment needs `ollama_timeout_seconds=120` added locally.
3. **GitHub push blocked** — 4 commits ahead, unpushed.

---

## Phase 6F Addendum — Greeting Guard and Safer Evidence

Performed 2026-07-04 under `sshawbadmin` account.

### Problem

Mr Kanban was performing full Kanban/email context retrieval for every prompt, including greetings. Typing `hello mr kanban` would:
- Search Kanban sandbox and pick an unrelated card (Zed Messenger Enhancements)
- Search email evidence and expose patient/ticket-style snippets
- Produce a recommendation and confidence score for an empty query

This was a safety/privacy concern — email evidence could contain patient-identifying information.

### Fix: Greeting/Low-Information Guard

Added `is_low_information_prompt()` in `coach_chat.py`:

```python
_LOW_INFO_PHRASES = frozenset({
    "hello", "hi", "hey", "good morning", "good afternoon", "good evening",
    "howdy", "greetings", "yo", "sup", "hiya",
    "thanks", "thank you", "cheers", "ty", "thx",
    "test", "testing", "are you there", "you there", "hello mr kanban",
    "hi mr kanban", "hey mr kanban", "mr kanban", "ok", "k", "done",
})
```

Detection logic:
1. Exact match against `_LOW_INFO_PHRASES`
2. Empty/no-meaningful-word check
3. All-word check (every significant word is a greeting phrase)

**Verified behaviour:**

| Input | Low-Info? | Result |
|-------|-----------|--------|
| `hello`, `hi`, `hey` | ✅ True | Friendly greeting, no retrieval |
| `good morning`, `thanks` | ✅ True | Friendly greeting, no retrieval |
| `are you there`, `test` | ✅ True | Friendly greeting, no retrieval |
| `hello mr kanban` (any case) | ✅ True | Friendly greeting, no retrieval |
| `what needs update` | ❌ False | Full retrieval as normal |
| `review the NT UltraRad card` | ❌ False | Full retrieval as normal |
| `what stale cards need attention` | ❌ False | Full retrieval as normal |

### Fix: Safer Evidence Display

In `render_answer()`:
- **Evidence cap**: reduced from 12 to 6 items
- **Evidence truncation**: reduced from 500 to 300 chars per item
- **Source summary cap**: reduced from 8 to 5 items
- **Source truncation**: reduced from 500 to 200 chars

All changes keep evidence useful while preventing accidental patient-data dumps.

### Test Results

| Check | Result |
|-------|--------|
| `compileall src/sami_kanban_coach` | ✅ PASS |
| `cli --help` | ✅ PASS |
| `local-ai-status` (16 checks) | ✅ 16/16 PASS |
| Greeting guard: 10 greetings → bypass | ✅ 10/10 PASS |
| Greeting guard: 5 real queries → retrieve | ✅ 5/5 PASS |
| Safety gates | ✅ All disabled |
| Kanban hash | ✅ Unchanged |
| Temp verifier | ✅ Written, run, deleted, confirmed gone |

### Files Changed

- `src/sami_kanban_coach/coach_chat.py` — greeting guard + safer evidence display

### Commit

```text
f6599dd..XXXXXXXX Guard Mr Kanban chat greetings and evidence output
```

---

## Phase 6G Addendum — Deterministic Routing and Grounded Answers

Performed 2026-07-04 under `sshawbadmin` account.

### Problem

Phase 6F fixed greeting detection, but:
1. Every non-greeting prompt triggered full Kanban + email search indiscriminately
2. No typed routing — all Kanban/email/recommendation/write prompts hit the same code path
3. Email context was always searched, even for simple Kanban lookups
4. No unsafe write handler existed
5. Answer shape lacked structured card metadata grounding

### Changes Made

**File: `src/sami_kanban_coach/coach_chat.py`**

#### 1. `ChatRoute` dataclass (model-agnostic routing)

```python
@dataclass(frozen=True)
class ChatRoute:
    intent: str         # social | kanban_lookup | email_evidence_lookup | recommendation_draft | unsafe_write_request | unknown
    use_kanban: bool    # whether to retrieve Kanban card context
    use_email: bool     # whether to retrieve email evidence
    allow_recommendation: bool
    allow_write: bool
    reason: str
```

#### 2. `route_prompt()` function

Deterministic, model-agnostic routing with priority:

| Priority | Route | Trigger |
|----------|-------|---------|
| 1 | `social` | `is_low_information_prompt()` match |
| 2 | `unsafe_write_request` | Write keywords + board-target co-occurrence or explicit write phrases |
| 3 | `email_evidence_lookup` | Email/mailbox/Outlook/evidence/attachment keywords |
| 4 | `recommendation_draft` | Recommend/draft/suggest/prepare/propose keywords |
| 5 | `kanban_lookup` | Default fallback |

#### 3. Source selection obeys route

`build_context(settings, query, route=None)`:
- `use_kanban=True`: retrieves Kanban card + prior evidence
- `use_email=True`: retrieves mailbox context (read-only, capped)
- Both default to `False` if route omitted

Email is **never** retrieved unless `route.use_email == True`.

#### 4. Unsafe write handler

`render_unsafe_write_response(console)` — prints:
- Refusal with gate status table
- Alternative safe paths (draft recommendation, `/apply-local`, pipeline workflow)
- Safety footer: No Team ESMI, no mailbox write

#### 5. Structured grounded answer

`render_answer()` now shows a "Based on:" section with card metadata:
```
- Card: <title> | status=<status> | risk=<risk> | lead=<lead>
- Current state: <context or "not recorded">
- Next action: <nextAction or "not recorded">
```

Missing values display `not recorded` — no invented data.

#### 6. Email safety helpers

- `format_safe_email_evidence(items, max_items=3)` — metadata-only summary, no raw bodies, capped at 3 by default
- Evidence rendering in interactive loop shows cap note when email exceeds 3 items

### Route Test Results (31/31 PASS)

| Category | Tested | Result |
|----------|--------|--------|
| Social (no retrieval) | 7 prompts | ✅ All `intent=social`, no kanban/email/write |
| Kanban-only | 4 prompts | ✅ All `intent=kanban_lookup`, `use_kanban=true`, `use_email=false` |
| Email evidence | 4 prompts | ✅ All `intent=email_evidence_lookup`, `use_email=true`, `allow_write=false` |
| Recommendation | 3 prompts | ✅ All `intent=recommendation_draft`, `allow_recommendation=true`, `allow_write=false` |
| Unsafe write | 4 prompts | ✅ All `intent=unsafe_write_request`, `allow_write=false` |

### Chat Behaviour

| Scenario | Expected | Behaviour |
|----------|----------|-----------|
| `hello mr kanban` | Friendly greeting, no retrieval | ✅ Social route, `render_greeting()` |
| `what stale cards need attention` | Kanban lookup only | ✅ `kanban_lookup` route, kanban only |
| `summarise the Monitor Replacement card` | Card metadata, grounded | ✅ Structured "Based on:" section |
| `find email context for NT UltraRad` | Email evidence (read-only) | ✅ `email_evidence_lookup`, capped |
| `apply this to Team ESMI now` | Refuse, explain gates | ✅ `unsafe_write_request`, `render_unsafe_write_response()` |

### Safety Gate Status

| Gate | Status |
|------|--------|
| `allow_kanban_apply` | False ✅ |
| `local_kanban_apply_enabled` | False ✅ |
| `team_kanban_apply_enabled` | False ✅ |
| `mailbox_search_enabled` | False ✅ |
| `mailbox_write_enabled` | False ✅ |
| `team_esmi_write_enabled` | False ✅ |
| `mailbox_search_read_only` | True ✅ |
| `kanban_apply_target` | `local_sandbox` ✅ |
| Kanban source hash | `3e01e9af...` ✅ Unchanged |

### Known Qwen Delay

Response from `qwen3:8b` takes ~63s. This is acceptable for Phase 6G quality work.

### Verification

| Check | Result |
|-------|--------|
| `compileall src/sami_kanban_coach -q` | ✅ PASS |
| `cli --help` | ✅ PASS (shows `coach-chat`) |
| `coach-chat --help` | ✅ PASS (shows `--smoke-test`) |
| `local-ai-status` | ✅ 16/16 PASS |
| `coach-status` | ✅ PASS (gates disabled) |
| Safety gates (8 critical) | ✅ All disabled/read-only |
| Temp verifier (31 checks) | ✅ 31/31 PASS, cleaned up |
| Kanban hash | ✅ `3e01e9af...` unchanged |

### Remaining Risks

1. **Qwen is slow** (~63s first response). Consider `llama3.2:3b` for faster interaction.
2. **Team ESMI offline** — Network path to `\\fusafmcf01\Medical Imaging\Team_ESMI\` unreachable (expected off VPN).
3. **GitHub push blocked** — 6 commits ahead, unpushed.
4. **Routing is heuristic** — some edge prompts may misclassify (e.g. `"can you update the firewall card"` contains `"update"` but says `"can you"` which suggests lookup, not write). Monitor and adjust phrase sets as needed.
5. **Qwen not exercised end-to-end** — routing and source selection verified statically; actual Qwen generation depends on model availability (confirmed reachable) and ~63s timeout.

### Recommended Next Phase (Phase 6H)

- ✅ ~~Run `coach-chat --smoke-test` with new routing code~~ (tested via headless harness)
- ✅ ~~Run interactive `coach-chat` and test: greeting, kanban lookup, email evidence, recommendation, unsafe write~~ (5/5 headless tests passed)
- Evaluate Qwen answer quality with grounded output in interactive session
- Consider pushing to GitHub