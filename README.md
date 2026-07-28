# Red Robin

> **AI red team companion that thinks with you, not for you.**

Red Robin is a Python daemon + Textual TUI + Cobalt Strike adapter that watches
what you run on your Beacons and advises you in a chat pane, grounded in your
engagement scope, your knowledge base and what the operation has revealed so far.

It is **advisory-only**: the adapter captures, the core advises. No command is
ever intercepted, blocked, delayed or rewritten, and Red Robin never speaks in
the Beacon console.

**Status:** v0.1, active development. **License:** MIT.

## What it does

- **Observe-only capture.** The Aggressor `.cna` hooks `beacon_input`,
  `BEACON_OUTPUT` and `beacon_initial` and POSTs each command and its output to
  the daemon. It installs no `alias` overrides, so the Beacon console behaves
  exactly like stock Cobalt Strike.
- **Operation-aware assistant.** The copilot reads the captured stream together
  with your engagement scope, objectives and memory, and speaks in the TUI chat
  — unprompted when it judges something material (`opsec_risk` INFO / WARN /
  DANGER / ESCALATE), and on demand when you ask it a question.
- **Engagement memory.** Per-engagement working memory, an append-only capture
  record, episodic command history, and cross-engagement lessons.
- **Adapter pattern.** The daemon is C2-agnostic and speaks only the wire
  protocol. Cobalt Strike is the reference adapter; Sliver-Mock ships for
  protocol validation without a C2.
- **Operator-owned data.** Everything lives under `~/.redrobin/`. Zero
  teamserver footprint. The only outbound calls are to Anthropic (LLM) and
  Voyage (embeddings).

## What it does not do

- Execute, block, delay or rewrite commands. You own every decision.
- Enforce scope. `in_scope` / `out_of_scope` are *context* the assistant reasons
  with, not a control that can stop anything.
- Work without a human. This is augmentation, not autonomy.

## Get started

```bash
git clone https://github.com/CFP-CR/Red-Robin.git
cd Red-Robin
uv sync --all-extras
uv run redrobin init --engagement-slug acme-q2-2026
```

`init` prompts for your Anthropic and Voyage keys, scaffolds `~/.redrobin/`,
creates and activates the engagement, and mints the bearer token the TUI and the
`.cna` authenticate with.

Then follow **[docs/operator-setup.md](docs/operator-setup.md)** — describe the
engagement, start the daemon, connect the TUI. About ten minutes.

## Architecture

```
+-----------+   CS-native (unchanged)  +-----------+
| CS Client | -----------------------> | CS Team   |
| + .cna    |                          | Server    |
+-----------+                          +-----------+
      |
      | HTTP localhost: captured commands + Beacon output
      v
+-------------+        WebSocket        +-------+
| Red Robin   | ----------------------> |  TUI  |
| daemon      |                         +-------+
+-------------+
```

The adapter is the sole C2 boundary. The daemon never connects to the team
server — only the CS Client does, over its own native channel. The `.cna` leg is
one-way: it POSTs captured events to the daemon and nothing flows back to the
Beacon console. Everything the assistant has to say lands in the TUI.

## Documentation

**Operators**

- [Setup walkthrough](docs/operator-setup.md) — clone to a working assistant.
- [Configuration](docs/configuration.md) — `.env` settings and `engagement.yaml`,
  with a worked example.
- [Cobalt Strike adapter](adapters/cobaltstrike/README.md) — loading the `.cna`.
- [Knowledge base](docs/kb-cli.md) — what the assistant retrieves from.
- [Secrets handling](docs/secrets-handling.md) — rotation and leak response.
- [Verify without a Beacon](docs/smoke-test.md) — end-to-end check via Sliver-Mock.

**Contributors**

- [Developer onboarding](docs/developer-onboarding.md)
- [Contributing](CONTRIBUTING.md) — architecture rules, commit conventions, PR flow.
- [Testing](TESTING.md) — the CI gates and the live-Beacon acceptance smokes.
- [Security model](SECURITY.md)
- [Architecture design spec](docs/architecture-design.md) — the v1.2 design
  record. Historical: it predates the removal of the pre-flight gate and
  describes behaviour Red Robin no longer has.

## Help and maintainers

Bugs and questions: [GitHub Issues](https://github.com/CFP-CR/Red-Robin/issues).
Security-sensitive reports: see [SECURITY.md](SECURITY.md).

Maintained by the CFP-CR operators.

## Why "Red Robin"

Augmenting the operator without replacing them. The thesis is in the name: work
*with* the operator, not for them. Don't hack for me, hack with me.
