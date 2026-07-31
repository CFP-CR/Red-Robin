# Operator setup

First run, start to finish: a daemon capturing, a TUI connected, and an
assistant that answers. About ten minutes with your API keys to hand.

This is the learning path. Once you know it, [configuration.md](configuration.md)
is the reference for everything you can tune.

## Before you start

- **Python 3.11+** and **[`uv`](https://docs.astral.sh/uv/getting-started/installation/)**.
- An **Anthropic API key**. Without it the assistant, the enricher and the
  summariser are all off.
- A **Voyage API key**. Without it knowledge-base retrieval returns nothing.
- Cobalt Strike is **not** required to complete this walkthrough.

Run every command from the repo checkout. `uv run` finds the project from there,
and the CLI loads `<root>/.env` on top of `<cwd>/.env`, so you never have to `cd`
into `~/.redrobin`.

## 1. Install

```bash
git clone https://github.com/CFP-CR/Red-Robin.git
cd Red-Robin
uv sync --all-extras
```

## 2. Initialise

Name the engagement now — `init` creates and activates it, and getting it right
here saves a daemon restart later.

```bash
uv run redrobin init --engagement-slug acme-q2-2026
```

It prompts for both API keys with hidden input, then writes:

| Path | What it is |
| --- | --- |
| `~/.redrobin/.env` | your keys and daemon settings (chmod 600 on POSIX) |
| `~/.redrobin/engagements/acme-q2-2026/engagement.yaml` | placeholder scope, yours to edit |
| `~/.redrobin/.active_engagement` | marks that engagement active |
| `~/.redrobin/transport.token` | bearer token for the TUI and the `.cna` |

`init` is idempotent: re-running never overwrites an existing `.env`,
`engagement.yaml`, or active engagement.

**On Git Bash / MSYS2, use the non-interactive form.** Python's `getpass` cannot
guarantee hidden input there, so `init` warns and may hang on the prompt:

```bash
uv run redrobin init \
  --non-interactive \
  --anthropic-key "sk-ant-..." \
  --voyage-key "pa-..." \
  --engagement-slug acme-q2-2026
```

## 3. Check the setup

```bash
uv run redrobin doctor
```

```
[*] Red Robin doctor (preflight check)
Configuration:
  ROOT:               /home/operator/.redrobin
  ENGAGEMENT:         acme-q2-2026
  ANTHROPIC_API_KEY:  set
  VOYAGE_API_KEY:     set
  ROOT_EXISTS:        yes (/home/operator/.redrobin)

Connectivity:
  [OK ] anthropic                        1510ms
  [OK ] voyage                            768ms
[+] Doctor finished: OK
```

Exit code 0 is a pass. **`set` only means the variable is populated** — the
`Connectivity` block is what proves the key works. An expired or revoked key
still reads `set` and fails below it:

```
Connectivity:
  [FAIL] anthropic                         789ms  HTTP 401
  [OK ] voyage                            763ms
[+] Doctor finished: DEGRADED (command capture still works)
```

That footer is softer than the situation: `DEGRADED` and exit code 1 mean the
assistant, enricher, summariser and KB retrieval are all off. Only capture and
working-memory persistence survive. Fix the key in `~/.redrobin/.env` and re-run.

`uv run redrobin doctor --quick` skips the network probes — fine for CI, useless
for catching a dead key.

## 4. Describe the engagement

The daemon reads this file once at startup, and the assistant reasons from it.
An engagement with no scope and no summary produces generic advice.

```bash
$EDITOR ~/.redrobin/engagements/acme-q2-2026/engagement.yaml
```

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

Every field is optional and every field is used. See
[configuration.md](configuration.md#engagementyaml) for what each one does.

**Scope is context, not enforcement.** Red Robin projects it into working memory
so the assistant can tell you when you look like you are drifting out of bounds.
It cannot stop a command.

## 5. Start the daemon

```bash
uv run redrobin daemon start
```

This shell blocks. The banner goes to stderr:

```
======================================================================
  Red Robin v0.1.0
======================================================================
  Listening:  http://127.0.0.1:7777
  Root:       /home/operator/.redrobin
  Engagement: Acme Q2 2026 internal
  Anthropic:  set
  Voyage:     set
  L5 window:  25 (every 30.0s)
======================================================================
```

Check `Engagement` names the one you meant and neither key says
`MISSING (degraded)`. To run capture-only with no keys at all, add
`--allow-degraded`.

## 6. Connect the TUI

In a second terminal, **from the checkout, with `--no-sync`**:

```bash
uv run --no-sync redrobin tui
```

The `--no-sync` is not optional while the daemon is running. Plain `uv run`
re-syncs the environment first, and on Windows it cannot replace the running
`redrobin.exe`:

```
error: failed to remove file `...\.venv\Scripts\redrobin.exe`:
The process cannot access the file because it is being used by another process. (os error 32)
```

That is `uv` behaviour, not Red Robin's. `--no-sync` skips the re-sync.

Three panes: **Sessions** (live Beacons), **Assistant Chat** (where every piece
of advice appears), **Working Memory** (hosts, credentials, findings,
objectives). `c` focuses the chat, `i` opens the inspector, `?` shows the keymap,
`q` quits. The token is read from `~/.redrobin/transport.token` automatically.

## 7. Confirm the assistant answers

Press `c` and ask it something about the engagement — *"what is the scope of this
engagement and what EDR am I up against?"* You should get an answer built from
the `engagement.yaml` you just wrote:

```
**In-scope:** corp.acme.local domain and 10.10.0.0/16 network
**Out-of-scope:** bigcorp.com domain and 10.99.0.0/16 network - avoid entirely.
...
**EDR / defenses observed:** None logged yet.
```

That round trip proves the whole chain: TUI socket, engagement load, assistant,
and your Anthropic key.

**Unprompted silence is correct.** The assistant is deliberately selective and
speaks on its own only when it judges something material. A direct question is
the deterministic check.

## 8. Stop

```bash
uv run --no-sync redrobin daemon stop
```

```
daemon stopped (pid=42940)
```

## Next

- **Attach Cobalt Strike:** [adapters/cobaltstrike/README.md](../adapters/cobaltstrike/README.md).
- **Another C2 (Sliver, Havoc, ...):** _planned, not built yet._ The daemon is
  C2-agnostic and speaks only the wire protocol, so a new C2 is a new adapter
  under `adapters/<name>/` and nothing on this page changes. See
  [Adding an adapter](../CONTRIBUTING.md#adding-an-adapter).
- **Verify the capture path without a Beacon:** [smoke-test.md](smoke-test.md).
- **Feed the knowledge base:** [kb-cli.md](kb-cli.md).

## Troubleshooting

- **`os error 32` from `uv run`.** The daemon holds `redrobin.exe`. Use
  `uv run --no-sync`.
- **`doctor` says `set` but the assistant never answers.** Read the
  `Connectivity` block, not the `set` lines. A 401 there means the key is
  expired or revoked.
- **Banner says `Engagement:` is the wrong one.** The daemon resolves the
  engagement once, at startup. Run `redrobin engagement use <slug>` and restart
  the daemon — see
  [configuration.md](configuration.md#switching-engagements) for what happens if
  you do not.
- **No banner at all.** It goes to stderr. If you redirected stderr you will not
  see it.
- **TUI cannot authenticate.** Confirm `~/.redrobin/transport.token` exists and
  is readable. Delete it and restart the daemon to mint a fresh one — then
  reload the `.cna`, which reads the same file.
- **Daemon will not bind.** Default is `127.0.0.1:7777`; pass `--host` / `--port`
  to change it. Note the banner prints the values from `.env`, not the flags, so
  it will still say `7777` if you overrode the port on the command line.
