# Secrets handling

How Red Robin stores secrets, how to rotate them, what to do if one leaks,
and platform-specific caveats every operator needs to know.

## Where secrets live

Red Robin reads two classes of secret:

1. **API keys** — `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`. Live in
   `~/.redrobin/.env` (created by `redrobin init`). Also overridable via
   the shell environment (explicit shell env wins).
2. **Adapter credentials** — CS team-server username/password. Live in
   `~/.redrobin/adapters/*.yaml` (one YAML per adapter instance).

Both directories sit under `~/.redrobin/`, which `redrobin init` creates
with `chmod 700` on POSIX. The `.gitignore` covers `.env` and
`adapters/cobaltstrike/*.yaml` (allow-listing only `*.example.yaml` for
shareable templates).

## Rotation procedure

### API keys

1. Issue a new key at the provider:
   - Anthropic: <https://console.anthropic.com/settings/keys>
   - Voyage: <https://dash.voyageai.com/api-keys>
2. Edit `~/.redrobin/.env`. Replace the relevant `KEY=...` line.
3. Restart the daemon: `redrobin daemon stop && redrobin daemon start`.
4. Revoke the old key at the provider.

The daemon does **not** hot-reload `.env`. A restart is mandatory.

### Adapter credentials

1. Rotate the CS operator password on the team server.
2. Edit `~/.redrobin/adapters/cs-main.yaml`. Update `operator_password`.
3. Restart the daemon.

The CS adapter `login()` call runs at daemon startup; bad credentials
surface as a `FAIL` line under `Connectivity:` in `redrobin doctor`.

## Leak response

If you suspect a secret leaked (committed to a repo, screen-shared,
posted in a chat, copied to a logfile):

1. **Rotate at the provider, first.** Revoking the key on Anthropic /
   Voyage / your CS team server is the only thing that actually stops the
   leak. Editing `.env` does not invalidate the leaked value.
2. **Check git history.** The `.gitignore` covers `.env` and
   `adapters/cobaltstrike/*.yaml`, but verify nothing slipped:
   ```bash
   git log --all -p -S 'sk-ant-' -- ':!*.example.yaml'
   git log --all -p -S 'ANTHROPIC_API_KEY=' -- ':!*.example.yaml'
   git log --all -p -- adapters/cobaltstrike/cs-main.yaml
   ```
   Any hit means the secret was committed at some point. Force-push won't
   help — assume the value is compromised forever and rely on the
   revocation at the provider.
3. **Replace in `.env` / adapter YAML.** Then restart the daemon.
4. **Audit logs.** Red Robin **does not log** API key values (the startup
   banner prints `set` or `MISSING (degraded)` only, never values) nor
   adapter passwords (`tests/integration/test_adapter_perms.py::test_no_password_in_logs`
   pins this). But operator-side logs (shell history, terminal scrollback,
   editor swap files) may have captured the value. Clear them.

## File permissions

### `.env`

POSIX: `redrobin init` calls `chmod 0o600` automatically when creating
`.env`. If you create or edit `.env` outside of `init`, re-apply manually:

```bash
chmod 600 ~/.redrobin/.env
```

Windows: the `init` chmod call is skipped on `sys.platform == "win32"`.
Rely on NTFS ACLs — see the Windows ACL caveat below.

### Adapter YAMLs

POSIX: chmod 600 manually after copying the example:

```bash
chmod 600 ~/.redrobin/adapters/cs-main.yaml
```

The adapter loader emits a stderr WARN at daemon boot if any
`*.yaml` under `~/.redrobin/adapters/` is group or world readable /
writable. The warning prints to stderr deliberately (not via the logger)
so the no-password-in-logs contract stays clean — operators still see it
on the terminal at startup.

### Windows ACL caveat

The loose-perms check is POSIX-only. It checks
`stat.S_IRGRP | stat.S_IROTH | stat.S_IWGRP | stat.S_IWOTH` and exits
early on `sys.platform == "win32"`. Windows operators are **not**
protected by the WARN.

Options:

1. **`icacls`** — explicitly restrict the file:
   ```powershell
   icacls $env:USERPROFILE\.redrobin\adapters\cs-main.yaml /inheritance:r
   icacls $env:USERPROFILE\.redrobin\adapters\cs-main.yaml /grant:r "$env:USERNAME:(R,W)"
   ```
2. **Run the daemon under a Windows account whose NTFS ACLs already
   restrict `~/.redrobin/` to that account.** Default Windows ACLs on a
   user profile directory grant only `SYSTEM`, `Administrators`, and the
   profile owner — usually sufficient if the daemon and the operator run
   as the same user.

Audit pass 1 #16 documented this gap; closing it requires per-platform
ACL inspection that v0.1.x does not implement.

## Git Bash / MSYS2 caveat

`redrobin init`'s interactive flow uses `getpass.getpass()` for hidden
input. On Git Bash / MSYS2 on Windows, the terminal is not a true TTY
from Python's perspective, and `getpass` falls back to **plain-text
echo** (it emits a `GetPassWarning`). The same applies to any
subprocess where stdin is not a TTY — CI runners, `< file` redirects,
piped invocations.

Red Robin's safeguard (audit pass 1 #7):

- **`sys.stdin.isatty()` False → refuses to prompt.** `init` exits with
  code 2 and an error pointing at `--non-interactive --anthropic-key K --voyage-key K`.
- **`MSYSTEM` env var set → WARN.** Git Bash and MSYS2 both set this. The
  prompt still happens (assuming stdin is a TTY) but with a stderr warning
  recommending Windows Terminal or PowerShell for secret entry, or the
  explicit-flag non-interactive form.

Recommended on Windows: run `redrobin init` from **Windows Terminal**,
**PowerShell**, or **cmd.exe**. Avoid Git Bash for the interactive flow.

If you must use Git Bash, pass the keys as flags:

```bash
redrobin init \
  --non-interactive \
  --anthropic-key "sk-ant-..." \
  --voyage-key "pa-..."
```

## dotenv path caveat

`redrobin/cli.py::_load_env_for_cli` pins to:

```python
load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)
```

It does **not** walk parent directories. This is deliberate (audit pass 1
#4): python-dotenv's default behavior walks up from CWD looking for a
`.env`, which can silently pick up an operator's home-directory `.env`
when running from a customer subdirectory — a cross-engagement secret
leakage path.

Implications:

- The `.env` must be in the **exact** directory where you invoke `redrobin`.
- If your `.env` doesn't seem to load (e.g. `redrobin doctor` reports
  `MISSING` for keys you know are set), check your CWD.
- Explicit shell env vars (`export ANTHROPIC_API_KEY=...`) always win
  regardless of `.env` location — the `override=False` argument means
  `.env` values do NOT clobber the shell.

Operator habit: invoke `redrobin` from the Red-Robin checkout directory,
or from `~/.redrobin/` if you put your `.env` there. Don't `cd` into a
customer subdirectory and expect home-directory secrets to come along.

## What does NOT enter logs

- **API key values.** The startup banner shows `set` or `MISSING (degraded)`
  only. No log line carries the literal key.
- **Adapter passwords.** The CS adapter loader and login flow never log
  `operator_password`. Pinned by
  `tests/integration/test_adapter_perms.py::test_no_password_in_logs`.

The loose-perms WARN intentionally prints to stderr rather than via the
logger so even an aggressive log capture (e.g. ELK with broad regex
ingestion) cannot accidentally land the warning in a place that gets
indexed alongside structured event logs.
