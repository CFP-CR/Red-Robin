# Operator setup

First-time setup walkthrough. Clone, init, daemon up, TUI connected, smoke
test — roughly 10 minutes if you have your API keys handy.

## Prereqs

- **Python 3.11+** (3.12 recommended; the test matrix runs against 3.11 + 3.12).
- **`uv`** — the project uses `uv sync` for dependency install.
  Install via [astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/).
- **Anthropic API key** (`ANTHROPIC_API_KEY`) — the assistant, the enricher and
  the summariser all need it. You can still start the daemon with
  `--allow-degraded` for a capture-only setup (events are still received,
  persisted and reduced into working memory; nothing talks back).
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

`init` is idempotent — re-running never clobbers your existing `.env` or
engagement YAML. What it creates:

- `~/.redrobin/.env` — pinned env config (chmod 600 on POSIX).
- `~/.redrobin/engagements/<slug>/engagement.yaml` — placeholder scope.
- `~/.redrobin/adapters/` — where your adapter YAML goes (see §5).

`~/.redrobin/transport.token` (the bearer token the TUI and adapters
authenticate with) is minted on first daemon start, not by `init`.

See `docs/secrets-handling.md` for caveats on Git Bash, Windows ACLs, and
the dotenv CWD-pin behavior. See `docs/operator-cli-conventions.md` for the
interactive vs non-interactive pattern.

## 3. `redrobin doctor`

Full preflight — env vars, root layout, and real network probes against
Anthropic, Voyage, and any configured CS adapter:

```bash
uv run redrobin doctor
```

```
[*] Red Robin doctor (preflight check)
Configuration:
  ROOT:               /home/operator/.redrobin
  ENGAGEMENT:         op-smoke
  ANTHROPIC_API_KEY:  set
  VOYAGE_API_KEY:     set
  ROOT_EXISTS:        yes (/home/operator/.redrobin)

Connectivity:
  [OK ] anthropic                         310ms
  [OK ] voyage                            180ms
[+] Doctor finished: OK
```

Quick preflight — env presence only, no network calls (useful in CI):

```bash
uv run redrobin doctor --quick
```

Exit code 0 means OK. Exit code 1 means DEGRADED — capture and working
memory still work, but the assistant may be disabled or KB retrieval may
come back empty. The output names each missing piece so you know what to
fix.

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
patterns, and CIDRs. `out_of_scope` wins on conflict. Scope is **context,
not enforcement** — Red Robin projects it into working memory so the
assistant can tell you when you look like you're drifting out of bounds;
it cannot stop the command. `summary` is injected as a cached system block
into the assistant's prompt, so anything you put there shapes its
reasoning.

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
- `events.jsonl` — the raw captured wire events, append-only
- `episodic.db` — one row per command the operator ran (sourced from
  `beacon.command_input`)

Each engagement is isolated: switching engagements never mixes hosts,
credentials, or command history between them. The daemon resolves the
active engagement at startup with this precedence:

1. `REDROBIN_ENGAGEMENT_SLUG` env var (explicit override, primarily for
   CI/tests)
2. `~/.redrobin/.active_engagement` (written by `redrobin engagement use`)
3. None — degraded mode

**Degraded mode** (no engagement active): the daemon writes to root paths
`~/.redrobin/{working_memory.json, episodic.db}` and tags all episodes
with `engagement_id="eng-default"`. The daemon banner emits a clear
warning when this happens — state won't be persisted per-engagement.
This mode primarily exists for tests and dev scripts; production operators
should always activate an engagement first.

**Cross-engagement state** (shared, NOT per-engagement):

- `~/.redrobin/longterm.db` — long-term lessons, written when the assistant
  captures one (there is no automatic promoter)
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

Two independent pieces, and it's worth keeping them straight:

- The **`.cna`** is the capture hook. Loaded in the CS Client, it observes
  what you type and what comes back and POSTs it to the daemon. It needs no
  YAML — see "Cobalt Strike Client Java requirement" below for loading it.
- The **adapter YAML** below configures the daemon's own REST client to the
  team server. At startup the daemon logs that client in and reports it on the
  `Adapters:` banner line, but **nothing in the daemon calls it yet** — there is
  no TUI-dispatch path and no beacon-state recovery today. It is wired-but-unused
  capability. Optional; skip it unless you want the login verified, because
  capture works entirely without it.

To wire the REST client, drop an adapter YAML:

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

### What the `.cna` does (and does not) do

The `.cna` is a **capture hook only**. It installs no `alias` overrides, so
your beacon console behaves exactly like stock Cobalt Strike — nothing is
intercepted, delayed, blocked or rewritten, and Red Robin prints no verdict
line into the beacon console.

Three capture surfaces, all passthrough-safe:

| Aggressor hook       | Wire event               |
| -------------------- | ------------------------ |
| `on beacon_input`    | `beacon.command_input`   |
| `set BEACON_OUTPUT`  | `beacon.command_output` (always returns `$2` unchanged) |
| `on beacon_initial`  | `beacon.initial` (plus a load-time backfill for beacons already checked in) |

Each is POSTed to `POST /v1/events` on the daemon. The leg is **one-way**:
nothing flows from the daemon back to the CS Client. Everything the assistant
has to say arrives in the TUI's Assistant Chat pane, on its own clock —
unprompted when it judges something material, or on demand when you ask.

Two console lines confirm the script is live:

```
[Red Robin] handshake OK; session=ses_...
[Red Robin] .cna loaded; capture active (observe-only, no interception).
```

**There is no periodic heartbeat today.** The 30-second `fork{}` that would poll
`/v1/adapter/heartbeat` is commented out in `redrobin.cna` (Sleep's `fork()` cannot
see `$REDROBIN_HTTP`; re-enabling it needs the `lambda(...)` rewrite tracked as
#110). Do not expect capture loss to announce itself on a timer.

What you get instead: every capture POST that fails warns immediately in the CS
event log, so you learn the daemon is down on the **next command you type** —

```
[Red Robin] daemon connection refused at http://127.0.0.1:7777 — is the daemon running?
```

If you are idle, you will not be told. Check the daemon terminal if in doubt.

The captured command history is on disk per engagement — useful when you want
the record rather than the chat:

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

The startup banner prints to stderr — look for the `Anthropic: set` and
`Voyage: set` lines. If either says `MISSING (degraded)`, the banner also
tells you what's disabled.

**The assistant is dark-launched — off unless you ask for it.** Capture,
persistence and working memory all run either way, but the chat pane stays
mute until you opt in:

```bash
REDROBIN_ASSISTANT_ENABLED=1 uv run redrobin daemon start
```

(or add `REDROBIN_ASSISTANT_ENABLED=1` to `~/.redrobin/.env`, which `init`
does not write for you).

To run without LLM keys at all (capture + working memory only, no assistant):

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

Three panes: **Sessions** (live beacons), **Assistant Chat** (the copilot —
this is where all advice appears), **Working Memory** (hosts, credentials,
findings, objectives). `c` focuses the chat, `i` opens the inspector, `?`
shows help, `q` quits.

## 8. Smoke test

With the daemon and TUI up and the `.cna` loaded, run something harmless in
a beacon console — `whoami` will do. Within a second or two:

- **Sessions** shows the beacon.
- **Working Memory** picks up the host/user facts the output reveals.
- **Assistant Chat** may or may not say anything. Silence is correct
  behaviour, not a failure: the assistant is deliberately selective and
  speaks only when it judges something material. To force a round-trip, type
  a question into the chat pane (`c` to focus) — e.g. *"what do you know
  about this host?"* — and you should get an answer citing working memory.

Nothing appears in the beacon console, and nothing you type there is delayed
or blocked. That is the intended behaviour: **the adapter captures, the core
advises.**

If the chat pane stays silent even when asked, check two things: that the
daemon was started with `REDROBIN_ASSISTANT_ENABLED=1`, and that the banner
does not say `Anthropic: MISSING (degraded)`. Either one leaves capture
working and the assistant switched off.

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
