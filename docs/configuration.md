# Configuration

Two files configure Red Robin: `~/.redrobin/.env` (how the daemon runs) and
`~/.redrobin/engagements/<slug>/engagement.yaml` (what the assistant reasons
about). `redrobin init` writes working versions of both.

This page covers the settings an operator actually sets. Everything else has a
default that is correct — see `redrobin/infrastructure/config.py` for the full
list.

## Where settings come from

Highest to lowest precedence:

1. Shell environment (`export ANTHROPIC_API_KEY=...`)
2. `<cwd>/.env`
3. `<root>/.env` — the file `init` writes

Neither `.env` ever overrides the shell, and neither is walked up from parent
directories. Because the root `.env` is loaded too, commands work from the repo
checkout without `cd`-ing into `~/.redrobin`.

The daemon does **not** hot-reload `.env`. Restart it after any change.

## `.env`

This is what `init` generates, minus the keys:

```dotenv
ANTHROPIC_API_KEY=sk-ant-...
VOYAGE_API_KEY=pa-...

REDROBIN_HOST=127.0.0.1
REDROBIN_PORT=7777

REDROBIN_ROOT=/home/operator/.redrobin

REDROBIN_L5_WINDOW=25
REDROBIN_L5_INTERVAL_SEC=30

REDROBIN_ASSISTANT_ENABLED=1

REDROBIN_ALLOW_DEGRADED=0
```

| Setting | Default | What it does |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | — | The assistant, the enricher and the summariser. Without it all three are off. |
| `VOYAGE_API_KEY` | — | Knowledge-base embeddings. Without it `kb_retrieve` returns nothing. |
| `REDROBIN_HOST` / `REDROBIN_PORT` | `127.0.0.1` / `7777` | Where the daemon binds. The `.cna`'s `$REDROBIN_URL` must match. |
| `REDROBIN_ROOT` | `~/.redrobin` | Everything else derives from this. |
| `REDROBIN_ASSISTANT_ENABLED` | `0` in code, `1` from `init` | The assistant. Set to `0` for a capture-only setup: capture, persistence and working memory keep running, the chat pane stays mute. |
| `REDROBIN_ALLOW_DEGRADED` | `0` | Let the daemon boot with no LLM keys. Equivalent to `daemon start --allow-degraded`. |
| `REDROBIN_L5_WINDOW` | `25` | Commands per rolling context summary. |
| `REDROBIN_L5_INTERVAL_SEC` | `30` | How often the summariser checks whether a window is full. |

### Tuning the assistant in a live op

These have sensible defaults; reach for them when the assistant's cadence is
wrong for the operation you are running.

| Setting | Default | What it does |
| --- | --- | --- |
| `REDROBIN_ASSISTANT_COOLDOWN_SEC` | `30` | Minimum gap between reactive advisories. Raise it if the assistant is noisy on a fast-moving op. |
| `REDROBIN_ASSISTANT_LULL_SEC` | `300` | Idle time before the assistant speaks up unprompted. |
| `REDROBIN_ASSISTANT_PROACTIVE_MIN_INTERVAL_SEC` | `600` | Rate limit on those unprompted advisories. |
| `REDROBIN_ENRICHMENT_DENYLIST` | empty | Commands whose output is never sent to the enricher. Comma-separated (`ls,cd,sleep`) or a JSON array. Use it to mute a command that proves noisy — or sensitive — mid-engagement. |

### Do not set `REDROBIN_ENGAGEMENT_SLUG`

It outranks the `.active_engagement` marker that `redrobin engagement use`
writes, so pinning it makes every later `engagement use` a silent no-op. `init`
deliberately leaves it out of `.env`. It exists for CI and tests.

Likewise `REDROBIN_WORKING_MEMORY_PATH` and `REDROBIN_EPISODIC_PATH` are ignored
whenever an engagement is active — those paths are derived from the engagement
directory unconditionally.

## `engagement.yaml`

One per engagement, at `~/.redrobin/engagements/<slug>/engagement.yaml`. The
daemon loads it once at startup. Every field is optional; a missing or malformed
field degrades to an empty default and logs a warning rather than crashing the
daemon.

```yaml
slug: acme-q2-2026
name: Acme Q2 2026 internal
client: Acme Corp

in_scope:
  - corp.acme.local
  - 10.10.0.0/16
out_of_scope:
  - bigcorp.com
  - 10.99.0.0/16

summary: |
  Internal red team. EDR is CrowdStrike Falcon on servers, Defender on
  workstations. Working hours only, no destructive actions. No Mimikatz
  binary on disk and no LSASS dumping by name.

rules_of_engagement:
  - No destructive actions or DoS
  - Working hours only (08:00-18:00 CST)
  - Notify the client POC before any domain-wide change

objectives:
  - id: obj_1
    goal: Compromise the domain controller
    priority: high
  - id: obj_2
    goal: Access the finance fileshare
    priority: medium
```

| Field | Type | What it does |
| --- | --- | --- |
| `slug` | string | Lowercase alphanumeric plus `-` / `_`, max 64 chars. Must match the directory name. |
| `name` | string | Shown in the daemon banner and the TUI topbar. |
| `client` | string | Projected into working memory. |
| `in_scope` / `out_of_scope` | list | Hostnames, suffix patterns, CIDRs, or `*`. `out_of_scope` wins on conflict. Must be YAML lists — a bare scalar is ignored with a warning. |
| `summary` | string | Injected into the assistant's system prompt. The highest-leverage field in the file: EDR vendor, tradecraft constraints, anything that should shape every answer. |
| `rules_of_engagement` | list | ROE lines the assistant reasons against. |
| `objectives` | list | Each needs an `id`; `goal` and `priority` (`low`/`medium`/`high`) are optional. The assistant tracks progress against these. |

**Scope is context, not enforcement.** It is projected into working memory so the
assistant can flag drift. Nothing stops the command.

## Managing engagements

```bash
uv run redrobin engagement new acme-q3-2026 --client "Acme Corp" \
  --in-scope "corp.acme.local,10.10.0.0/16" --out-of-scope bigcorp.com
uv run redrobin engagement use acme-q3-2026
uv run redrobin engagement list       # marks the active one
uv run redrobin engagement current
uv run redrobin engagement close [<slug>]
uv run redrobin engagement archive <slug>
uv run redrobin engagement delete <slug> --confirm
```

`engagement new` writes the YAML from your flags and leaves `summary`,
`rules_of_engagement` and `objectives` empty — fill them in before you start.

### Switching engagements

**Restart the daemon after `engagement use`.** It resolves the engagement once,
at startup, and switching underneath a running daemon splits your state in two:

- `events.jsonl` follows the marker immediately — new captures land under the
  **new** engagement.
- Working memory, episodic history and the assistant's scope stay bound to the
  engagement the daemon **booted** with.

So the raw capture record goes one way and everything the assistant sees goes the
other, silently, until you restart.

```bash
uv run --no-sync redrobin daemon stop
uv run redrobin engagement use acme-q3-2026
uv run redrobin daemon start
```

## On-disk layout

```
~/.redrobin/
├── .env                       # settings above (chmod 600 on POSIX)
├── .active_engagement         # which engagement is live
├── transport.token            # bearer token for the TUI and the .cna
├── longterm.db                # cross-engagement lessons
├── context_summaries.db       # rolling context summaries
├── kb-source/                 # your KB markdown
├── shared/
│   └── kb-index/index.npz     # the vector index (see kb-cli.md)
└── engagements/
    ├── archived/
    └── acme-q2-2026/
        ├── engagement.yaml    # you edit this
        ├── working_memory.json
        ├── events.jsonl       # append-only capture record
        ├── episodic.db        # one row per command
        └── chat_history.jsonl
```

Engagements are isolated: switching never mixes hosts, credentials or command
history. `longterm.db` and `context_summaries.db` are shared by design.

Inspect the captured command history directly with:

```bash
uv run --no-sync python tools/inspect_episodic.py ~/.redrobin/engagements/<slug>/episodic.db
```
