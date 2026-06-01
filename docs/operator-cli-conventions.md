# Operator CLI conventions

Short ADR-style note on the interactive vs non-interactive pattern that
`redrobin init` (Slice 5) introduced. Future interactive subcommands
should inherit this pattern without re-litigating it.

## Status

Accepted. Implemented in `redrobin init` as of Slice 5 (v0.1.x).
Applies to all future interactive subcommands.

## Context

`redrobin init` is the first Red Robin subcommand that needs to gather
secrets from the operator (API keys). Two failure modes must be handled:

1. **Plain-text echo on Git Bash / non-TTY.** `getpass.getpass()` falls
   back to plain-text input — emitting a `GetPassWarning` — when the
   controlling terminal is not a true TTY. Operators on Git Bash, MSYS2,
   CI runners, or any `< file` redirect will see the secret echo to the
   terminal (and likely the scrollback / log). Documented in
   `docs/secrets-handling.md` under "Git Bash / MSYS2 caveat".
2. **Operators want to script `init` for fresh CI runs / lab provision.**
   Prompting an unattended invocation is a non-starter.

The pattern needs to safely support both interactive humans and scripted
non-interactive runs without making either feel like a second-class
citizen.

## Decision

Every interactive subcommand follows this contract:

1. **`interactive=True` by default.** The command prompts for any
   missing required input via `getpass.getpass()` (secrets) or `input()`
   (non-secret values).

2. **All required values are also acceptable as explicit CLI flags.**
   Example: `--anthropic-key`, `--voyage-key`, `--engagement-slug`. A
   value passed via flag skips the prompt for that specific value.

3. **`--non-interactive` suppresses all prompts.** Any required value
   missing under this flag causes the command to `return 2` with a
   stderr error pointing at the missing flag. The error names which flag
   to pass — never a generic "missing argument".

4. **Refuse to prompt when `sys.stdin.isatty()` is False.** Regardless of
   `interactive=True`. Exit code 2 with a stderr error pointing at
   `--non-interactive` plus the explicit-value flags. This catches Git
   Bash, MSYS2 (also flagged separately via `MSYSTEM`), CI runners, and
   any `< file` redirect — all environments where `getpass` cannot
   guarantee hidden input.

5. **Idempotent on disk.** Re-running the command never clobbers
   existing user-owned files. The command may add missing pieces (a new
   `engagement.yaml` for a slug that doesn't exist yet) but it must not
   rewrite an existing `.env` or adapter YAML. This makes the command
   safe to run repeatedly during operator onboarding.

## Consequences

- Operators on Windows are funneled toward Windows Terminal / PowerShell
  for interactive secret entry, or `--non-interactive` from any shell.
- Scripted invocations are a first-class flow — same surface area, same
  exit codes, no special "scripted mode" parser.
- `getpass`'s plain-text-echo failure mode is impossible: the command
  refuses to prompt in any environment where the fallback would trigger.
- Idempotency means CI can run `init` unconditionally on every job
  without checking whether `.env` already exists.

## Reference implementation

`redrobin/cli_helpers/init_cmd.py::run_init`:

```python
if interactive:
    if not sys.stdin.isatty():
        print("ERROR: stdin is not a TTY. Use --non-interactive ...", file=sys.stderr)
        return 2
    if os.environ.get("MSYSTEM"):
        print("WARN: Git Bash / MSYS2 detected ...", file=sys.stderr)
    if anthropic_key is None:
        anthropic_key = getpass.getpass("ANTHROPIC_API_KEY (input hidden): ").strip()
    if voyage_key is None:
        voyage_key = getpass.getpass("VOYAGE_API_KEY (input hidden): ").strip()
    ...
```

Argparse wiring in `redrobin/cli.py::build_parser` (init subparser):

- `--non-interactive` flag → `args.non_interactive` (bool).
- `--anthropic-key` → `args.anthropic_key` (str or None).
- `--voyage-key` → `args.voyage_key` (str or None).
- `--engagement-slug` → `args.engagement_slug` (str, defaults to `op-default`).

The dispatcher passes `interactive=not args.non_interactive` to `run_init`,
so the helper module never needs to know argparse exists.

## Out of scope (yet)

- Reading secrets from a system keyring (Windows Credential Manager,
  macOS Keychain, libsecret). Possible follow-up; not in v0.1.x.
- Per-secret `--anthropic-key-file PATH` style — for now operators who
  want to avoid shell history echo can use a here-doc or `read -s` shell
  builtin and pass the captured value through `--anthropic-key`.
- Re-running `init` to **rotate** keys. Today `init` won't rewrite an
  existing `.env`; rotation is a manual edit (see `docs/secrets-handling.md`).
  A dedicated `redrobin rotate` could land in a later slice if rotation
  friction becomes a complaint.
