# Operator setup

First-time setup walkthrough. Clone, init, daemon up, TUI connected, smoke
test — roughly 10 minutes if you have your API keys handy.

## Prereqs

- **Python 3.11+** (3.12 recommended; the test matrix runs against 3.11 + 3.12).
- **`uv`** — the project uses `uv sync` for dependency install.
  Install via [astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/).
- **Anthropic API key** (`ANTHROPIC_API_KEY`) — Layer B verdicts degrade to no-op
  without it. You can still start the daemon with `--allow-degraded` for a
  Layer-A-only setup.
- **Voyage AI API key** (`VOYAGE_API_KEY`) — KB retrieval returns empty without
  it.
- **Cobalt Strike license** — optional. Only required if you intend to load
  the CS adapter; the daemon runs fine without any adapter for protocol
  testing.

## 1. Clone and install

```bash
git clone https://github.com/CFP-CR/Red-Robin.git
cd Red-Robin
uv sync --all-extras
```

`uv sync` materializes `.venv/` and installs the project plus dev extras
(pytest, ruff, mypy, lint-imports). The `redrobin` CLI becomes available
under `uv run redrobin ...`.

## 2. `redrobin init`

Interactive (recommended on Windows Terminal or PowerShell, any POSIX shell):

```bash
uv run redrobin init
```

You will be prompted for:

- `ANTHROPIC_API_KEY` (hidden input via `getpass`).
- `VOYAGE_API_KEY` (hidden input).
- Engagement slug (defaults to `op-default`).

Non-interactive (CI, Git Bash, `< file` redirect):

```bash
uv run redrobin init \
  --non-interactive \
  --anthropic-key "sk-ant-..." \
  --voyage-key "pa-..." \
  --engagement-slug op-smoke
```

`init` is idempotent — re-running never clobbers your existing `.env`,
engagement YAML, or sample rule. What it creates:

- `~/.redrobin/.env` — pinned env config (chmod 600 on POSIX).
- `~/.redrobin/engagements/<slug>/engagement.yaml` — placeholder scope.
- `~/.redrobin/rules/shared/sample-lazagne.yaml` — example BLOCK rule.
- `~/.redrobin/transport.token` — bearer token for daemon auth.

See `docs/secrets-handling.md` for caveats on Git Bash, Windows ACLs, and
the dotenv CWD-pin behavior. See `docs/operator-cli-conventions.md` for the
interactive vs non-interactive pattern.

## 3. `redrobin doctor`

Full preflight — env vars, root layout, rule count, real network probes
against Anthropic, Voyage, and any configured CS adapter:

```bash
uv run redrobin doctor
```

Quick preflight — env presence + rule count only, no network calls (useful
in CI):

```bash
uv run redrobin doctor --quick
```

Exit code 0 means OK. Exit code 1 means DEGRADED — Layer A still works,
Layer B may be disabled or KB retrieval may be empty. The output names each
missing piece so you know what to fix.

## 4. Engagement YAML

`init` drops a placeholder. Edit it for your engagement:

```bash
$EDITOR ~/.redrobin/engagements/op-smoke/engagement.yaml
```

Minimal shape:

```yaml
slug: op-smoke
name: op-smoke
in_scope:
  - corp.local
  - 10.10.0.0/16
out_of_scope:
  - bigcorp.com
  - 10.99.0.0/16
summary: |
  Internal red team against ACME Corp. EDR is SentinelOne; AV is Defender.
  Working hours only; no destructive actions. Prior tradecraft constraints:
  no Mimikatz binary on disk, no LSASS dumping by name.
```

Scope rules: `in_scope` and `out_of_scope` accept hostnames, suffix
patterns, and CIDRs. `out_of_scope` wins on conflict. `summary` is
injected as a cached system block into Layer B's LLM prompt, so anything
you put there shapes the verdict reasoning.

To create additional engagements:

```bash
uv run redrobin engagement new acme-q2-2026 --client "Acme Corp"
uv run redrobin engagement use acme-q2-2026
```

### Engagement isolation and persistence layout

After `redrobin engagement use <slug>`, the daemon stores per-engagement
state under `~/.redrobin/engagements/<slug>/`:

- `engagement.yaml` — scope, objectives, kill-chain status (operator-edited)
- `working_memory.json` — live host/credential/finding/session facts
- `episodic.db` — every verdict and observation, one row per event

Each engagement is isolated: switching engagements never mixes hosts,
credentials, or verdict history between them. The daemon resolves the
active engagement at startup with this precedence:

1. `REDROBIN_ENGAGEMENT_SLUG` env var (explicit override, primarily for
   CI/tests)
2. `~/.redrobin/.active_engagement` (written by `redrobin engagement use`)
3. None — degraded mode

**Degraded mode** (no engagement active): the daemon writes to root paths
`~/.redrobin/{working_memory.json, episodic.db}` and tags all episodes
with `engagement_id="eng-default"`. The daemon banner emits a clear
warning when this happens — verdicts won't be persisted per-engagement.
This mode primarily exists for tests and dev scripts; production operators
should always activate an engagement first.

**Cross-engagement state** (shared, NOT per-engagement):

- `~/.redrobin/longterm.db` — long-term lessons promoted from episodic
- `~/.redrobin/context_summaries.db` — L5 sliding-window summaries
- `~/.redrobin/shared/` — anything explicitly meant to span engagements

The corresponding env vars (`REDROBIN_LONGTERM_PATH`,
`REDROBIN_SUMMARY_PATH`) are still honored — those repos are
cross-engagement by design (long-term tradecraft + context spans
engagements).

**Env-var caveat:** when an engagement is active, the daemon ignores
`REDROBIN_WORKING_MEMORY_PATH` and `REDROBIN_EPISODIC_PATH`. Paths are
derived from the engagement directory unconditionally. Setting those
env vars only takes effect in degraded mode.

**Historical data (pre-2026-05-24):** episodic rows accumulated before
the per-engagement isolation change live at `~/.redrobin/episodic.db`
tagged `engagement_id="eng-default"`. They are not auto-migrated to
per-engagement DBs. To query historical data:

```bash
sqlite3 ~/.redrobin/episodic.db "SELECT * FROM episodes WHERE engagement_id='eng-default' ORDER BY id DESC LIMIT 20"
```

New engagements created after this change get their own clean DB at
`~/.redrobin/engagements/<slug>/episodic.db`.

## 5. Cobalt Strike adapter (optional)

If you want the daemon to dispatch ALLOW verdicts to a real teamserver,
drop an adapter YAML:

```bash
cp adapters/cobaltstrike/cs-main.example.yaml ~/.redrobin/adapters/cs-main.yaml
$EDITOR ~/.redrobin/adapters/cs-main.yaml
chmod 600 ~/.redrobin/adapters/cs-main.yaml   # POSIX
```

Fields:

- `name` — adapter instance label (any string).
- `kind: cobaltstrike` — required; other `kind` values are silently skipped.
- `teamserver_url` — e.g. `https://ts.example.com:50050`.
- `operator_user` / `operator_password` — your CS team server credentials.
- `verify_ssl: false` — typical for CS team servers using self-signed certs.

The loader warns at boot if the file is group/world readable on POSIX. On
Windows the loose-perms check is skipped; rely on NTFS ACLs. See
`docs/secrets-handling.md`.

### Cobalt Strike Client Java requirement

Red Robin's Aggressor script (`redrobin.cna`) requires **Java 11+** on the CS
Client side. CS 4.12 bundles OpenJDK 17 in its standard launcher per
[Fortra Java dependency docs](https://www.cobaltstrike.com/help-java-dependency),
so operators on stock CS launches are automatically on a supported JVM.

**No JVM flags required.** All HTTP work happens inside a Java helper JAR
(`adapters/cobaltstrike/jars/redrobin-aggressor-http-1.0.2.jar`) loaded by
the .cna via `URLClassLoader`. Sleep reflection targets only our public
`com.redrobin.aggressor.RedRobinHttp` class — never `jdk.internal.*` or
`sun.*` — so JVM access checks pass on stock launches.

**Launch CS Client via the official launcher** (no manual `java -jar`):

```
cobaltstrike.exe              # Windows
./cobaltstrike-client         # Linux/Mac
```

**JAR co-location:** the .cna expects `redrobin-aggressor-http-1.0.2.jar`
in a sibling `jars/` directory. If you cloned the Red-Robin repo, the
layout is already correct:

```
adapters/cobaltstrike/
├── redrobin.cna
└── jars/
    ├── redrobin-aggressor-http-1.0.2.jar
    └── json-20240303.jar
```

If you copy the .cna elsewhere, also copy the `jars/` directory next to
it (relative path `./jars/redrobin-aggressor-http-1.0.2.jar`).

**Troubleshooting:**

- If the .cna fails to load with `Java ... detected. Red Robin requires
  Java 11+` in the Script Console, your CS Client JVM is older than 11.
  Upgrade to a modern JDK (Temurin 17 or 21).
- If the .cna fails with `ClassNotFoundException: com.redrobin.aggressor.RedRobinHttp`,
  the JAR is missing or misplaced. Verify the sibling-jars layout above.

**Historical note:** Earlier Red Robin versions documented an
`--add-opens` JVM flag workaround. That is no longer needed as of the
2026-05-17 JAR helper refactor — see
`docs/superpowers/specs/2026-05-17-cna-jar-helper-design.md` for the
architecture rationale.

### Layer B (async) verdict visibility

Red Robin's pre-flight gate has two layers:

- **Layer A** — synchronous Sigma/YAML rule eval (~150 ms). Verdicts surface
  in the beacon console immediately as `[Red Robin] DANGER: ...`,
  `[Red Robin] WARN: ...`, or `[Red Robin] novel pattern — Layer B reviewing async`.

- **Layer B** — async Claude LLM judge (~10–30 s). When Layer B finishes
  evaluating a command that Layer A marked as ESCALATE, its verdict surfaces
  back to the **same beacon console** as a second line:

  ```
  [Red Robin] Layer B BLOCK: <Claude's rationale>
  [Red Robin] Layer B WARN:  <Claude's rationale>
  ```

  Only `WARN` and `BLOCK` retroactive verdicts are surfaced. A clean
  Layer B result (`PASS`) is silent — no news is good news. The full
  enum is `PASS / WARN / BLOCK / ESCALATE`; `ESCALATE` is what Layer A
  emits when it defers to Layer B, and Layer B's job is to upgrade that
  to one of `PASS / WARN / BLOCK`.

Layer B verdicts are delivered over a WebSocket subscription to the daemon
(`/v1/adapters/events`), maintained by the
`com.redrobin.aggressor.RedRobinEventStream` class inside the helper JAR.
No operator configuration required — the connection establishes
automatically when `redrobin.cna` loads.

If the daemon goes down, the WS connection drops; the JAR reconnects with
exponential backoff (up to 60 s). Verdicts published during the gap are
NOT replayed — check `episodic.db` for the historical record:

```bash
uv run python tools/inspect_episodic.py
```

(Helper lives at `tools/inspect_episodic.py` in the repo. It defaults to
`~/.redrobin/episodic.db`; pass an explicit path to inspect a per-engagement DB,
e.g. `uv run python tools/inspect_episodic.py ~/.redrobin/engagements/<slug>/episodic.db`.)

## 6. Start the daemon

```bash
uv run redrobin daemon start --host 127.0.0.1 --port 7777
```

The startup banner prints to stderr — look for `ANTHROPIC_API_KEY: set`
and `VOYAGE_API_KEY: set` lines. If either says `MISSING (degraded)`, the
banner also tells you what's disabled.

To run without LLM keys at all (Layer A only):

```bash
uv run redrobin daemon start --allow-degraded
```

To stop:

```bash
uv run redrobin daemon stop
```

## 7. TUI

In a second shell:

```bash
uv run redrobin tui \
  --connect-ws ws://127.0.0.1:7777/v1/tui \
  --connect-http http://127.0.0.1:7777
```

Defaults match those flag values, so `uv run redrobin tui` alone works for
the default daemon address. The TUI reads `~/.redrobin/transport.token`
automatically.

## 8. Smoke test

In the TUI, type `whoami` and press Enter. A verdict should land in the
verdict pane (PASS for `whoami`, since it has no matching BLOCK rule).

Hot-reload check: in another shell, drop a rule live:

```bash
cat > ~/.redrobin/rules/shared/test-block.yaml <<'EOF'
id: rule-test-whoami
description: Test block for smoke
severity: DANGER
match:
  command: whoami
reason: Smoke test rule
EOF
```

The daemon should pick up the new rule within seconds (watchdog observer).
Type `whoami` in the TUI again — verdict should now be BLOCK. Delete the
test rule when done.

## 9. Stop

```bash
uv run redrobin daemon stop
```

Daemon shutdown drains background tasks, closes adapter sessions, and
flushes the persistence queue before exit.

## Troubleshooting

- **`redrobin doctor` reports DEGRADED.** Re-run `redrobin init` — it
  rewrites `.env` only when the file does not exist, so if `.env` already
  has a missing or stale key, edit `.env` directly and re-run doctor.
- **Daemon startup banner missing.** The banner prints to stderr. If you
  redirected stderr, you won't see it. Re-run without redirection.
- **`REDROBIN_ROOT not set` or paths under the wrong directory.** Check
  that you are running `redrobin` from a CWD that contains a `.env` (the
  `_load_env_for_cli` helper pins to `Path.cwd() / ".env"` and does NOT
  walk parent directories — see `docs/secrets-handling.md`).
- **Daemon won't bind to the port.** Default `127.0.0.1:7777`. Pass
  `--host` / `--port` if you need a different bind address.
- **TUI cannot authenticate.** Confirm `~/.redrobin/transport.token`
  exists and is readable by the TUI user. `redrobin init` creates it
  idempotently; deleting it forces a fresh token on next daemon start.
- **Adapter handshake fails.** Run `redrobin doctor` — it probes every
  configured adapter and prints the failure reason. Common causes:
  teamserver unreachable, wrong password, SSL verification mismatch.
