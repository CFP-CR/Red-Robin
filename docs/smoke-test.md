# Verify the capture path without a Beacon

A five-minute check that the whole daemon-side chain works: an adapter POSTs wire
events, the daemon validates and persists them, the reducer folds them into
working memory, and the TUI renders the result. No Cobalt Strike, no Beacon, no
`.cna`.

Assumes you have finished [operator-setup.md](operator-setup.md) — a daemon that
starts, a TUI that connects, and an active engagement.

The `.cna` itself is **not** covered here. Aggressor changes are accepted by a
live-Beacon operator smoke only — see
[adapters/cobaltstrike/README.md](../adapters/cobaltstrike/README.md#acceptance-testing).

## Run it

Three terminals. In the first, start the daemon and leave it:

```bash
uv run redrobin daemon start
```

In the second, connect the TUI and leave it visible:

```bash
uv run --no-sync redrobin tui
```

In the third, replay a recorded Sliver scenario against the daemon:

```bash
uv run --no-sync python adapters/sliver-mock/adapter.py \
  --scenario acme_corp \
  --daemon http://127.0.0.1:7777
```

```
[sliver-mock] connected as session ses_01KYMRTWQF06204NSRK4XGDCF5
[sliver-mock] sent beacon.initial
[sliver-mock] sent host.observation
[sliver-mock] sent beacon.command_input
[sliver-mock] sent beacon.command_output
[sliver-mock] sent credential.observed
[sliver-mock] sent host.observation
[sliver-mock] sent beacon.command_input
[sliver-mock] disconnected
```

The mock reads its bearer token from `REDROBIN_TOKEN`, falling back to
`~/.redrobin/transport.token`. Set `REDROBIN_TOKEN` explicitly if your root is
somewhere else.

## What should happen

- **Sessions** gains `WS-FINANCE-04.acme.local` / `acme\jdoe`. This leg is
  deterministic — no LLM involved.
- `engagements/<slug>/events.jsonl` gains **seven lines** on a fresh engagement,
  one per wire event. The adapter's connect and disconnect frames are not stored.
- **Working Memory** gains a *finding* a few seconds later, once the enricher has
  called out and back (about two seconds in practice — do not stop the daemon the
  instant the replay finishes or you will race it):

  ```
  findings: [{"kind": "domain_admin_membership",
              "summary": "Domain Admins group contains 3 members: ...",
              "source_beacon_id": "win_ws01"}]
  ```

- **`hosts` and `credentials` stay empty — that is correct for this scenario**,
  even with a working Anthropic key. The only command output is a `net group
  "Domain Admins"` listing, which names no host, and its three bare usernames
  carry no secret material for a credential to attach to.

Then press `c` in the TUI and ask *"what do you know about this host?"*. You
should get an answer grounded in the Beacon shown in Sessions. Unprompted
advisories are **not** a pass criterion — the assistant is deliberately
selective, and silence during a short replay is correct. The direct question is
the deterministic check.

Finally:

```bash
uv run --no-sync redrobin daemon stop
```

The daemon drains its background tasks before closing the bus, so its stdout
should show no tracebacks on shutdown.

## If a step fails

- **Replay exits 401.** The mock's token did not match. Set `REDROBIN_TOKEN` to
  the contents of your root's `transport.token`.
- **Replay exits 400 with `envelope_errors` / `event_errors`.** `/v1/events`
  validates against `protocol/schemas/v0.1/`, and the schemas are
  `additionalProperties: false`, so an unknown key is a hard reject. If you
  hand-edited a scenario, diff it against `protocol/fixtures/v0.1/valid/`.
- **Replay succeeded but nothing reached the TUI.** Check the daemon banner for
  `Engagement: (none)`. With no active engagement `/v1/events` returns
  `stored: false` and writes nothing. Run `redrobin engagement use <slug>` and
  restart the daemon.
- **TUI says the WebSocket failed.** The daemon is not running, or it bound a
  different host/port than the TUI's default `ws://127.0.0.1:7777/v1/tui`.
- **Chat never answers.** Confirm `REDROBIN_ASSISTANT_ENABLED=1` in your `.env`
  and that `redrobin doctor` shows `[OK ] anthropic` — not merely
  `ANTHROPIC_API_KEY: set`.

## What this proves

`init` produced a working layout; the daemon boots its lifespan in order; the
capture path holds end to end (adapter → `POST /v1/events` → schema validation →
`events.jsonl` → event bus → reducer → TUI); the enricher fills working memory
from captured output; the assistant answers grounded in it; and shutdown is
clean.

It does **not** prove anything about the Cobalt Strike `.cna`. Only a live Beacon
can.
