# Secrets handling

Where Red Robin's secrets live, how to rotate them, and what to do if one leaks.

## What there is to leak

- **API keys** — `ANTHROPIC_API_KEY` and `VOYAGE_API_KEY`, in `~/.redrobin/.env`
  (or the shell environment, which wins).
- **The transport token** — `~/.redrobin/transport.token`, the bearer token the
  TUI and the `.cna` authenticate to the daemon with.

That is the whole list. Red Robin holds **no C2 credentials**: it never connects
to a team server. The `.cna` runs inside the already-authenticated CS Client and
only POSTs captured activity inbound to the daemon.

Neither secret ever enters a log. The startup banner prints `set` or
`MISSING (degraded)`, never a value; auth failures log the outcome, never the
bearer token.

## Rotating an API key

1. Issue a new key —
   [Anthropic](https://console.anthropic.com/settings/keys) /
   [Voyage](https://dash.voyageai.com/api-keys).
2. Replace the `KEY=...` line in `~/.redrobin/.env`.
3. `redrobin daemon stop && redrobin daemon start`. The daemon does **not**
   hot-reload `.env`; a restart is mandatory.
4. Revoke the old key at the provider.

`redrobin init` will not do this for you — it never rewrites an existing `.env`.

## Rotating the transport token

Delete `~/.redrobin/transport.token` and restart the daemon; a fresh token is
minted on start. Copy it to wherever the `.cna` reads it from, then reload the
script in the CS Client.

## If a key leaks

1. **Revoke it at the provider first.** Editing `.env` does nothing to the
   leaked value.
2. **Check git history.** `.gitignore` covers `.env`, but verify:

   ```bash
   git log --all -p -S 'sk-ant-'
   git log --all -p -S 'ANTHROPIC_API_KEY='
   ```

   Any hit means it was committed at some point. A force-push does not help —
   assume the value is compromised forever and rely on the revocation.
3. **Replace it in `.env` and restart the daemon.**
4. **Clear operator-side traces.** Shell history, terminal scrollback, editor
   swap files.

## File permissions

`redrobin init` creates `~/.redrobin/` at `chmod 700` and `.env` at `chmod 600`
on POSIX. The daemon writes `transport.token` at `600`.

**Both chmod calls are skipped on Windows.** Nothing else in the tree is
permission-hardened on any platform, so treat full-disk encryption (BitLocker,
FileVault, LUKS) as the real control. To restrict `.env` explicitly on Windows:

```powershell
icacls $env:USERPROFILE\.redrobin\.env /inheritance:r
icacls $env:USERPROFILE\.redrobin\.env /grant:r "$env:USERNAME:(R,W)"
```

Default Windows ACLs on a profile directory already grant only `SYSTEM`,
`Administrators` and the profile owner, which is usually enough when the daemon
and the operator are the same user.

## Entering keys on Git Bash / MSYS2

`redrobin init`'s interactive flow uses `getpass.getpass()` for hidden input.
On Git Bash and MSYS2 the terminal is not a true TTY from Python's perspective,
and `getpass` can fall back to **plain-text echo**. The same applies to any
non-TTY stdin — CI runners, `< file` redirects, pipes.

Red Robin's safeguards: `init` refuses to prompt when `sys.stdin.isatty()` is
false (exit 2, pointing at the explicit flags), and warns when `MSYSTEM` is set.
In practice on Git Bash you will see the warning and then a prompt that may
never return.

Use Windows Terminal or PowerShell for interactive entry, or pass the keys as
flags from any shell:

```bash
redrobin init \
  --non-interactive \
  --anthropic-key "sk-ant-..." \
  --voyage-key "pa-..." \
  --engagement-slug acme-q2-2026
```

Flags land in shell history. `read -s` into a variable first if that matters.
