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

No Cobalt Strike, no beacon, and no `.cna` are needed: step 5 drives the capture
path with the Sliver-Mock replayer instead. The `.cna` itself is **not** covered
by this guide — Aggressor changes are accepted by a live-beacon operator smoke
(see the `smoke-test` skill), never by CI.

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

- `init` creates `./.rr/{engagements,adapters}/` and the
  `engagements/op-smoke/engagement.yaml` placeholder.
- `.env` written with the supplied keys (and `chmod 600` on POSIX).
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
REDROBIN_ASSISTANT_ENABLED=1 uv run redrobin daemon start --host 127.0.0.1 --port 7777
```

PowerShell:

```powershell
$env:REDROBIN_ASSISTANT_ENABLED = "1"
uv run redrobin daemon start --host 127.0.0.1 --port 7777
```

The assistant is dark-launched — **off unless you set that variable**. Capture
and working memory run either way, but the chat pane stays mute without it.

This shell blocks. The startup banner prints to stderr — confirm it shows the
resolved root, engagement (`op-smoke`), and key status (`set` or
`MISSING (degraded)`):

```
======================================================================
  Red Robin v0.1.0
======================================================================
  Listening:  http://127.0.0.1:7777
  Root:       /.../op-smoke/.rr
  Engagement: op-smoke
  Anthropic:  set
  Voyage:     set
  L5 window:  25 (every 30.0s)
======================================================================
```

If a key is missing the banner will say `MISSING (degraded)`. The daemon
exits 2 in that case unless `--allow-degraded` (or `REDROBIN_ALLOW_DEGRADED=1`)
was passed. If the engagement has an empty `in_scope` the banner adds a
`Scope: EMPTY` warning — the assistant will reason without a target boundary.

## Step 4 — Connect TUI (second shell)

In a new shell, from the same `./.rr` directory:

```bash
uv run redrobin tui \
  --connect-ws ws://127.0.0.1:7777/v1/tui \
  --connect-http http://127.0.0.1:7777
```

(The defaults match those flags, so `uv run redrobin tui` alone also works.)
The TUI reads the bearer token from `./.rr/transport.token` automatically.

Expected: the three-pane dashboard — **Sessions** (left), **Assistant Chat**
(centre), **Working Memory** (right) — and a header that says connected rather
than `disconnected`. `c` focuses the chat, `i` opens the Inspector, `?` shows
the keymap, `q` quits. Leave it running for step 5.

## Step 5 — Drive the capture path (Sliver-Mock replay)

This is the step that proves the whole live path: an adapter POSTs wire events
to `/v1/events`, the daemon persists and republishes them, the reducer folds
them into working memory, and the TUI renders the result. No beacon required.

In a third shell, from the repo root:

Bash / zsh:

```bash
REDROBIN_TOKEN="$(cat ./.rr/transport.token)" \
  uv run python adapters/sliver-mock/adapter.py \
    --scenario acme_corp \
    --daemon http://127.0.0.1:7777
```

PowerShell:

```powershell
$env:REDROBIN_TOKEN = (Get-Content .\.rr\transport.token -Raw).Trim()
uv run python adapters/sliver-mock/adapter.py `
  --scenario acme_corp `
  --daemon http://127.0.0.1:7777
```

(The mock resolves its token from `REDROBIN_TOKEN` or `~/.redrobin/transport.token`
— it does not know about `--root ./.rr`, hence the explicit env var.)

Watch the TUI while it replays. Expected:

- **Sessions** gains `WS-FINANCE-04.acme.local` / `acme\jdoe` from the
  scenario's `beacon.initial`. This leg is deterministic — no LLM involved.
- `./.rr/engagements/op-smoke/events.jsonl` grows by one line per event —
  the append-only capture record. Check it with
  `wc -l ./.rr/engagements/op-smoke/events.jsonl`.
- **Working Memory's `hosts` and `credentials` arrays stay empty** for this
  scenario, even with a working `ANTHROPIC_API_KEY` — expected, not a
  failure. The scenario's only `beacon.command_output` (a `net group "Domain
  Admins"` listing) names no host at all, and separately the enricher is
  grounded never to re-report the beacon's own host or user as
  newly-discovered (`_build_enricher_user_message`, #105). That output also
  carries no secret material for a credential to attach to — its three names
  are bare usernames, and `WorkingMemoryDeltas.from_payload` drops any
  credential with no `secret`/`ntlm_hash` (#114). (The reducer
  itself only handles `beacon.initial`, `beacon.command_input`,
  `engagement.milestone` and `working_memory.deltas`; the scenario's
  `host.observation` / `credential.observed` events are validated and
  persisted to `events.jsonl` but do not currently drive a panel.)

Then ask the assistant something in the chat pane (`c` to focus), e.g.
*"what do you know about this host?"*. Expected: an answer grounded in the
beacon session shown in Sessions (`WS-FINANCE-04.acme.local` / `acme\jdoe`) —
the assistant's prompt context carries the full working-memory snapshot,
`sessions` included, so it can ground an answer in that beacon even though
`hosts`/`credentials` stay empty for this scenario.

Note: unprompted advisories are **not** part of the pass criteria. The
assistant is deliberately selective (dedup, novelty, and a 30 s cooldown), so
silence during a short replay is correct behaviour, not a failure. The direct
question is the deterministic check.

Nothing in this flow blocks, delays, or rewrites a command — Red Robin is
advisory-only. **The adapter captures, the core advises.**

## Step 6 — Stop the daemon cleanly

```bash
uv run redrobin daemon stop
```

Expected: `daemon stopped (pid=<n>)`, the PID file removed, no leftover
processes. Shutdown drains in-flight background tasks before cancelling the
summariser and closing the bus, so the daemon's stdout should NOT show
tracebacks during shutdown.

## Troubleshooting

- **`doctor` fails on the Anthropic probe with `HTTP 401`.** Check
  `ANTHROPIC_API_KEY` in `./.rr/.env`. `init` is idempotent and will not
  rewrite the file — edit it manually or delete and re-run `init`.
- **`doctor` fails on the Voyage probe with `HTTP 401`.** Same fix path
  for `VOYAGE_API_KEY`.
- **Daemon won't start.** Read the banner. The two most common causes:
  - `ERROR: ANTHROPIC_API_KEY required ...` — set the key or pass
    `--allow-degraded` (no assistant, enrichment or summaries; capture and
    working-memory persistence still work).
  - `ERROR: REDROBIN_ROOT does not exist ...` — you `cd`'d somewhere
    without a `.env` and `REDROBIN_ROOT` resolves to a path that does
    not exist. Either `cd` back to `./.rr` or pass `--root` explicitly.
- **Daemon binds the wrong port.** Default is `127.0.0.1:7777`. Pass
  `--host` / `--port` to override.
- **TUI shows "WebSocket connection failed".** The daemon isn't running,
  or the `--connect-ws` URL doesn't match the host/port the daemon bound.
- **Replay exits with 401.** The mock's token didn't match. It reads
  `REDROBIN_TOKEN`, falling back to `~/.redrobin/transport.token` — which is
  the wrong file when you ran `init --root ./.rr`. Set `REDROBIN_TOKEN`
  explicitly as shown in step 5.
- **Replay exits with 400 `envelope_errors` / `event_errors`.** `/v1/events`
  validates every envelope against `protocol/schemas/v0.1/` and the schemas
  are `additionalProperties: false`, so an unknown key is a hard reject. If
  you hand-edited a scenario, diff it against
  `protocol/fixtures/v0.1/valid/`.
- **Nothing lands in the TUI but the replay reported success.** Check the
  daemon stderr for `Engagement: (none)` in the banner — with no active
  engagement, `/v1/events` returns `stored: false` and writes nothing.
  `redrobin engagement use op-smoke`, then retry.
- **Chat pane never answers.** The assistant is off unless the daemon was
  started with `REDROBIN_ASSISTANT_ENABLED=1`, and it needs
  `ANTHROPIC_API_KEY`. Unprompted silence is normal; silence in response to a
  direct question is not.

## What this smoke test validates

- `redrobin init` produces a working layout.
- `doctor` connectivity probes reach Anthropic, Voyage, and any adapter.
- The structured banner reflects the resolved config.
- The daemon lifespan boots its `start_*` helpers in order.
- The capture path end-to-end: adapter → `POST /v1/events` → schema
  validation → `events.jsonl` → event bus → reducer → TUI.
- The assistant is reachable and answers grounded in working memory.
- Graceful shutdown drains background tasks before closing adapters and the
  bus.
- Operator-facing CLI commands (`init`, `doctor`, `daemon start/stop`, `tui`)
  all work end-to-end.

It does **not** validate the Cobalt Strike `.cna` — only a live beacon can.

If every step above completes with the expected output, the v0.1.x
operator journey is wired correctly.
