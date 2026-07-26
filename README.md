# Red Robin

> **AI red team companion that thinks with you, not for you.**

Red Robin is a Python daemon + TUI + Cobalt Strike adapter that watches what
you run on your Beacons, suggests stealthier alternatives grounded in your
knowledge base, and accumulates engagement context across operations. It is
advisory-only: **the adapter captures, the core advises** — no command is ever
intercepted, blocked, or rewritten.

**Status:** v0.1 (DEF CON 34 RTV / La Villa 2026 ship target). Active dev.

**License:** MIT.

## What it does

- **Observe-only capture hook** for Cobalt Strike: the Aggressor `.cna` hooks
  `beacon_input`, `BEACON_OUTPUT` and `beacon_initial` and ships each command
  and its output to the daemon. It installs no `alias` overrides, so the beacon
  console behaves exactly like stock Cobalt Strike.
- **Operation-aware assistant**: the copilot reads the captured stream together
  with your engagement scope, objectives and memory, and speaks in the TUI chat
  — unprompted advisories when something is worth flagging (`opsec_risk` of
  INFO / WARN / DANGER / ESCALATE), plus answers when you ask it directly.
- **5-layer memory system**: working memory (per-engagement), episodic (raw
  command activity), long-term (cross-engagement lessons), tradecraft graph
  (technique→bypass), and context management.
- **Adapter pattern**: the daemon is C2-agnostic. CS adapter (Aggressor `.cna` +
  Python REST client) is the v0.1 reference; Sliver-Mock ships for protocol
  validation + demo; Sliver-Real committed for v0.1.1 (2026-06-22).
- **Operator-owned data**: everything lives in `~/.redrobin/`. Zero teamserver
  footprint. API keys via env var only.

## What it does NOT do

- **Does not execute commands for you.** Red Robin observes and advises.
  The operator owns every decision.
- **Does not block, delay or rewrite anything you type.** There is no
  pre-flight gate. Advice lands in the TUI chat, never in the beacon console,
  and never between you and the teamserver.
- **Does not work without a human.** This is augmentation, not autonomy.
- **Does not exfil engagement data.** The only outbound network calls are to
  Anthropic (LLM) and Voyage (embeddings) — operator opts in per engagement.
  v0.2 local mode (Ollama + sentence-transformers) eliminates these too.

## Quick start

`redrobin init` is the front door. It scaffolds `~/.redrobin/`, prompts for
your API keys (hidden input), and drops a sample engagement. (The bearer
token the TUI uses is minted on first daemon start.) Full walkthrough —
including non-interactive use,
the CS adapter, and smoke-test — in
[`docs/operator-setup.md`](docs/operator-setup.md).

### Install

```bash
git clone https://github.com/CFP-CR/Red-Robin.git
cd Red-Robin
uv sync --all-extras
```

### Init + verify

```bash
uv run redrobin init      # interactive: prompts for keys + engagement slug
uv run redrobin doctor    # preflight: env keys + dependencies + connectivity
```

Non-interactive (CI, Git Bash):

```bash
uv run redrobin init \
  --non-interactive \
  --anthropic-key "sk-ant-..." \
  --voyage-key "pa-..." \
  --engagement-slug op-smoke
```

### Run

```bash
uv run redrobin daemon start    # in one terminal
uv run redrobin tui              # in another terminal
```

### Create an engagement

```bash
uv run redrobin engagement new acme-q2-2026 --client "Acme Corp"
$EDITOR ~/.redrobin/engagements/acme-q2-2026/engagement.yaml
uv run redrobin engagement use acme-q2-2026
```

### Load the CS adapter

In Cobalt Strike Client → Scripts → Load → select
`adapters/cobaltstrike/redrobin.cna`. Verify the Script Console shows

```
[Red Robin] handshake OK; session=ses_...
[Red Robin] .cna loaded; capture active (observe-only, no interception).
```

## Architecture

Full spec at `docs/architecture-design.md` (~2600 lines).

```
+-----------+      HTTP localhost      +-------------+      REST API     +-----------+
| CS Client | -----------------------> | Red Robin   | <---------------- | CS Team   |
| + .cna    |      captured commands   | daemon      |   state/dispatch  | Server    |
+-----------+      + beacon output     +-------------+                   +-----------+
                                           |
                                           v
                                       +-------+
                                       |  TUI  | (WebSocket)
                                       +-------+
```

The CS adapter is the sole C2 boundary. Red Robin core speaks the wire protocol
only — it doesn't know about REST API endpoints, JWT, beacon IDs, etc. The
`.cna` leg is one-way: it POSTs captured events to the daemon and nothing flows
back to the beacon console. Everything the assistant has to say lands in the
TUI.

## Status & roadmap

- **v0.1** (current — DEF CON ship): CS adapter (observe-only capture hook),
  Sliver-Mock, Anthropic cloud LLM, Voyage cloud embeddings, Textual TUI with
  the assistant chat, 5-layer memory.
- **v0.1.1** (2026-06-22): Sliver-Real adapter (protoc-generated gRPC client).
- **v0.2** (post-DEF CON): Local mode (Ollama + sentence-transformers), Havoc
  adapter, Mythic adapter, Tradecraft Graph expansions.

## Documentation

Operator-facing:

- [`docs/operator-setup.md`](docs/operator-setup.md) — first-time daemon
  setup walkthrough (clone → init → doctor → daemon → TUI → smoke test).
- [`docs/secrets-handling.md`](docs/secrets-handling.md) — rotation, leak
  response, file perms (incl. Windows ACL, Git Bash, dotenv path caveats).
- [`docs/kb-cli.md`](docs/kb-cli.md) — building the knowledge base the
  assistant retrieves from (`redrobin kb add`, tag conventions).
- [`docs/operator-cli-conventions.md`](docs/operator-cli-conventions.md) —
  ADR for interactive vs non-interactive subcommands.
- [`docs/demo-runbook.md`](docs/demo-runbook.md) — DEF CON RTV / La Villa
  live-demo checklist + script.

Project / dev:

- Project status: `Projects/red-robin-copilot.md`
- Architecture: `docs/architecture-design.md`
- Developer onboarding: [`docs/developer-onboarding.md`](docs/developer-onboarding.md)
- Security model: `SECURITY.md`
- Contributing: `CONTRIBUTING.md`

## Why "Red Robin"

Augmenting the operator without replacing them. The thesis is in the name:
work *with* the operator, not for them. Don't hack for me, hack with me.
