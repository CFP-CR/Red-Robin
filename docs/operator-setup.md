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
Client side. CS 4.7+ bundles Java 17+ in its standard distribution, so
operators on stock CS launches are automatically on a supported JVM.

**No JVM flags required.** Earlier Red Robin versions documented an
`--add-opens java.base/sun.net.www.protocol.http=ALL-UNNAMED` workaround
for Java module access issues. As of 2026-05-14 (the HTTP modernization
refactor — see `docs/superpowers/specs/2026-05-14-cna-http-modernization-design.md`),
the `.cna` uses the public `java.net.http.HttpClient` API and no longer
requires any `--add-opens` flag.

If the .cna fails to load with `Java ... detected. Red Robin requires
Java 11+` in the Script Console, upgrade your JVM (Temurin 21 or
Microsoft Build of OpenJDK 17 are both well-tested) and reload the
script.

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
