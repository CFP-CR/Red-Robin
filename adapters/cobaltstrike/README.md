# Cobalt Strike adapter

`redrobin.cna` is the whole adapter. Loaded in the CS Client, it observes what
you type and what comes back and POSTs it to the Red Robin daemon.

It is a **capture hook only**. It installs no `alias` overrides and writes
nothing to the Beacon console, so your console behaves exactly like stock Cobalt
Strike — nothing is intercepted, delayed, blocked or rewritten. There is nothing
to configure on the daemon side: Red Robin never connects to the team server.

## Load it

1. Start the daemon (`uv run redrobin daemon start`).
2. CS Client → **Scripts** → **Load** → `adapters/cobaltstrike/redrobin.cna`.
3. The Script Console should show both lines:

   ```
   [Red Robin] handshake OK; session=ses_...
   [Red Robin] .cna loaded; capture active (observe-only, no interception).
   ```

After any edit to the `.cna` you must **unload and reload** it — a stale script
looks exactly like a code bug.

## Requirements

- **Cobalt Strike 4.12+** with **Java 11+** on the client. CS 4.12 bundles
  OpenJDK 17 in its standard launcher, so a stock launch is already supported.
  Launch via `cobaltstrike.exe` / `./cobaltstrike-client`, not `java -jar`.
- **No JVM flags.** All HTTP work happens inside a helper JAR loaded by
  `URLClassLoader`; Sleep reflection only ever targets our own public
  `com.redrobin.aggressor.RedRobinHttp` class, so JVM access checks pass.
- **The `jars/` directory must sit next to the `.cna`.** A fresh clone is already
  correct:

  ```
  adapters/cobaltstrike/
  ├── redrobin.cna
  └── jars/
      ├── redrobin-aggressor-http-1.0.2.jar
      └── json-20240303.jar
  ```

  If you copy the `.cna` elsewhere, copy `jars/` with it.

## Settings

Two values are hardcoded at the top of `redrobin.cna`:

| Value | Default | Change it when |
| --- | --- | --- |
| `$REDROBIN_URL` | `http://localhost:7777` | The daemon is not on the CS Client machine, or you moved its port. |
| token path | `~/.redrobin/transport.token` (of the user running the CS Client) | The CS Client runs as a different user, or on a different machine, from the daemon. Copy the token file there. |

The token must be byte-identical to the one the daemon uses, or the daemon
answers 401.

## What it captures

| Aggressor hook | Wire event |
| --- | --- |
| `on beacon_input` | `beacon.command_input` |
| `set BEACON_OUTPUT` | `beacon.command_output` (always returns `$2` unchanged) |
| `on beacon_initial` | `beacon.initial`, plus a load-time backfill for Beacons that had already checked in |

Each is POSTed to `POST /v1/events`. The leg is **one-way**: nothing flows from
the daemon back to the CS Client. Everything the assistant has to say arrives in
the TUI's Assistant Chat pane.

## Failure modes

**There is no heartbeat.** The 30-second liveness poll is commented out (Sleep's
`fork()` cannot see the JAR handle; re-enabling it needs a `lambda(...)` rewrite,
tracked as #110). Capture loss will not announce itself on a timer.

What you get instead: every capture POST that fails warns immediately in the
Script Console, so you learn the daemon is down on the **next command you type**.

```
[Red Robin] daemon connection refused at http://localhost:7777 — is the daemon running?
```

If you are idle, you will not be told. Check the daemon terminal if in doubt.

**Handshake fails.** The `.cna` POSTs `/v1/adapters/connect` at load time and
retries three times before giving up. `redrobin doctor` does not cover this path
— it only probes Anthropic and Voyage. Three real causes:

- `$REDROBIN_URL` points at the wrong host:port, or the daemon is unreachable
  from the CS Client machine — the daemon never sees the POST at all.
- The token mismatches, so the daemon answers 401.
- The token file is missing, and the script throws `FileNotFound` on load.

Fix, then **reload the script**. The retry loop does not resume on its own.

**`Java ... detected. Red Robin requires Java 11+`** — the CS Client JVM is
older than 11. Use Temurin 17 or 21.

**`ClassNotFoundException: com.redrobin.aggressor.RedRobinHttp`** — the JAR is
missing or misplaced. Check the sibling-`jars/` layout above.

## Acceptance testing

CI never runs the `.cna`. Aggressor changes are accepted by a live-Beacon
operator smoke only:

- [`tests/test_cna_smoke.md`](tests/test_cna_smoke.md) — adapter-side acceptance.
- [`../../scripts/smoke/rr-assistant-smoke-runbook.md`](../../scripts/smoke/rr-assistant-smoke-runbook.md)
  — the assistant surface over a real Beacon.

## Building the helper JAR

See [`helper-jar/README.md`](helper-jar/README.md). The committed JAR is
prebuilt; you only need this if you change the Java sources.
