# Red Robin v0.1.x — Testing Guide

Four-tier testing playbook. Run them in order. Each tier isolates a layer of the
system so failures point at one cause, not three.

| Tier | What it validates | Time | External deps |
|------|-------------------|------|---------------|
| 1. Local smoke | Daemon boot, verdict pipeline, Sigma matcher, TUI, hot-reload | ~10 min | Anthropic + Voyage keys |
| 2. Sliver-Mock | Wire protocol end-to-end, bus → reducer → working memory, adapter lifecycle | ~15 min | none (no CS license needed) |
| 3. CS local (no beacon) | `redrobin.cna` parses in Aggressor, adapter login works, alias gates wire up | ~30 min | CS Team Server + CS Client |
| 4. CS full E2E | All 3 paths: console gate, TUI dispatch, output observation | ~1-2 h | CS + lab with a live beacon |

**Stop after Tier 1 if anything's red.** Don't escalate to a real CS test with
a broken local daemon.

All commands assume **Windows + PowerShell** (per the operator's environment).
The repo at `C:\Users\jcspi\Documents\dev-projects\Red-Robin` and the test root
at `$env:USERPROFILE\redrobin-smoke` keep your real `~/.redrobin` (if any)
untouched.

---

## Tier 0 — Pre-flight

Run this once before any tier:

```powershell
cd C:\Users\jcspi\Documents\dev-projects\Red-Robin
git checkout dev-juan
git log --oneline -1
# Expect: e7bf956 docs: fix stale references caught by Slice 15 final review

uv sync --extra dev
uv run pytest tests/ adapters/cobaltstrike/tests/ -q
```

Expected: `442 passed, 6 skipped`. If anything fails, stop and investigate
before running any tier.

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

Must include: `.env`, `rules\shared\sample-lazagne.yaml`,
`engagements\op-smoke\engagement.yaml`, `adapters\`, `transport.token`.

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
  RULES:              1 loaded
[+] Doctor finished: OK (quick mode - skipped network probes)
```

**Expect (full mode):** above plus a `Connectivity:` block with
`[OK ] anthropic   <ms>` and `[OK ] voyage   <ms>`, then
`[+] Doctor finished: OK`.

**Transient failures:**
- `[FAIL] anthropic   HTTP 401`: your key is wrong, edit `.env`.
- `[FAIL] anthropic   HTTP 503` or `HTTP 429`: Anthropic's API is overloaded
  or rate-limiting you. Retry once or twice (`redrobin doctor`). If it
  persists >2 min, check https://status.anthropic.com. Does NOT block Step
  1.2 — `daemon start` validates key PRESENCE not connectivity; Layer B
  works as soon as Anthropic recovers.
- Same applies to Voyage at https://status.voyageai.com.

The `DEGRADED (Layer A still works)` tag in doctor's footer means the
NETWORK probe failed; it does NOT mean `daemon start` will run in
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
  Rules:      1
  Adapters:   (none)
  Anthropic:  set
  Voyage:     set
  L5 window:  25 (every 30.0s)
  Promoter:   min=3 (every 60.0s)
======================================================================
```

If `Anthropic: MISSING (degraded)`, the `.env` wasn't loaded — confirm
`cd $TEST_ROOT` happened in this shell.

### Step 1.3 — verdict path (terminal B)

```powershell
$TEST_ROOT = "$env:USERPROFILE\redrobin-smoke"
$token = Get-Content "$TEST_ROOT\transport.token"
$H = @{ Authorization = "Bearer $token"; "Content-Type" = "application/json" }

# Health
Invoke-RestMethod http://127.0.0.1:7777/v1/healthz
# Expect: status=ok

# Sample rule blocks lazagne (Slice 14 caught a bug where this silently passed)
$body = @{ source_event_id="c-1"; bid="B-1"; command="lazagne"; raw="lazagne all" } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:7777/v1/verdict/quick -Method Post -Headers $H -Body $body
```

**Expect:** `action: BLOCK`, `rule_id: rule-sample-lazagne`.

### Step 1.4 — hot-reload

**WARNING — Windows PowerShell 5 BOM footgun:** PS5's `Out-File -Encoding utf8`
adds a Byte Order Mark (0xEF 0xBB 0xBF) to the file. PyYAML attaches that BOM
to the first key, so `data["id"]` raises `KeyError: 'id'` even though the
file looks fine in an editor. The rules loader fail-loud emits
`RulesLoadError: missing required field 'id'` and keeps the previous rules.

Use `.NET WriteAllText` instead — its default is UTF-8 without BOM:

```powershell
$ruleContent = @"
id: rule-block-sharphound
description: Block SharpHound
severity: DANGER
match:
  command: sharphound
reason: SharpHound has high EDR detection
"@
[System.IO.File]::WriteAllText("$TEST_ROOT\rules\shared\block-sharphound.yaml", $ruleContent)

Start-Sleep -Seconds 2

$body = @{ source_event_id="c-2"; bid="B-1"; command="sharphound"; raw="sharphound -c all" } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:7777/v1/verdict/quick -Method Post -Headers $H -Body $body
```

**Expect:** `action: BLOCK`, `rule_id: rule-block-sharphound`. Daemon picked up
the new rule without restart.

**If you get `action: ESCALATE` with reason `novel pattern` and the daemon
log shows `RulesLoadError: missing required field 'id'`** — the YAML has a
BOM. Verify with:

```powershell
[System.IO.File]::ReadAllBytes("$TEST_ROOT\rules\shared\block-sharphound.yaml")[0..2]
# Expect: 239 187 191 means BOM is present (bad). 105 100 58 means no BOM (good — "id:" in ASCII).
```

Delete the file and re-write with `[System.IO.File]::WriteAllText`.

This footgun ONLY affects operators dropping rules from PS5. PowerShell 7
has `Out-File -Encoding utf8NoBOM`; editors like VS Code / Notepad++ save
without BOM by default. Filed as a v0.1.x follow-up to make the rules
loader BOM-tolerant.

### Step 1.5 — Sigma extended matcher (Slice 11's highest-risk slice)

```powershell
$sigmaDir = "$TEST_ROOT\rules\shared\sigma"
New-Item -ItemType Directory -Force $sigmaDir | Out-Null

$sigmaRule = @"
title: Mimikatz invoked at High integrity
id: aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee
logsource:
  product: windows
detection:
  selection:
    IntegrityLevel: High
    CommandLine|contains: 'sekurlsa::logonpasswords'
  condition: selection
level: high
"@
# Use WriteAllText (no BOM) — see Step 1.4 footgun note
[System.IO.File]::WriteAllText("$sigmaDir\mimikatz-high-integrity.yaml", $sigmaRule)

Start-Sleep -Seconds 2

# With High integrity: should BLOCK
$body = @{
  source_event_id = "c-sigma-high"
  bid = "B-1"
  command = "mimikatz"
  raw = 'mimikatz "sekurlsa::logonpasswords"'
  integrity_level = "High"
} | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:7777/v1/verdict/quick -Method Post -Headers $H -Body $body

# Without integrity_level: should NOT match this rule
$body = @{
  source_event_id = "c-sigma-noint"
  bid = "B-1"
  command = "mimikatz"
  raw = 'mimikatz "sekurlsa::logonpasswords"'
} | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:7777/v1/verdict/quick -Method Post -Headers $H -Body $body
```

**Expect:** first call returns `BLOCK`; second call returns `PASS` or
`ESCALATE` (the IntegrityLevel predicate fails because the field is null).

### Step 1.6 — ParentImage unsupported-field WARN

```powershell
$parentRule = @"
title: ParentImage test (unsupported in v0.1.x)
id: bbbbbbbb-cccc-dddd-eeee-ffffffffffff
logsource:
  product: windows
detection:
  selection:
    ParentImage|endswith: '\powershell.exe'
  condition: selection
level: high
"@
[System.IO.File]::WriteAllText("$sigmaDir\parent-test.yaml", $parentRule)

Start-Sleep -Seconds 2
```

In **terminal A** (daemon), watch for a `WARN` log line mentioning
`ParentImage` and "unsupported". The rule loads but produces a
`_NeverMatchPredicate` so it can never match.

### Step 1.7 — TUI (terminal C)

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

**Expect:**
- 3 panels: Sessions (left), Events (center), Memory (right)
- Type `whoami` + Enter → verdict in Events panel
- Type `lazagne` + Enter → BLOCK with `rule-sample-lazagne`
- Press **Ctrl+O** → inspector overlay
- Press **Esc** → overlay closes
- Press **Ctrl+C** → TUI exits

### Step 1.8 — clean shutdown

In terminal B (venv already activated from Setup):

```powershell
redrobin daemon stop
```

**Expect:** `daemon stopped (pid=<n>)`, no orphan task tracebacks in terminal A.

### Tier 1 success criteria

| Check | Pass condition |
|-------|----------------|
| Step 1.3 lazagne | `action: BLOCK`, `rule_id: rule-sample-lazagne` |
| Step 1.4 hot-reload | `action: BLOCK`, `rule_id: rule-block-sharphound` |
| Step 1.5 Sigma + integrity | BLOCK with `High`, PASS/ESCALATE without |
| Step 1.6 ParentImage | WARN line in daemon log |
| Step 1.7 TUI | 3 panels render, Enter dispatches, Ctrl+O works |
| Step 1.8 stop | Clean exit, no errors |

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
[sliver-mock] sent beacon.command_output
[sliver-mock] sent credential.observed
... (more events as the scenario progresses)
[sliver-mock] disconnected
```

In terminal A (daemon log), you should see corresponding `INFO` log lines as
events flow through the bus and the reducer parses beacon outputs.

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

**Expect:** Memory panel (right side) shows the host(s), credentials, and any
findings from the scenario. Events panel may show recent verdict events if the
scenario triggered any. Exit with Ctrl+C.

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
| Step 2.4 TUI memory panel | Memory panel reflects the scenario state |
| No daemon crash | Terminal A shows no tracebacks |

**If hosts/credentials don't show up in working memory** despite events being
sent, that's a wire-protocol bug — the same class of bug Phase 2 had with
`/v1/events` not bus-publishing. Stop and report.

---

## Tier 3 — CS Local (Load `.cna`, No Live Beacon)

**Goal:** validate that `adapters/cobaltstrike/redrobin.cna` parses cleanly in
a real Aggressor Console, that the daemon's `cs_adapter_loader` can log in to
your team server, and that alias gates wire up. **No beacon required.**

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

The `.cna` script needs to know where the daemon lives. Open
`adapters/cobaltstrike/redrobin.cna` and find the Sleep globals near the top
(`$REDROBIN_URL`, `$REDROBIN_TOKEN`). Set:

```sleep
$REDROBIN_URL = "http://127.0.0.1:7777";  # or http://YOUR-LAPTOP-IP:7777 if daemon is remote
$REDROBIN_TOKEN = "<paste contents of $TEST_ROOT\transport.token>";
```

(If the `.cna` reads these from an env file or a sister config file instead,
follow that pattern — the canonical mechanism is documented in the .cna's
header comments.)

### Step 3.5 — load `.cna` in Aggressor Console

In CS Client, with the team server connected:

1. **Cobalt Strike** menu → **Aggressor Scripts**
2. **Load** button → navigate to
   `C:\Users\jcspi\Documents\dev-projects\Red-Robin\adapters\cobaltstrike\redrobin.cna`
3. Watch the **Script Console** (bottom of the Aggressor Scripts dialog) for:
   - Parse errors (syntax issues — STOP, fix in `.cna`, reload)
   - Warnings about undefined functions or scope issues
   - Success message (something like `[Red Robin] loaded` or no output at all
     if the .cna is silent on load)

**If parse errors appear**, copy the exact error message and the line number.
That's a `.cna` syntax bug we need to fix in source.

### Step 3.6 — exercise an alias gate (no beacon needed)

In the CS Client's main console (the operator command line), try a registered
alias from the `.cna`. The .cna defines aliases like `mimikatz`, `dcsync`,
`safetykatz`, etc. (~22 Tier 1 aliases per the architecture doc). Try one:

```
mimikatz "sekurlsa::logonpasswords"
```

**Without a beacon selected**, CS will likely complain "no beacon selected" —
that's fine. What you're looking for is whether the alias was **recognized**
(not "no such command"). If recognized, the .cna loaded correctly.

To exercise the gate without a beacon, you can register a fake/dummy beacon
in Aggressor via the Script Console:

```sleep
&binit(rand(100000));  # registers a fake bid for testing
```

(Or look at the .cna for whatever fake-beacon helper it defines.)

Then run an alias against that dummy bid and watch:
- **Terminal A (daemon)**: should see `POST /v1/verdict/quick` log line.
- **CS Script Console**: should see the .cna's response handling (gate decision
  printed, or the command being passed to `bmimikatz!` for PASS, or blocked
  for BLOCK).

### Tier 3 success criteria

| Check | Pass condition |
|-------|----------------|
| 3.2 doctor adapter probe | `[OK ]` |
| 3.3 daemon banner | `Adapters: cs-main` |
| 3.5 .cna load | No parse errors in Aggressor Script Console |
| 3.6 alias gate fires | Daemon log shows `POST /v1/verdict/quick`; .cna responds to verdict |

**Common failure modes:**
- `.cna` parse error on load → fix syntax in source, commit, reload
- Alias registered but daemon never sees the POST → `$REDROBIN_URL` wrong, or
  network unreachable between CS Client machine and daemon
- Daemon sees the POST but returns 401 → token mismatch
- Alias body errors with "no such function `&bmimikatz`" → beacon not
  selected (CS context limitation — needs a real beacon)

---

## Tier 4 — CS Full E2E with Beacon

**Goal:** end-to-end validation across all three architectural paths:

1. **Console gate** — operator types `mimikatz` in CS console → alias
   intercepts → daemon verdict → response back to .cna → BLOCK refuses,
   PASS lets the command through to beacon
2. **TUI dispatch** — from Red Robin TUI, operator types a command → POST
   to daemon → daemon routes to CS REST adapter → command appears on the
   beacon in CS console
3. **Output observation** — beacon executes a recon command → output flows
   through `set BEACON_OUTPUT` hook in .cna → POST `/v1/events` → reducer
   ingests → working memory updates → TUI Memory panel reflects new
   credentials/hosts

### Pre-reqs

- Tier 3 passing (CS adapter green, .cna loads, alias gates fire on dummy bid)
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

If your operator user shows up as `corp\admin *` (trailing space-asterisk),
that's the `binfo $bid "user"` value Slice 11's `redrobin_integrity_level`
sub keys off for `integrity_level: "High"`. If it shows `corp\admin` (no
asterisk), the beacon is in Medium integrity and the integrity_level field
will be null per the binary-signal design.

### Step 4.2 — Path 1: Console gate

In the CS console, with the lab beacon selected, type:

```
mimikatz "sekurlsa::logonpasswords"
```

**Expect:**

- The .cna alias intercepts. Output should show something like
  `[Red Robin] Checking mimikatz...` or whatever your .cna prints.
- **Terminal A (daemon)** logs `POST /v1/verdict/quick` with payload including
  `command: mimikatz`, `raw: 'mimikatz "sekurlsa::logonpasswords"'`,
  `integrity_level: "High"` (if beacon was elevated, per the user `*` suffix).
- If you dropped a Sigma rule like `Mimikatz invoked at High integrity` from
  Step 1.5 earlier, daemon returns `action: BLOCK`. The .cna should refuse
  to dispatch the command and print the block reason.
- If no rule matches, daemon returns `PASS` or `ESCALATE`. The .cna dispatches
  via `bmimikatz!` to the actual beacon. Mimikatz output flows back.

**Critical check:** if BLOCK fires, the actual beacon **must not** receive
the command. Verify by looking at the beacon's task list (`Sessions →
right-click beacon → Tasks`). No mimikatz task should appear.

### Step 4.3 — Path 2: TUI dispatch

Open Red Robin TUI in a separate terminal:

```powershell
cd $env:USERPROFILE\redrobin-smoke
uv run redrobin tui `
  --connect-ws ws://127.0.0.1:7777/v1/tui `
  --connect-http http://127.0.0.1:7777
```

Find the beacon ID in the TUI's Sessions panel (left side). It should appear
because the .cna's `set BEACON_OUTPUT` hook reports beacon presence via
events to the daemon.

In the TUI input, type a safe recon command:

```
whoami
```

Hit Enter. The TUI's `OperatorDispatcher` POSTs to `/v1/verdict/quick`. If
PASS, the daemon's verdict route dispatches to the CS REST adapter via
`POST /api/v1/beacons/{bid}/consoleCommand`.

**Expect:**

- TUI Events panel shows the verdict (`action: PASS`, source `layer_a`).
- **CS console** shows the command appearing on the lab beacon, as if you'd
  typed it directly. The beacon executes it and the output streams back.

**Critical check:** the command must reach the actual beacon (not just the
daemon's mock). Verify by watching CS console for the `whoami` task on the
beacon's task line.

### Step 4.4 — Path 3: Output observation

With a beacon active and the .cna's `set BEACON_OUTPUT` hook installed, run
a recon command that produces parsable output. Mimikatz dumping NTLM hashes
is ideal:

In CS console (or via TUI dispatch):

```
mimikatz "sekurlsa::logonpasswords"
```

Wait for the beacon to execute and return output. The .cna's BEACON_OUTPUT
hook should fire, POST to `/v1/events` with `type: beacon.command_output`
and the output text.

**Expect:**

- **Daemon log (terminal A)**: `POST /v1/events`, then reducer parsing log
  lines mentioning credential extraction.
- **Working memory file**: new entries.

```powershell
Get-Content "$env:USERPROFILE\redrobin-smoke\working_memory.json" | ConvertFrom-Json | Format-List
```

Look for `credentials` containing the dumped users + NTLM hashes, `hosts`
containing the beacon's hostname, etc.

- **TUI Memory panel**: refresh by exiting and reopening the TUI (or wait
  for the snapshot to push via WebSocket). Should reflect the new state.

### Step 4.5 — Layer B retroactive (optional, requires real LLM)

If your Anthropic key is configured (not `--allow-degraded`), the daemon's
Layer B retroactive judgment fires asynchronously after a PASS or ESCALATE
verdict. Watch the TUI Events panel: a verdict that initially showed
`source: layer_a` may get an updated entry with `source: layer_b` and a
different `action` if the LLM disagrees with Layer A.

For demo: get a borderline command (something not blocked by Sigma but
plausibly risky) through Layer A as PASS, then watch Layer B post-hoc judge
it. The TUI's retroactive update logic re-keys by `correlation_id` and
updates the existing verdict line in place.

### Tier 4 success criteria

| Path | Pass condition |
|------|----------------|
| 4.2 Console gate | BLOCK actually prevents the command from reaching the beacon; PASS lets it through |
| 4.3 TUI dispatch | Command typed in TUI executes on the beacon |
| 4.4 Output observation | Mimikatz output extracted into working memory, visible in TUI Memory panel |
| 4.5 Layer B (optional) | Retroactive verdict updates the original verdict line in TUI |

If Paths 1-3 all work, **v0.1.x is genuinely production-ready** for the
DEF CON RTV demo path. Path 4 (Layer B retroactive) is the "wow moment" of
the demo — the LLM disagreeing with the Sigma matcher in real time.

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

### Tier 3 alias fires but daemon never sees the POST

Network. Test reachability from CS Client machine:

```
# On the CS Client machine:
curl http://YOUR-DAEMON-IP:7777/v1/healthz
```

If that hangs or refuses, fix firewall / bind address before continuing.

### Tier 4 BLOCK verdict but beacon STILL receives the command

The .cna's gate logic is broken — it's not respecting the verdict response.
This is a high-priority bug. The .cna must NOT call `bmimikatz!` (or whatever
the actual command dispatch is) when the verdict is BLOCK. Inspect the
.cna's verdict-handling block.

### Tier 4 PASS verdict but command never reaches beacon

Either the .cna isn't dispatching after PASS (bug in .cna), or for the TUI
dispatch path the CS REST adapter isn't reaching the team server. Check
daemon logs for `cs_adapter dispatch failed` warnings; check CS team server
side for the operator's session being active.

### Working memory has zero credentials despite mimikatz running

Either:
- BEACON_OUTPUT hook isn't installed (check .cna source)
- POST `/v1/events` is failing auth (check daemon log)
- Reducer regex doesn't match the output format (CS mimikatz BOF format may
  differ; check `redrobin/application/working_memory_reducer.py::_NTLM_LINE`
  regex against the actual output text)

---

## After Tier 4: what's next

If all four tiers pass:

1. **Merge `dev-juan` → `main`** and tag `v0.1.1` (or `v0.2` if you treat
   the hardening as a milestone bump).
2. **DEF CON RTV CFP** is due 2026-05-31. The paper draft at
   `_Claude/redrobin_paper.txt` needs the alias-gate honesty pass + AI-tone
   scrub + ES version per the memory.
3. **Sliver-Real adapter** is the v0.2 commitment — 2026-06-22 deadline.
   Use `SliverPy` is OFF (abandoned 2023); use protoc-generated client from
   upstream `.proto` files instead.
4. **v0.2 roadmap items** (referenced in `docs/sigma-support.md`):
   - `ParentImage` Sigma field (needs async `ps` parsing + cache)
   - `IntegrityLevel: Medium/Low/System` (needs beacon-side BOF)
   - `|re|i` case-insensitive regex sub-modifier
   - `|cidr`, `|wide`, `|fieldref` modifiers
   - `|cased` case-sensitive opt-in
   - Mythic adapter (Mythic has native `opsec_pre()` hook + peer-review built in)
