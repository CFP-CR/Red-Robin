# Red Robin smoke test (v0.1.x)

A 5-minute smoke test confirming the full operator journey works end-to-end.
Run after `git clone` + `uv sync` against a fresh checkout. Each step is
verifiable; if a step fails see the "Troubleshooting" section.

The longer setup walkthrough lives in [`operator-setup.md`](operator-setup.md);
this guide is the abbreviated "is everything wired up" pass.

## Prereqs

- Python 3.11+, [`uv`](https://docs.astral.sh/uv/getting-started/installation/) installed.
- An `ANTHROPIC_API_KEY` and a `VOYAGE_API_KEY` (or you'll run with `--allow-degraded`).
- Two shells. The daemon blocks on `daemon start`; everything else runs in the second shell.
- Bash, zsh, or PowerShell — both flavours are spelt out below where they diverge.

## Step 1 — Init the operator layout

Bash / zsh:

```bash
uv run redrobin init \
  --non-interactive \
  --root ./.rr \
  --anthropic-key "$ANTHROPIC_API_KEY" \
  --voyage-key "$VOYAGE_API_KEY" \
  --engagement-slug op-smoke
```

PowerShell:

```powershell
uv run redrobin init `
  --non-interactive `
  --root .\.rr `
  --anthropic-key $env:ANTHROPIC_API_KEY `
  --voyage-key $env:VOYAGE_API_KEY `
  --engagement-slug op-smoke
```

What this verifies:

- `init` creates `./.rr/{rules/shared,engagements,adapters}/` and the
  `engagements/op-smoke/engagement.yaml` placeholder.
- `.env` written with the supplied keys (and `chmod 600` on POSIX).
- Sample `rules/shared/sample-lazagne.yaml` dropped.
- Idempotent — re-running never clobbers your existing files.

Expected: `Done. Next steps: ...` and exit code 0.

## Step 2 — Verify setup

`cd` into the init root so the dotenv pin picks up `./.rr/.env`:

```bash
cd ./.rr
```

Quick check (env-var presence only, no network — useful in CI):

```bash
uv run redrobin doctor --quick
```

Full probe (Anthropic, Voyage, every configured CS adapter):

```bash
uv run redrobin doctor
```

Expected: `[+] Doctor finished: OK` and exit code 0. If any probe fails, the
output names which key/adapter — fix and retry. The daemon will start in
degraded mode if any LLM key is missing; doctor surfaces the failure
explicitly so the operator notices.

## Step 3 — Start the daemon

```bash
uv run redrobin daemon start --host 127.0.0.1 --port 7777
```

This shell blocks. The Slice 7 startup banner prints to stderr — confirm it
shows the resolved root, engagement (`op-smoke`), rules count (>= 1),
adapter list (empty unless you wired one), and key status
(`set` or `MISSING (degraded)`). Slice 7 / Slice 8 / Slice 11 features:

```
======================================================================
  Red Robin v0.1.0
======================================================================
  Listening:  http://127.0.0.1:7777
  Root:       /.../op-smoke/.rr
  Engagement: op-smoke
  Rules:      1
  Adapters:   (none)
  Anthropic:  set
  Voyage:     set
  L5 window:  25 (every 30.0s)
  Promoter:   min=3 (every 60.0s)
======================================================================
```

If a key is missing the banner will say `MISSING (degraded)`. The daemon
exits 2 in that case unless `--allow-degraded` (or `REDROBIN_ALLOW_DEGRADED=1`)
was passed.

## Step 4 — Connect TUI (second shell)

In a new shell, from the same `./.rr` directory:

```bash
uv run redrobin tui \
  --connect-ws ws://127.0.0.1:7777/v1/tui \
  --connect-http http://127.0.0.1:7777
```

(The defaults match those flags, so `uv run redrobin tui` alone also works.)
The TUI reads the bearer token from `./.rr/transport.token` automatically.

Type `lazagne` and press Enter. Expected: the verdict pane shows BLOCK with
the `rule-sample-lazagne` rule id and the reason "LaZagne is loud — prefer
in-memory creds extraction". This confirms the sample rule from `init`
loaded into Layer A and Layer A is reachable from the TUI.

## Step 5 — Drop a rule live (hot-reload)

In a third shell (or back in shell 2 after pressing `q` to exit the TUI):

Bash / zsh:

```bash
cat > ./.rr/rules/shared/block-exotic.yaml <<'YAML'
id: rule-block-exotic
description: Block exotic_tool
severity: DANGER
match:
  command: exotic_tool
reason: Sample hot-reload rule
YAML
```

PowerShell:

```powershell
@'
id: rule-block-exotic
description: Block exotic_tool
severity: DANGER
match:
  command: exotic_tool
reason: Sample hot-reload rule
'@ | Set-Content -Encoding utf8 .\.rr\rules\shared\block-exotic.yaml
```

`severity: DANGER` is the native Red Robin severity that maps to
`VerdictAction.BLOCK` — Sigma-format rules use `level: high` in a separate
`detection` block (see [`sigma-support.md`](sigma-support.md)).

Wait ~2 seconds for the watchdog observer to fire. Back in the TUI, type
`exotic_tool` and Enter. Expected: BLOCK verdict referencing
`rule-block-exotic`. This verifies the Slice 11 hot-reload watchdog
hands new rules to Layer A without a daemon restart.

## Step 6 — Stop the daemon cleanly

```bash
uv run redrobin daemon stop
```

Expected: `daemon stopped (pid=<n>)`, the PID file removed, no leftover
processes. The Slice 10 shutdown order drains background tasks before
closing the adapter REST clients, so the daemon's stdout should NOT show
"httpx client closed" tracebacks during shutdown.

## Troubleshooting

- **`doctor` fails on the Anthropic probe with `HTTP 401`.** Check
  `ANTHROPIC_API_KEY` in `./.rr/.env`. `init` is idempotent and will not
  rewrite the file — edit it manually or delete and re-run `init`.
- **`doctor` fails on the Voyage probe with `HTTP 401`.** Same fix path
  for `VOYAGE_API_KEY`.
- **`doctor` fails on the CS adapter.** Check
  `./.rr/adapters/cs-main.yaml` `teamserver_url` / `operator_user` /
  `operator_password`, and `verify_ssl: false` if the team server uses a
  self-signed cert. The CS adapter is optional; doctor only probes
  adapters you've configured.
- **Daemon won't start.** Read the banner. The two most common causes:
  - `ERROR: ANTHROPIC_API_KEY required ...` — set the key or pass
    `--allow-degraded` (Layer B disabled, Layer A still works).
  - `ERROR: REDROBIN_ROOT does not exist ...` — you `cd`'d somewhere
    without a `.env` and `REDROBIN_ROOT` resolves to a path that does
    not exist. Either `cd` back to `./.rr` or pass `--root` explicitly.
- **Daemon binds the wrong port.** Default is `127.0.0.1:7777`. Pass
  `--host` / `--port` to override.
- **TUI shows "WebSocket connection failed".** The daemon isn't running,
  or the `--connect-ws` URL doesn't match the host/port the daemon bound.
- **Hot-reload doesn't fire.** Confirm the YAML file is well-formed
  (`uv run python -c "import yaml; yaml.safe_load(open('rules/shared/block-exotic.yaml'))"`).
  The watchdog logs malformed YAML at WARN level and skips it; check the
  daemon stderr for the diagnostic.
- **Rule never matches a command.** Native rules use `INFO` / `WARN` /
  `DANGER` for `severity`; Sigma rules use `level: low|medium|high|critical`
  inside the `detection` block. Mixing the two (`severity: HIGH`)
  silently fails to load because the loader's enum has no `HIGH` entry.

## What this smoke test validates

- Slice 1 + 2 + 5: `redrobin init` produces a working layout.
- Slice 6: `doctor` connectivity probes.
- Slice 7: structured banner reflects the resolved config.
- Slice 8: daemon lifespan boots all eight `start_*` helpers in order.
- Slice 11: extended Sigma matcher loads sample rules and hot-reload
  picks up new ones without a restart.
- Slice 10: graceful shutdown drains background tasks before closing
  adapters and the bus.
- Slice 13: operator-facing CLI commands (`init`, `doctor`,
  `daemon start/stop`, `tui`) all work end-to-end.

If every step above completes with the expected output, the v0.1.x
operator journey is wired correctly.
