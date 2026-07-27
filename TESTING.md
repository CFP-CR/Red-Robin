# Red Robin v0.1.x — Testing Guide

Four-tier testing playbook. Run them in order. Each tier isolates a layer of the
system so failures point at one cause, not three.

| Tier | What it validates | Time | External deps |
|------|-------------------|------|---------------|
| 1. Local smoke | Daemon boot, `/v1/events` ingestion, bus → reducer → working memory, TUI | ~10 min | Anthropic + Voyage keys |
| 2. Sliver-Mock | Wire protocol end-to-end, adapter session lifecycle, scenario replay | ~15 min | none (no CS license needed) |
| 3. CS local (no beacon) | `redrobin.cna` parses in Aggressor, handshake succeeds, console stays stock | ~30 min | CS Team Server + CS Client |
| 4. CS full E2E | Both live paths: passthrough capture (no interception) + assistant advisory in the TUI | ~1-2 h | CS + lab with a live beacon |

**Stop after Tier 1 if anything's red.** Don't escalate to a real CS test with
a broken local daemon.

**The boundary you are testing:** the adapter captures, the core advises. The
`.cna` observes operator commands and their output and ships them to the daemon
(`beacon.command_input` / `beacon.command_output` / `beacon.initial` →
`POST /v1/events`); it never intercepts, blocks, or rewrites a command. Every
Red Robin utterance lands in the Textual TUI's Assistant Chat pane, never in the
Beacon console. If a test step ever shows Red Robin standing between the
operator and the beacon, that is the bug.

All commands assume **Windows + PowerShell** (per the operator's environment).
The repo at `C:\Users\jcspi\Documents\dev-projects\Red-Robin` and the test root
at `$env:USERPROFILE\redrobin-smoke` keep your real `~/.redrobin` (if any)
untouched.

---

## Tier 0 — Pre-flight

Run this once before any tier:

```powershell
cd C:\Users\jcspi\Documents\dev-projects\Red-Robin
git checkout dev          # the git-flow integration branch (see CONTRIBUTING)
git log --oneline -1

uv sync --extra dev
uv run pytest tests/ -q
uv run pytest adapters/ -q
```

Expected: `0 failed` from both. (They are two runs because CI runs them as two
steps; `adapters/` currently holds Python tests for the Sliver-Mock adapter only
— the CS adapter's `tests/` dir carries the manual `test_cna_smoke.md`
checklist, which no `pytest` run can execute.) If anything fails, stop and
investigate before running any tier.

**Why `--extra dev`?** This project defines dev tools (`pytest`, `mypy`,
`ruff`, `import-linter`, `pre-commit`, etc.) under `[project.optional-
dependencies]` in `pyproject.toml` — PEP 621 "extras". The uv flag for
extras is `--extra <name>`, not `--dev`. Recent versions of `uv` install
only the main dependency set by default, so a bare `uv sync` will UNINSTALL
the dev tools and you'll see `error: Failed to spawn: 'pytest' program
not found` on the next `uv run pytest`. If `--extra dev` doesn't restore
them, run `uv sync --all-extras` to pull every defined extra. (For projects
that use `[dependency-groups]` / PEP 735 instead, the equivalent flag is
`--group dev` or `--all-groups`. This project uses PEP 621 extras.)

---

## Tier 1 — Local Smoke

**Goal:** prove the daemon + TUI work end-to-end against a clean operator setup.

### Setup

```powershell
# Must be in the repo root so `uv run redrobin init` finds the project
cd C:\Users\jcspi\Documents\dev-projects\Red-Robin

$TEST_ROOT = "$env:USERPROFILE\redrobin-smoke"
if (Test-Path $TEST_ROOT) { Remove-Item -Recurse -Force $TEST_ROOT }

uv run redrobin init --non-interactive `
  --root $TEST_ROOT `
  --anthropic-key "<paste real ANTHROPIC_API_KEY>" `
  --voyage-key "<paste real VOYAGE_API_KEY>" `
  --engagement-slug op-smoke
```

Verify layout:

```powershell
Get-ChildItem $TEST_ROOT -Recurse -Name
```

Must include: `.env`, `engagements\op-smoke\engagement.yaml`.

There is no `rules\` directory. The Sigma/OPSEC rule pack died with the
pre-flight gate — if you see one, it is left over from an older run against this
root.

`transport.token` will NOT appear under `$TEST_ROOT` yet — not because `init`
never mints one, but because it mints it at `REDROBIN_ROOT` (defaulting to
`~\.redrobin`), which `--root` does not override. Step 1.2 runs from
`$TEST_ROOT`, where the `.env` `init` just wrote pins `REDROBIN_ROOT`, so the
daemon mints `$TEST_ROOT\transport.token` there — along with the rest of the
layout it creates on boot (`shared\`, `engagements\archived\`, `kb-source\`).

### Activate the venv (do this in EVERY terminal you open)

`uv run <cmd>` searches CWD upward for `pyproject.toml`. After `cd $TEST_ROOT`
the project root is no longer findable from CWD, so `uv run redrobin doctor`
fails with `program not found`. Two ways to fix:

- **Activate the venv once** (recommended): `redrobin` lands on PATH, then
  commands work from anywhere. Re-activate in every new terminal you open.
- **Or use `uv run --project`** with the full repo path on each call:
  `uv run --project C:\Users\jcspi\Documents\dev-projects\Red-Robin redrobin doctor`.

This guide uses the venv-activation pattern from here on.

```powershell
# From the repo root (still here from Setup):
& .\.venv\Scripts\Activate.ps1
# Prompt should now show (red-robin) or similar prefix.

# cd to the test root so dotenv loads (Slice 2 pinned to Path.cwd() / ".env")
cd $TEST_ROOT
```

### Step 1.1 — doctor

```powershell
redrobin doctor --quick
redrobin doctor
```

**Expect (quick mode):**

```
[*] Red Robin doctor (preflight check)
Configuration:
  ROOT:               C:\Users\jcspi\redrobin-smoke
  ENGAGEMENT:         op-smoke
  ANTHROPIC_API_KEY:  set
  VOYAGE_API_KEY:     set
  ROOT_EXISTS:        yes (...)
[+] Doctor finished: OK (quick mode - skipped network probes)
```

There is no `RULES:` line any more — doctor stopped counting rule YAMLs when the
pre-flight gate was removed.

**Expect (full mode):** above plus a `Connectivity:` block with
`[OK ] anthropic   <ms>` and `[OK ] voyage   <ms>`, then
`[+] Doctor finished: OK`.

**Transient failures:**
- `[FAIL] anthropic   HTTP 401`: your key is wrong, edit `.env`.
- `[FAIL] anthropic   HTTP 503` or `HTTP 429`: Anthropic's API is overloaded
  or rate-limiting you. Retry once or twice (`redrobin doctor`). If it
  persists >2 min, check https://status.anthropic.com. Does NOT block Step
  1.2 — `daemon start` validates key PRESENCE not connectivity; the assistant
  starts answering as soon as Anthropic recovers.
- Same applies to Voyage at https://status.voyageai.com.

Doctor's failure footer reads `DEGRADED (command capture still works)` — meaning
a probe failed, so the LLM-backed surfaces (assistant, enricher, summariser, KB
retrieval) are impaired, but `.cna` → `/v1/events` capture is unaffected because
it touches no external API. It does NOT mean `daemon start` will run in
`--allow-degraded` mode. Those are independent: `daemon start` only sees
`--allow-degraded` if you pass the flag explicitly or set
`REDROBIN_ALLOW_DEGRADED=1`.

### Step 1.2 — daemon start (terminal A, keep open)

**The assistant is OFF by default** (`assistant_enabled=False`), and `init` does
not write the flag to `.env`. Step 1.4's pass condition is "chat answers", so you
must opt in here or Tier 1 cannot pass:

```powershell
$env:REDROBIN_ASSISTANT_ENABLED = "1"
uv run redrobin daemon start --host 127.0.0.1 --port 7777
```

Set it in *this* terminal before starting the daemon — it gates the daemon's
assistant, KB and chat-transcript wiring at boot, not the TUI. To make it stick,
add `REDROBIN_ASSISTANT_ENABLED=1` to `$TEST_ROOT\.env`.

**Expect:** Slice 7 banner to stderr:

```
======================================================================
  Red Robin v0.1.0
======================================================================
  Listening:  http://127.0.0.1:7777
  Root:       C:\Users\jcspi\redrobin-smoke
  Engagement: op-smoke
  Anthropic:  set
  Voyage:     set
  L5 window:  25 (every 30.0s)
======================================================================
```

If `Anthropic: MISSING (degraded)`, the `.env` wasn't loaded — confirm
`cd $TEST_ROOT` happened in this shell.

If a `Scope:` line appears under `Engagement:`, the engagement resolved with an
empty in-scope list and the assistant will reason without a target boundary.
Fix `engagements\op-smoke\engagement.yaml` and restart before continuing — the
generated template ships a non-empty `in_scope`, so an empty one means the file
was edited or replaced.

### Step 1.3 — event ingestion path (terminal B)

`POST /v1/events` is the daemon's only command-facing entry point. It validates
the envelope + the per-type payload schema, dedups, appends to the engagement's
`events.jsonl`, and republishes onto the in-process bus so the reducer and the
TUI WebSocket see it. This step proves that whole chain without an adapter.

```powershell
$TEST_ROOT = "$env:USERPROFILE\redrobin-smoke"
$token = Get-Content "$TEST_ROOT\transport.token"
$H = @{ Authorization = "Bearer $token"; "Content-Type" = "application/json" }

# Health + readiness
Invoke-RestMethod http://127.0.0.1:7777/v1/healthz
# Expect: status=ok
Invoke-RestMethod http://127.0.0.1:7777/v1/readyz
# Expect: status=ready  (status=not_ready means the lifespan hasn't finished booting)

# A beacon checks in.
$initial = @'
{"spec_version":"0.1.0",
 "id":"evt_01HZX9KMMCSMKE0000000000AA",
 "ts":"2026-07-24T12:00:00.000Z",
 "source":{"adapter":"manual-smoke","version":"1.0.0","engagement_id":"op-smoke"},
 "event":{"type":"beacon.initial","bid":"B-1","host":"WS-SMOKE-01","user":"corp\\admin",
          "integrity_level":"High","arch":"x64","pid":4321,"ppid":2200}}
'@
Invoke-RestMethod http://127.0.0.1:7777/v1/events -Method Post -Headers $H -Body $initial

# The operator runs a command. Red Robin observes it; nothing is gated.
$input = @'
{"spec_version":"0.1.0",
 "id":"evt_01HZX9KMMCSMKE0000000000AB",
 "ts":"2026-07-24T12:00:05.000Z",
 "source":{"adapter":"manual-smoke","version":"1.0.0","engagement_id":"op-smoke"},
 "event":{"type":"beacon.command_input","bid":"B-1","operator":"neo",
          "command":"whoami","raw":"whoami /groups","correlation_id":"corr-smoke-1"}}
'@
Invoke-RestMethod http://127.0.0.1:7777/v1/events -Method Post -Headers $H -Body $input
```

**Expect:** each POST returns `ok: True, stored: True`. `stored` is `True` only
when the active engagement directory exists — `stored: False` means the
engagement wasn't resolved, not that ingestion failed.

Re-POSTing the `beacon.command_input` verbatim returns
`ok: True, stored: False, duplicate: True` — the dedup gate keys on
`bid|correlation_id|type`. Change `correlation_id` to send a genuinely new
command. `beacon.initial` is deliberately NOT deduped (it is idempotent at the
reducer), so re-sending it always stores.

Then confirm the reducer landed it:

```powershell
Get-Content "$TEST_ROOT\engagements\op-smoke\working_memory.json" |
  ConvertFrom-Json | Format-List
```

Note the **per-engagement** path. With `REDROBIN_ENGAGEMENT_SLUG=op-smoke`
resolved (it is — `init` wrote it to `.env`), state lives under
`engagements\<slug>\`. The root-level `working_memory.json` is only the degraded
fallback used when no engagement resolves, so reading it here finds nothing.

**Expect:** `sessions` contains `B-1` on host `WS-SMOKE-01`. The command is
written to L3 episodic history (`engagements\op-smoke\episodic.db`), not to the
working-memory JSON.

**Negative check — the gate is gone.** The pre-flight verdict route must not
exist (`try`/`catch` rather than `-SkipHttpErrorCheck`, so this works on
PowerShell 5 too):

```powershell
try {
  Invoke-RestMethod http://127.0.0.1:7777/v1/verdict/quick -Method Post -Headers $H -Body '{}'
  "FAIL - the route still answers"
} catch {
  $_.Exception.Response.StatusCode.value__   # Expect: 404
}
```

There is likewise no `/v1/adapters/events` WebSocket. The only WebSocket the
daemon serves is `/v1/tui`.

### Step 1.4 — TUI (terminal C)

Terminal C is a new shell session, so activate the venv from the repo first, then
`cd` into the test root (same pattern as terminals A and B):

```powershell
cd C:\Users\jcspi\Documents\dev-projects\Red-Robin
& .\.venv\Scripts\Activate.ps1
cd $env:USERPROFILE\redrobin-smoke
redrobin tui `
  --connect-ws ws://127.0.0.1:7777/v1/tui `
  --connect-http http://127.0.0.1:7777
```

(`--connect-http` is accepted for CLI compatibility but unused — the Textual
client never POSTs to the daemon over HTTP. Everything travels over the single
`/v1/tui` socket: daemon frames down, and operator chat back up.)

**Expect:**
- 3 panes: **Sessions** (left), **Assistant Chat** (center), **Working Memory**
  (right), plus a topbar showing the engagement id + connection state
- Sessions shows `B-1 / WS-SMOKE-01 / corp\admin / High` from Step 1.3 (the
  `High` cell renders highlighted)
- Type a question in the chat input + Enter → the line echoes, a
  `⠋ Red Robin is thinking…` spinner appears after ~200 ms, then the assistant's
  answer renders in the pane. The chat input is a question box, **not** a
  command box — nothing you type here reaches a beacon.
- Press **i** → Inspector overlay; **Esc** → closes it
- Press **?** → keymap overlay; **Esc** or **?** → closes it
- Press **c** → focuses the chat input
- Press **q** (or **Ctrl+C**) → TUI exits

There is no Verdicts pane and no verdict counters in the topbar — the center
pane is the Assistant Chat.

### Step 1.5 — clean shutdown

In terminal B (venv already activated from Setup):

```powershell
redrobin daemon stop
```

**Expect:** `daemon stopped (pid=<n>)`, no orphan task tracebacks in terminal A.

### Tier 1 success criteria

| Check | Pass condition |
|-------|----------------|
| Step 1.3 ingest | Both POSTs return `ok: True, stored: True` |
| Step 1.3 dedup | Verbatim re-POST of the command returns `duplicate: True` |
| Step 1.3 reducer | `engagements\op-smoke\working_memory.json` `sessions` contains `B-1` |
| Step 1.3 no gate | `POST /v1/verdict/quick` returns 404 |
| Step 1.4 TUI | 3 panes render, chat answers, `i` / `?` overlays work |
| Step 1.5 stop | Clean exit, no errors |

**If any of these fail, stop here. Don't move to Tier 2.**

---

## Tier 2 — Sliver-Mock Adapter

**Goal:** validate the full wire protocol end-to-end without CS. Tier 2 catches
any drift in `beacon.initial` / `beacon.command_output` / `host.observation` /
`credential.observed` payload handling, adapter session lifecycle, and reducer
ingestion into working memory.

This is the **last cheap tier** before you spin up Cobalt Strike. Trust it.

### Setup

Reuse the smoke setup from Tier 1 (`$TEST_ROOT` = `~/redrobin-smoke`). Daemon
must be running (Step 1.2). Token must exist (`transport.token`).

What sliver-mock does, in plain terms:

1. Reads a scenario JSON from `adapters/sliver-mock/scenarios/<name>.json`.
2. POSTs `/v1/adapters/connect` with its manifest → daemon assigns a session ID.
3. Replays each event envelope from the scenario via POST `/v1/events`, with
   `delay_ms` timing between events (use `--speed 5` to fast-forward).
4. POSTs `/v1/adapters/disconnect`.

Available scenarios: `linux_demo`, `acme_corp`. Inspect them:

```powershell
cd C:\Users\jcspi\Documents\dev-projects\Red-Robin
Get-Content adapters/sliver-mock/scenarios/acme_corp.json | Select-Object -First 30
```

`acme_corp` is the richer scenario — Windows host, credentials, multiple
commands. Recommended for first run.

### Step 2.1 — start daemon (terminal A)

```powershell
cd $env:USERPROFILE\redrobin-smoke
uv run redrobin daemon start --host 127.0.0.1 --port 7777
```

### Step 2.2 — replay the acme_corp scenario (terminal B)

```powershell
cd C:\Users\jcspi\Documents\dev-projects\Red-Robin
$env:REDROBIN_TOKEN = Get-Content "$env:USERPROFILE\redrobin-smoke\transport.token"

uv run python adapters/sliver-mock/adapter.py `
  --scenario acme_corp `
  --daemon http://127.0.0.1:7777 `
  --speed 5
```

**Expect** (stderr):

```
[sliver-mock] connected as session ses_...
[sliver-mock] sent beacon.initial
[sliver-mock] sent host.observation
[sliver-mock] sent beacon.command_input
[sliver-mock] sent beacon.command_output
[sliver-mock] sent credential.observed
... (more events as the scenario progresses)
[sliver-mock] disconnected
```

In terminal A you get one uvicorn access line per POST —
`"POST /v1/events HTTP/1.1" 200 OK` — plus, for each `beacon.command_output`
that clears the enricher's trigger gate, one `enrichment ok: …` INFO line from
`redrobin.application.agents.enricher.enricher` (the note under the Tier 2
success criteria has the full reading). The reducer stays quiet: its only log
calls are `logger.exception` on a failed event, an episodic write, or a
milestone apply (`application/working_memory_reducer.py`), so a silent reducer
is a healthy one. Be precise about what the reducer folds into working
memory — it handles exactly four types: `beacon.initial` (sessions),
`beacon.command_input` (an L3 episode, no working-memory change),
`working_memory.deltas`, and `engagement.milestone`.

**`host.observation` and `credential.observed` are validated and persisted to
`events.jsonl`, but they drive no panel** — the reducer ignores them. Hosts and
credentials reach working memory only via `working_memory.deltas`, which the LLM
enricher produces from `beacon.command_output`. So Step 2.3's hosts/credentials
depend on a working `ANTHROPIC_API_KEY`, not on the structured events the
scenario sends.

### Step 2.3 — verify working memory ingested the scenario

In terminal B after sliver-mock finishes:

```powershell
$token = Get-Content "$env:USERPROFILE\redrobin-smoke\transport.token"
$H = @{ Authorization = "Bearer $token" }

# The working memory state is on disk as JSON (per-engagement path). Inspect:
Get-Content "$env:USERPROFILE\redrobin-smoke\engagements\op-smoke\working_memory.json" |
  ConvertFrom-Json | Format-List
```

**Expect:** `sessions` contains an entry for beacon `win_ws01` —
`hostname: WS-FINANCE-04.acme.local`, `user: acme\jdoe` — populated straight
from the scenario's `beacon.initial`. No LLM involved (see the paragraph above
Step 2.3).

The LLM enricher owns five arrays — `hosts`, `credentials`, `findings`,
`tools_used`, `defenses_observed` (the merge in `enricher.py`'s `_enrich`) —
and for **this scenario**, `hosts` and `credentials` specifically are expected
to stay **empty** even with a working `ANTHROPIC_API_KEY`. That is correct,
not a failure:

- `hosts` stays empty because the scenario's lone `beacon.command_output`
  (the `net group "Domain Admins"` listing) names no host at all, and the
  enricher is separately grounded to never re-emit a beacon's own host/user as
  newly-discovered (`_build_enricher_user_message`, #105) — so even the one
  host this beacon has (`WS-FINANCE-04.acme.local`) is withheld from
  extraction.
- `credentials` stays empty because that output is bare usernames
  (`Administrator`, `svc_backup`, `jdoe`) with no secret material, and
  `WorkingMemoryDeltas.from_payload` drops any credential lacking a
  `secret`/`ntlm_hash` (#114 — a username alone is not a credential).

`findings` is NOT blocked the same way — nothing stops the enricher from
logging an observation about the Domain Admins listing itself, so a non-empty
`findings` array here is possible. It just isn't guaranteed, so it is not a
pass/fail signal either way.

To confirm the wire path itself, independent of the LLM, check the append-only
capture log instead:

```powershell
Select-String -Path "$env:USERPROFILE\redrobin-smoke\engagements\op-smoke\events.jsonl" `
  -Pattern '"host\.observation"|"credential\.observed"'
```

**Expect:** 3 matching lines (2x `host.observation`, 1x `credential.observed`)
— proof the daemon validated and persisted these 3 of the scenario's 7 events,
independent of the LLM. Both types fall through `_dispatch`'s `else` in
`working_memory_reducer.py`, and so does `beacon.command_output`: three of this
scenario's five event types are ones the reducer does not handle (the paragraph
above Step 2.3 lists the four it does).

This does not by itself prove the other 4 events landed — `beacon.initial`, 2x
`beacon.command_input`, `beacon.command_output`. Step 2.3's `sessions` check
covers `beacon.initial`. Step 2.2's `[sliver-mock] sent <type>` line does not
cover the rest: sliver-mock prints it after `raise_for_status()`
(`adapters/sliver-mock/adapter.py`), so it attests that `POST /v1/events`
answered 2xx — envelope and payload passed schema validation and the daemon
accepted them — and nothing about persistence. That route answers 200 with
`stored: false` and appends nothing when the dedup gate hits, and equally when
no engagement is active or its directory is missing (`api/events_router.py`).

### Step 2.4 — TUI shows the simulated state

Open TUI (terminal C):

```powershell
cd $env:USERPROFILE\redrobin-smoke
uv run redrobin tui `
  --connect-ws ws://127.0.0.1:7777/v1/tui `
  --connect-http http://127.0.0.1:7777
```

**Expect:** Sessions (left) shows the scenario's beacon — `win_ws01` /
`WS-FINANCE-04.acme.local` / `acme\jdoe` — the deterministic half, no LLM
required. Working Memory's `hosts` and `credentials` arrays are expected to
stay empty for this scenario (Step 2.3 explains why) — don't wait for panels
that won't move. The Assistant Chat pane (center) may carry an unprompted
advisory if the replayed activity cleared the assistant's trigger gate — that
is a bonus, not a pass condition, since the gate is deliberately quiet (dedup +
novelty + cooldown). Exit with `q`.

### Step 2.5 — replay at real-time speed (optional, ~3 min)

Drop `--speed 5` to see the scenario play out at the timing it was recorded
at. Useful for demos.

```powershell
uv run python adapters/sliver-mock/adapter.py --scenario acme_corp
```

### Step 2.6 — clean shutdown

```powershell
cd $env:USERPROFILE\redrobin-smoke
uv run redrobin daemon stop
```

### Tier 2 success criteria

| Check | Pass condition |
|-------|----------------|
| Step 2.2 events sent | All scenario events show `[sliver-mock] sent <type>` |
| Step 2.3 sessions | `sessions` contains beacon `win_ws01` / `WS-FINANCE-04.acme.local` (reducer-driven, deterministic, no LLM) |
| Step 2.3 wire path | `events.jsonl` contains the 2x `host.observation` + 1x `credential.observed` lines the scenario sent (deterministic proof the wire path worked, independent of the LLM) |
| Step 2.4 TUI memory pane | Sessions pane shows the beacon; `hosts`/`credentials` in Working Memory are expected EMPTY for this scenario (not a failure — see Step 2.3) |
| No daemon crash | Terminal A shows no tracebacks |

**`hosts`/`credentials` staying empty in working memory is expected for
`acme_corp`** — it is not a symptom of a missing/failing `ANTHROPIC_API_KEY`,
and re-checking or replacing the key will not change the outcome. Step 2.3 has
the full reason: the one command output names no host at all, the beacon's own
host is separately grounded out of extraction, and the output carries no secret
material for a credential to attach to.

**The daemon prints a per-event `enrichment ok` line on every completed
extraction**, so terminal A is the first place to look. `redrobin daemon start`
passes `log_config=build_uvicorn_log_config("info")` to uvicorn
(`cli_helpers/daemon_cmd.py`), which adds a `redrobin` logger at INFO with its
own timestamped stderr handler — so every `logger.info` under the `redrobin.*`
tree reaches terminal A. The enricher's success path
(`agents/enricher/enricher.py`, the `logger.info` just after it publishes
`working_memory.deltas`) renders as:

```
<ts> INFO    redrobin.application.agents.enricher.enricher | enrichment ok: command=… beacon=… chunks=… deltas=… latency_ms=…
```

The line's *presence* is the signal, whatever `deltas=` reads — it proves the
enricher ran a pass to completion on that output, which the empty
`hosts`/`credentials` arrays cannot tell you on their own. Don't over-read the
count: `deltas` sums hosts + credentials + findings + tools + defenses, so a
non-zero value is possible here even with both arrays empty (Step 2.3). No such
line at all means the enricher never got that far; the three gates below are
what stop it:

- **The key.** Step 2.1 starts the daemon without `--allow-degraded`. With
  `REDROBIN_ALLOW_DEGRADED` also unset, a missing `ANTHROPIC_API_KEY` makes
  `redrobin daemon start` print `ERROR: ANTHROPIC_API_KEY required for the
  assistant/enricher/summariser` and exit 2 *before* binding the port
  (`infrastructure/boot_validation.py`) — so a daemon that started at all had
  the key. If `REDROBIN_ALLOW_DEGRADED=1` is set in that shell, a missing key
  instead prints `WARN: ANTHROPIC_API_KEY missing — running degraded (no
  assistant, enrichment, or summaries)` and boot continues, and the Step 1.2
  banner reads `Anthropic:  MISSING (degraded)` rather than `Anthropic:  set`.
  Both go to stderr in terminal A, and the banner prints at every start. The
  banner and the enricher's LLM-client factory read the same
  `ANTHROPIC_API_KEY` environment variable, so `Anthropic:  set` means the
  factory found a key too. The factory says so itself now — with no key,
  `build_enricher_llm` (`agents/enricher/llm.py`) logs one INFO line at boot,
  `WM enricher: ANTHROPIC_API_KEY missing — running degraded (no enrichment)`.
- **`REDROBIN_ENRICHMENT_ENABLED=0`** also short-circuits `_enrich`, and
  neither the banner nor `doctor` reports it — the setting is passed to the
  enricher at boot and never echoed. Check the environment of the shell you
  started the daemon in, and `$TEST_ROOT\.env`. It defaults to on.
- **The trigger gate** skips output shorter than
  `REDROBIN_ENRICHMENT_MIN_OUTPUT_CHARS` (default 8) or a command verb listed
  in `REDROBIN_ENRICHMENT_DENYLIST` (empty by default) — `enricher/trigger.py`.
  This scenario's one output is 73 characters, so it clears the floor unless
  you set a denylist yourself.

**Do not read anything into the absence of `enrichment failed` either.** That
message fires only on an LLM/network error or on output no chunk of which could
be parsed (`enricher.py`'s `_emit_failed`, two call sites) — a missing key, a
disabled enricher and a clean run that extracted nothing all produce no
`enrichment failed` line, so its absence separates none of them. Its *presence*
is meaningful (it is logged at ERROR, and one is a real alarm). Read it
alongside `enrichment ok`, which is the line that *does* separate those cases:
present means a completed pass, absent means the enricher never ran one.

Only if `sessions` is ALSO empty (that path is deterministic and needs no LLM)
are you looking at a wire-protocol bug — the class Phase 2 had with
`/v1/events` not bus-publishing. In that case, stop and report.

---

## Tier 3 — CS Local (Load `.cna`, No Live Beacon)

**Goal:** validate that `adapters/cobaltstrike/redrobin.cna` parses cleanly in
a real Aggressor Console and that its handshake against the daemon succeeds.
**No beacon required.**

Red Robin never reaches out to the team server itself — the `.cna` is the only
thing that talks to CS, and it only ever POSTs captured activity to the daemon.
There is no adapter YAML and no daemon-side team server login to configure.

### Pre-reqs

- CS Team Server licensed and running (Digital Ocean or local). Get the URL
  and credentials.
- CS Client (`cobaltstrike-client.jar`) installed on a machine that can reach
  both the team server and the Red Robin daemon (port 7777).
- The daemon's host:port must be reachable from the CS Client machine. If the
  daemon runs on your laptop and the CS Client runs on the same laptop,
  `127.0.0.1:7777` works. If they're on different machines, you'll need to
  bind the daemon to `0.0.0.0` and open the port.

### Step 3.1 — start the daemon

```powershell
cd $TEST_ROOT
uv run redrobin daemon start --host 0.0.0.0 --port 7777
```

(`0.0.0.0` instead of `127.0.0.1` if your CS Client is on a different machine.)

**Expect:** the Tier 1 startup banner, unchanged. Nothing CS-specific appears
at boot — the daemon learns about the adapter only when the `.cna` handshakes
in Step 3.3.

### Step 3.2 — configure `redrobin.cna` URL and token

**URL** — edit the Sleep global near the top of
`adapters/cobaltstrike/redrobin.cna`:

```sleep
$REDROBIN_URL = "http://127.0.0.1:7777";  # or http://YOUR-LAPTOP-IP:7777 if daemon is remote
```

**Token** — do NOT paste it into the `.cna`. The top-level
`$REDROBIN_TOKEN = ""` is overwritten at boot by `redrobin_load_token()`, which
reads a hardcoded path:

```
<user.home>/.redrobin/transport.token
```

**This is Tier 3's sharpest edge.** Your test root is
`~\redrobin-smoke`, but the `.cna` will only ever read `~\.redrobin`. Before
loading the script, copy the token across (or point the daemon at the real root
for the CS tiers):

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.redrobin" | Out-Null
Copy-Item "$env:USERPROFILE\redrobin-smoke\transport.token" `
          "$env:USERPROFILE\.redrobin\transport.token" -Force
```

If that file is missing the `.cna` throws on load from the `FileReader` — a
FileNotFound, not a Red Robin warning.

### Step 3.3 — load `.cna` in Aggressor Console

In CS Client, with the team server connected:

1. **Cobalt Strike** menu → **Aggressor Scripts**
2. **Load** button → navigate to
   `C:\Users\jcspi\Documents\dev-projects\Red-Robin\adapters\cobaltstrike\redrobin.cna`
3. Watch the **Script Console** (bottom of the Aggressor Scripts dialog) for:
   - Parse errors (syntax issues — STOP, fix in `.cna`, reload)
   - Warnings about undefined functions or scope issues
   - The two load-time lines the script prints on success:

```
[Red Robin] handshake OK; session=ses_...
[Red Robin] .cna loaded; capture active (observe-only, no interception).
```

**If parse errors appear**, copy the exact error message and the line number.
That's a `.cna` syntax bug we need to fix in source.

**If you see `[Red Robin] FATAL: Java <version> detected`**, the CS Client JVM
is older than Java 11 and the script aborts by design — the HTTP helper JAR
needs `java.net.http`. Install a modern JVM (e.g. Temurin 21) and reload.

**If the handshake retries and fails** (`handshake attempt 1/3 failed` … then
`handshake failed after 3 attempts`), the daemon is unreachable or the token is
wrong. Fix Step 3.2 and **reload the script** — the retry loop does not resume
on its own.

### Step 3.4 — confirm the script registers no commands (no beacon needed)

The `.cna` contains **no `alias` definitions**. It hooks
`on beacon_input`, `set BEACON_OUTPUT`, and `on beacon_initial` — all
observation-only. The check here is a negative one: Red Robin must not have
taken over any operator command.

In the CS Client's main console, type a command the old pre-flight gate used to
intercept:

```
mimikatz "sekurlsa::logonpasswords"
```

**Without a beacon selected**, CS complains "no beacon selected" (or similar).
That is the correct, stock behaviour — CS's own handler answered. What you must
**not** see is any `[Red Robin]` line **in response to the command** (the two
load-time lines from Step 3.3 are expected and unrelated), any gate/verdict
message, or any hint that the command was inspected before dispatch. If Red
Robin speaks here, an alias override survived the gate removal and the `.cna`
needs fixing.

Nothing is POSTed to the daemon at this point either: `on beacon_input` only
fires when a command is actually tasked to a real beacon, which is Tier 4.

### Tier 3 success criteria

| Check | Pass condition |
|-------|----------------|
| 3.1 daemon start | Banner prints; daemon listening on the chosen host:port |
| 3.3 .cna load | No parse errors; `handshake OK` + `capture active` lines print |
| 3.4 no interception | Console behaves exactly like stock CS; no `[Red Robin]` output in response to the command |

**Common failure modes:**
- `.cna` parse error on load → fix syntax in source, commit, reload
- Handshake fails, daemon never sees `POST /v1/adapters/connect` →
  `$REDROBIN_URL` wrong, or network unreachable between CS Client machine and
  daemon
- Daemon sees the POST but returns 401 → token mismatch; the `.cna` read a
  different `transport.token` than the daemon is using (see Step 3.2)
- `.cna` throws FileNotFound on load → `~\.redrobin\transport.token` is missing
- A `[Red Robin]` gate/verdict message appears in the console → an alias
  override survived the gate removal. Stop and report; this is the exact
  regression Tier 3 exists to catch.

---

## Tier 4 — CS Full E2E with Beacon

**Goal:** end-to-end validation across the two live paths, plus the negative
guarantee that binds them:

1. **Passthrough capture (the negative guarantee)** — operator types a command
   in the CS console → it dispatches to the beacon **immediately and unchanged**
   → `on beacon_input` mirrors it to the daemon as `beacon.command_input` via
   `POST /v1/events`. Red Robin is never in the path.
2. **Output observation** — beacon returns output → `set BEACON_OUTPUT` hook
   fires (always returning `$2` unchanged) → `POST /v1/events` with
   `beacon.command_output` → enricher + reducer ingest → working memory updates
   → TUI Working Memory pane reflects new credentials/hosts
3. **Assistant advisory** — the assistant reads that state and speaks in the
   TUI's Assistant Chat pane, with an `opsec_risk` of INFO/WARN/DANGER. It is
   advice after the fact, not a decision before it.

There is no TUI-dispatch path. The Textual client cannot dispatch to a beacon —
it sends only `operator.question` / `operator.answer` upstream — and the daemon
has no outbound path to the team server at all. The CS REST client that once
carried the gate's PASS-verdict dispatch was orphaned when the pre-flight gate
was removed, and deleted in v0.1.x once nothing called it.

### Pre-reqs

- Tier 3 passing (CS adapter green, `.cna` loads with `handshake OK`, console
  behaves like stock CS)
- **A real beacon** in a lab environment. Options:
  - The user's `red-team-playground` repo (Terraform-managed GOAD-Light on
    Digital Ocean — per the memory, this is production-grade and deployed)
  - Local VM running Windows 10/11 with EDR off (or with EDR for realistic
    testing) and a beacon delivered via your phishing/initial-access toolchain
  - A purpose-built test VM with a beacon hand-deployed via `psexec` or RDP
- **Disposable target** — don't test on customer assets. Use lab only.
- The lab beacon must be able to check in to your team server.

### Step 4.1 — deploy beacon

Use whatever delivery you normally use for CRTO-style labs:

- GOAD-Light operator workflow (your existing playground)
- HTML smuggling → .hta → PowerShell loader (CRTO Module 5 territory)
- Direct payload drop via SSH/RDP for "I just want a beacon, skip the chain"

Confirm in CS console: the beacon appears in the beacon table with normal
fields (`user`, `host`, `pid`, etc.).

If your operator user shows up as `corp\admin *` (trailing space-asterisk) in the
CS beacon table, that's the `binfo $bid "user"` value the `.cna`'s
`redrobin_integrity_level` sub keys off to emit `integrity_level: "High"` on
`beacon.initial`. Without the asterisk it emits `"Medium"` — the signal is
binary by design, so `Low`/`System` never appear. Either way the `.cna` strips
the trailing ` *` before putting `user` on the wire, so the TUI's Sessions pane
shows a clean `corp\admin`.

Also open the TUI now and leave it open for the rest of Tier 4 — it is the only
surface Red Robin speaks on:

```powershell
cd $env:USERPROFILE\redrobin-smoke
uv run redrobin tui `
  --connect-ws ws://127.0.0.1:7777/v1/tui `
  --connect-http http://127.0.0.1:7777
```

The beacon should appear in the Sessions pane from the `beacon.initial` the
`.cna` emitted on check-in (or from its load-time backfill, if the beacon
checked in before you loaded the script).

### Step 4.2 — Path 1: Passthrough capture (the negative guarantee)

In the CS console, with the lab beacon selected, type a command the old gate
used to hard-block:

```
mimikatz "sekurlsa::logonpasswords"
```

**Expect:**

- The command **dispatches immediately**, exactly as it would with no Aggressor
  script loaded. No confirmation, no delay, no interception.
- **Zero `[Red Robin]` output in the Beacon console.** The console is now silent
  by design — every Red Robin utterance goes to the TUI chat instead.
- **Terminal A (daemon)** logs `POST /v1/events` carrying
  `type: beacon.command_input`, `command: mimikatz`,
  `raw: 'mimikatz "sekurlsa::logonpasswords"'`, plus the `correlation_id` the
  `.cna` forwards from Aggressor's `$5`.

**Critical check:** the beacon **must** receive the command. Verify in the
beacon's task list (`Sessions → right-click beacon → Tasks`) — the mimikatz task
must be there. A missing task means something is still intercepting.

**Second critical check:** kill the daemon and repeat the command. It must still
dispatch. The `.cna`'s HTTP helper is fail-open (`redrobin_http_post` returns
`""` on any error), so a dead daemon costs you capture, never tasking. You will
see a `[Red Robin] daemon connection refused at …` warn line in the Script
Console — that is the one place the `.cna` still speaks, and it is a transport
warning, not a gate. Restart the daemon before continuing.

### Step 4.3 — Path 2: Output observation

With the beacon active, run a recon command that produces parsable output.
Mimikatz dumping NTLM hashes is ideal — Step 4.2 already ran one.

Wait for the beacon to execute and return output. The `.cna`'s `BEACON_OUTPUT`
hook fires, POSTs `/v1/events` with `type: beacon.command_output` and the output
text, and returns the output unchanged so your console display is untouched.

**Expect:**

- **Daemon log (terminal A)**: `POST /v1/events`, then enricher activity. The
  reducer no longer regex-parses command output — extraction is the LLM
  enricher's job, so this step needs a working `ANTHROPIC_API_KEY`.
- **Working memory file**: new entries.

```powershell
Get-Content "$env:USERPROFILE\redrobin-smoke\engagements\op-smoke\working_memory.json" |
  ConvertFrom-Json | Format-List
```

Look for `credentials` containing the dumped users + NTLM hashes, `hosts`
containing the beacon's hostname, etc.

- **TUI Working Memory pane**: the daemon pushes a fresh snapshot over
  `/v1/tui`, so the pane updates in place — no restart needed.

**Display check:** the mimikatz output in the CS console must look exactly as it
would with no script loaded. `set BEACON_OUTPUT` is a filter hook; a bug there
would visibly mangle or swallow operator output.

### Step 4.4 — Path 3: Assistant advisory (requires a real LLM key)

This is where Red Robin actually speaks. With the engagement state now populated
by Steps 4.2–4.3, watch the TUI's **Assistant Chat** pane.

**Expect:**

- An unprompted advisory may appear, rendered with an `opsec_risk` tag —
  `INFO` / `WARN` / `DANGER` (the surviving `Severity` vocabulary). The
  assistant is deliberately quiet: `TriggerGate` enforces dedup by
  `correlation_id`, a novelty check on the state signature, and a cooldown
  window, and the model itself applies a materiality threshold on top. Silence
  after a single command is normal, not a failure.
- Ask it something directly instead — press **c**, type a question about the
  engagement, Enter. The `⠋ Red Robin is thinking…` spinner appears, then a
  grounded answer citing the working-memory state you just built.
- If the assistant asks you to confirm something, the confirm bar
  (`Red Robin needs your call`) reveals above the input and the placeholder
  flips; answer `/y` or `/n`.

**The point of this step:** the advisory arrives *after* the command already
ran. Nothing here can un-run mimikatz. If you find yourself waiting on Red Robin
before a command completes, that is a regression.

### Tier 4 success criteria

| Path | Pass condition |
|------|----------------|
| 4.2 Passthrough | mimikatz dispatches immediately and appears in the beacon task list; zero `[Red Robin]` output in the Beacon console; daemon logs `beacon.command_input` |
| 4.2 Fail-open | With the daemon down, the command still dispatches |
| 4.3 Output observation | Console output visually unchanged; mimikatz output extracted into working memory, visible in the TUI Working Memory pane |
| 4.4 Assistant advisory | The assistant answers a direct question in the chat pane, grounded in that state |

If all of these hold, the capture-and-advise boundary is intact end to end:
the operator's command path is untouched, and Red Robin's entire contribution
arrives on its own surface, after the fact.

---

## Troubleshooting (cross-tier)

### `redrobin doctor` fails on Anthropic probe

- `HTTP 401`: API key wrong. Re-issue from
  https://console.anthropic.com → Keys.
- `Connection refused` / `Connection timeout`: corporate proxy or DNS
  blocking `api.anthropic.com`. Try `Invoke-RestMethod https://api.anthropic.com/v1/messages -Method Post` directly to confirm.
- `HTTP 429`: rate limited. Wait, retry.

### `redrobin doctor` fails on Voyage probe

Same patterns. Voyage console at https://dashboard.voyageai.com.

### Daemon banner shows `MISSING (degraded)` despite `.env` being present

You're not running from a directory that contains the `.env`. `cd` to the
directory that has `.env` before running `redrobin daemon start`. Slice 2
deliberately pins dotenv to `Path.cwd() / ".env"` and does NOT walk parent
directories (prevents cross-engagement secret leak).

### `redrobin daemon stop` says "no daemon running" when one IS running

PID file path mismatch. Set `REDROBIN_PID_FILE` env var to the actual path,
or kill manually:

```powershell
Get-Process -Name python | Where-Object { $_.CommandLine -match "redrobin" } | Stop-Process
```

### .cna won't load: "Unknown function: &binfo"

Sleep version too old, or CS version too old. `&binfo` is documented in CS
4.x Aggressor docs. Confirm CS 4.7+ on the team server.

### `.cna` loads but the daemon never sees the handshake or any event

Network. Test reachability from the CS Client machine:

```
# On the CS Client machine:
curl http://YOUR-DAEMON-IP:7777/v1/healthz
```

If that hangs or refuses, fix firewall / bind address before continuing. Then
**reload the script** — the handshake's 3-attempt retry does not resume later.

### A command is intercepted, delayed, or refused in the Beacon console

Red Robin must never do this. The `.cna` defines no `alias` and its three hooks
(`on beacon_input`, `set BEACON_OUTPUT`, `on beacon_initial`) are all
passthrough-safe — `BEACON_OUTPUT` always ends in `return $2`. If a command is
blocked or altered, either an `alias` override was reintroduced or one of the
hooks has a path that fails to return. Treat it as a release blocker: it is the
exact failure the gate removal exists to prevent.

Confirm with a grep — this must return nothing:

```powershell
Select-String -Path adapters\cobaltstrike\redrobin.cna -Pattern '^\s*alias\s'
```

### Beacon console output looks mangled or truncated

`set BEACON_OUTPUT` is a filter hook: whatever it returns is what the operator
sees. Any code path in it that doesn't reach `return $2` eats the operator's
output. Check the early-return guard for Red Robin's own `[Red Robin]` lines
(it returns `$2` deliberately) and the null-client guard around the envelope
builder.

### Working memory has zero credentials despite mimikatz running

Either:
- BEACON_OUTPUT hook isn't installed (check .cna source)
- POST `/v1/events` is failing auth (check daemon log) — the `.cna` reads
  `~\.redrobin\transport.token`, which may not be the token your daemon is using
- The dedup gate swallowed a re-emit: identical `bid|correlation_id|type` returns
  `duplicate: True` and is dropped before the bus publish
- Enricher is short-circuited (no `ANTHROPIC_API_KEY`,
  `REDROBIN_ENRICHMENT_ENABLED=0`, or the LLM provider failed) — the reducer no
  longer regex-parses command output since Plan B2. Scan the daemon terminal for
  `enrichment ok:` — one line per completed extraction, so its absence means the
  enricher never ran. `enrichment failed:` (logged at ERROR) is the other half:
  present means a real alarm, absent proves nothing on its own, because the
  no-key and disabled paths return before any LLM call and log nothing per event
  (Tier 2's note under the success criteria has the full reasoning). The boot
  banner's `Anthropic:  set` vs `MISSING (degraded)` still separates the no-key
  case at a glance

### The assistant never says anything in the chat pane

**Check the enable flag first.** The assistant is off unless the daemon was
started with `REDROBIN_ASSISTANT_ENABLED=1` (default `False`). With it unset the
daemon boots fine, the TUI connects, the chat pane renders — and nothing ever
answers, because the assistant, KB and chat transcript were never wired at boot.
This is the single most common cause of a silent pane. Confirm it in the daemon's
terminal, restart with the flag, and retry before reading further.

Beyond that: unprompted advisories are gated on purpose. `TriggerGate` suppresses an
utterance when the `correlation_id` was already spoken on, when the working-
memory state signature hasn't changed, or when the cooldown window
(`REDROBIN_ASSISTANT_COOLDOWN_SEC`) hasn't elapsed — and the model applies its
own materiality threshold after that. Before debugging, ask it a direct question
(**c**, type, Enter): if it answers, the pipeline is healthy and you were just
watching a quiet gate.

If a direct question also goes unanswered, check `ANTHROPIC_API_KEY` and the
daemon log — and confirm the TUI topbar shows a connected state, since the chat
round-trips over the `/v1/tui` WebSocket.

---

## After Tier 4: what's next

If all four tiers pass:

1. **Merge the topic branch → `dev`**, then `dev` → `main` at release and tag
   (see CONTRIBUTING's PR flow — both branches are protected, PRs only).
2. **DEF CON RTV CFP** — the paper draft under `docs/paper/` needs an honesty
   pass: it was written when the pre-flight gate was the headline feature, and
   Red Robin is now advisory-only.
3. **Sliver-Real adapter** is the v0.2 commitment. `SliverPy` is OFF (abandoned
   2023); use a protoc-generated client from upstream `.proto` files instead.
4. **Mythic adapter** — also v0.2. Note that Mythic's native `opsec_pre()` hook
   is a pre-execution gate, which is precisely the shape Red Robin removed; the
   adapter should use Mythic's observation surface, not its gate.
