# Red Robin v0.1.x — Testing Guide

Four-tier testing playbook. Run them in order. Each tier isolates a layer of the
system so failures point at one cause, not three.

| Tier | What it validates | Time | External deps |
|------|-------------------|------|---------------|
| 1. Local smoke | Daemon boot, `/v1/events` ingestion, bus → reducer → working memory, TUI | ~10 min | Anthropic + Voyage keys |
| 2. Sliver-Mock | Wire protocol end-to-end, adapter session lifecycle, scenario replay | ~15 min | none (no CS license needed) |
| 3. CS local (no beacon) | `redrobin.cna` parses in Aggressor, handshake succeeds, adapter login works | ~30 min | CS Team Server + CS Client |
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
uv run pytest tests/ adapters/cobaltstrike/tests/ -q
```

Expected: `0 failed`. (Adapter tests under `adapters/*/tests/` are deliberately
outside the CI `pytest tests/` run — Tier 0 adds them because the tiers below
exercise the adapter.) If anything fails, stop and investigate before running
any tier.

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

Must include: `.env`, `engagements\op-smoke\engagement.yaml`, `adapters\`.

There is no `rules\` directory. The Sigma/OPSEC rule pack died with the
pre-flight gate — if you see one, it is left over from an older run against this
root.

`transport.token` is NOT written by `init`; it is generated on first use by
whichever process needs it (`daemon start`, `redrobin tui`). Expect it to appear
after Step 1.2, not before — along with the rest of the layout the daemon
creates on boot (`shared\`, `engagements\archived\`, `kb-source\`).

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

```powershell
uv run redrobin daemon start --host 127.0.0.1 --port 7777
```

**Expect:** Slice 7 banner to stderr:

```
======================================================================
  Red Robin v0.1.0
======================================================================
  Listening:  http://127.0.0.1:7777
  Root:       C:\Users\jcspi\redrobin-smoke
  Engagement: op-smoke
  Adapters:   (none)
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
Get-Content "$TEST_ROOT\working_memory.json" | ConvertFrom-Json | Format-List
```

**Expect:** `sessions` contains `B-1` on host `WS-SMOKE-01`. The command is
written to L3 episodic history (`episodic.db`), not to the working-memory JSON.

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
client is a read-only WebSocket observer. It never POSTs to the daemon over
HTTP; operator chat messages go back up the same `/v1/tui` socket.)

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
| Step 1.3 reducer | `working_memory.json` `sessions` contains `B-1` |
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

In terminal A (daemon log), you should see corresponding log lines as events
flow through the bus and the reducer folds them into working memory. Structured
events (`host.observation`, `credential.observed`) land directly; free-text
command output is extracted by the LLM enricher, not by the reducer.

### Step 2.3 — verify working memory ingested the scenario

In terminal B after sliver-mock finishes:

```powershell
$token = Get-Content "$env:USERPROFILE\redrobin-smoke\transport.token"
$H = @{ Authorization = "Bearer $token" }

# The working memory state is on disk as JSON. Inspect:
Get-Content "$env:USERPROFILE\redrobin-smoke\working_memory.json" | ConvertFrom-Json | Format-List
```

**Expect:** `hosts` array contains `WS-FINANCE-04.acme.local` (or similar from
the scenario), `credentials` array contains observed creds, `sessions` array
contains the beacon. Exact values depend on the scenario JSON — open it
side-by-side to cross-check.

### Step 2.4 — TUI shows the simulated state

Open TUI (terminal C):

```powershell
cd $env:USERPROFILE\redrobin-smoke
uv run redrobin tui `
  --connect-ws ws://127.0.0.1:7777/v1/tui `
  --connect-http http://127.0.0.1:7777
```

**Expect:** Working Memory pane (right side) shows the host(s), credentials, and
any findings from the scenario; Sessions (left) shows the scenario's beacons. The
Assistant Chat pane (center) may carry an unprompted advisory if the replayed
activity cleared the assistant's trigger gate — that is a bonus, not a pass
condition, since the gate is deliberately quiet (dedup + novelty + cooldown).
Exit with `q`.

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
| Step 2.3 working memory | At least one host + credential in the JSON |
| Step 2.4 TUI memory pane | Working Memory pane reflects the scenario state |
| No daemon crash | Terminal A shows no tracebacks |

**If hosts/credentials don't show up in working memory** despite events being
sent, that's a wire-protocol bug — the same class of bug Phase 2 had with
`/v1/events` not bus-publishing. Stop and report.

---

## Tier 3 — CS Local (Load `.cna`, No Live Beacon)

**Goal:** validate that `adapters/cobaltstrike/redrobin.cna` parses cleanly in
a real Aggressor Console, that its handshake against the daemon succeeds, and
that the daemon's `cs_adapter_loader` can log in to your team server. **No
beacon required.**

### Pre-reqs

- CS Team Server licensed and running (Digital Ocean or local). Get the URL
  and credentials.
- CS Client (`cobaltstrike-client.jar`) installed on a machine that can reach
  both the team server and the Red Robin daemon (port 7777).
- The daemon's host:port must be reachable from the CS Client machine. If the
  daemon runs on your laptop and the CS Client runs on the same laptop,
  `127.0.0.1:7777` works. If they're on different machines, you'll need to
  bind the daemon to `0.0.0.0` and open the port.

### Step 3.1 — drop the CS adapter YAML

```powershell
$TEST_ROOT = "$env:USERPROFILE\redrobin-smoke"
$adapterDir = "$TEST_ROOT\adapters"

@"
name: cs-main
kind: cobaltstrike
teamserver_url: https://YOUR-TS-IP:50050
operator_user: YOUR-OPERATOR-USERNAME
operator_password: YOUR-OPERATOR-PASSWORD
verify_ssl: false
"@ | Out-File -Encoding utf8 "$adapterDir\cs-main.yaml"
```

Replace the placeholders with real values. On POSIX you'd `chmod 600`; on
Windows the file inherits your user's NTFS ACLs.

### Step 3.2 — verify adapter probe

```powershell
cd $TEST_ROOT
uv run redrobin doctor
```

**Expect:** `Connectivity:` block now includes a line like:

```
  [OK ] cs-adapter(https://YOUR-TS-IP:50050)   234ms
```

If `[FAIL]`:
- `HTTP 401` → operator user/password wrong in the YAML
- `Connection refused` → wrong port or team server not running
- `SSL: CERTIFICATE_VERIFY_FAILED` → flip `verify_ssl: false`
- Connection timeout → firewall or wrong IP

Fix until you get `[OK ]`. Don't proceed otherwise.

### Step 3.3 — start daemon with CS adapter loaded

```powershell
uv run redrobin daemon start --host 0.0.0.0 --port 7777
```

(`0.0.0.0` instead of `127.0.0.1` if your CS Client is on a different machine.)

**Expect:** banner now shows `Adapters: cs-main`. Daemon log shows
`cs_adapter loaded: name=cs-main url=https://YOUR-TS-IP:50050`.

### Step 3.4 — configure `redrobin.cna` URL and token

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

### Step 3.5 — load `.cna` in Aggressor Console

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
wrong. Fix Step 3.4 and **reload the script** — the retry loop does not resume
on its own.

### Step 3.6 — confirm the script registers no commands (no beacon needed)

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
load-time lines from Step 3.5 are expected and unrelated), any gate/verdict
message, or any hint that the command was inspected before dispatch. If Red
Robin speaks here, an alias override survived the gate removal and the `.cna`
needs fixing.

Nothing is POSTed to the daemon at this point either: `on beacon_input` only
fires when a command is actually tasked to a real beacon, which is Tier 4.

### Tier 3 success criteria

| Check | Pass condition |
|-------|----------------|
| 3.2 doctor adapter probe | `[OK ]` |
| 3.3 daemon banner | `Adapters: cs-main` |
| 3.5 .cna load | No parse errors; `handshake OK` + `capture active` lines print |
| 3.6 no interception | Console behaves exactly like stock CS; no `[Red Robin]` output in response to the command |

**Common failure modes:**
- `.cna` parse error on load → fix syntax in source, commit, reload
- Handshake fails, daemon never sees `POST /v1/adapters/connect` →
  `$REDROBIN_URL` wrong, or network unreachable between CS Client machine and
  daemon
- Daemon sees the POST but returns 401 → token mismatch; the `.cna` read a
  different `transport.token` than the daemon is using (see Step 3.4)
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

There is no TUI-dispatch path. The Textual client is a read-only WebSocket
observer; the CS REST client still exposes `dispatch_console_command`, but
nothing in `redrobin/` calls it.

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
Get-Content "$env:USERPROFILE\redrobin-smoke\working_memory.json" | ConvertFrom-Json | Format-List
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
- Enricher is short-circuited (no `ANTHROPIC_API_KEY`, or the LLM provider
  failed) — the reducer no longer regex-parses command output since Plan B2.
  Check `redrobin/application/agents/enricher/enricher.py` + daemon logs for
  `enrichment failed` warnings, and confirm the engagement was started with
  the key set (not `REDROBIN_ALLOW_DEGRADED=1`)

### The assistant never says anything in the chat pane

Unprompted advisories are gated on purpose. `TriggerGate` suppresses an
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
