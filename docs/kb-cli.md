# Knowledge Base CLI

Red Robin's knowledge base (KB) is a single per-operator vector index that the
assistant retrieves from (its `kb_retrieve` tool) when it reasons about what
you just ran or answers a question in the chat pane. The KB is content the
*operator* curates — vault notes, prior engagement reports, exploit write-ups,
MITRE technique pages. Anything an LLM would do better with at hand.

The KB is built and managed via the `redrobin kb` subcommand group. As of
v0.1, only `redrobin kb add` is wired end-to-end. The other commands
(`list`, `remove`, `query`) are tracked on the v0.1.x roadmap below.

## `redrobin kb add`

Read a markdown file, chunk it, embed each chunk via Voyage, and upsert
the chunks into the shared vector store.

```
redrobin kb add <path> [--tag TAG ...] [--chunk-size N] [--chunk-overlap N] [--watch]
```

| Flag | Default | Purpose |
| --- | --- | --- |
| `<path>` | (required) | Path to a single markdown file. Directory-level ingest is on the v0.1.x roadmap. |
| `--tag TAG` | none | Repeatable. Free-form metadata tag attached to every chunk (e.g. `--tag mimikatz --tag lsass`). |
| `--chunk-size` | `1200` | Max characters per chunk before splitting. |
| `--chunk-overlap` | `200` | Characters of overlap between consecutive chunks (keeps cross-chunk context intact). |
| `--watch` | off | Reserved for future use; currently a no-op. |

Requires `VOYAGE_API_KEY` in the environment. The command exits non-zero
if the key is missing or the path is not a regular file.

### Example

```
export VOYAGE_API_KEY=pa-...
redrobin kb add ~/vault/Techniques/mimikatz.md --tag mimikatz --tag credential-access
# [*] KB indexing /home/operator/vault/Techniques/mimikatz.md (tags=['mimikatz', 'credential-access'])
# [+] Indexed 4 chunk(s) into /home/operator/.redrobin/shared/kb-index/index.npz
```

### What each chunk's metadata records

Every chunk stored has the following metadata (now JSON-serialised on
disk, so downstream filtering on tags etc. is safe):

| Field | Type | Source |
| --- | --- | --- |
| `source` | string | absolute path of the ingested file |
| `doc_id` | string | first 16 hex chars of SHA-256(absolute path) — stable per file |
| `chunk_index` | int | 0-based position of the chunk within the file |
| `tags` | list of strings | the `--tag` flags passed to `kb add` |

## Tag conventions (suggested)

Tags are free-form, but for retrieval ergonomics consistency matters more
than completeness. Suggested namespaces (mirroring the operator vault):

- **Phase**: `phase/recon`, `phase/initial-access`, `phase/privesc`,
  `phase/lateral-movement`, `phase/credential-access`, `phase/exfil`,
  `phase/c2`, `phase/persistence`.
- **Tool**: `tool/cobalt-strike`, `tool/sliver`, `tool/bloodhound`,
  `tool/rubeus`, `tool/mimikatz`.
- **MITRE**: ATT&CK technique IDs (`mitre/T1558`, `mitre/T1003.001`).
- **OS**: `os/windows`, `os/linux`, `os/macos`.
- **Source**: `source/vault`, `source/blog`, `source/internal-report`.

You don't need every tag on every chunk — pick the one or two that the
chunk is genuinely *about*.

## Files

| Path | Purpose |
| --- | --- |
| `~/.redrobin/shared/kb-index/index.npz` | The single-file vector index. Numpy-backed cosine-similarity store; safe to back up by copying this file. |
| `~/.redrobin/transport.token` | Daemon bearer token; `kb add` does not need it directly but other subcommands will once they ship. |

The KB lives under `shared/` (not under any individual `engagements/<slug>/`)
so notes you ingested for last quarter's engagement are still retrievable
when you start a new one. If you ever want a clean slate, delete the
`kb-index/` directory and re-run `kb add` against your sources.

## v0.1.x roadmap

The following subcommands are planned but not yet implemented. The shape
is sketched here so operators know what's coming; the surface may change
slightly before v0.1.x ships.

### `redrobin kb list` (planned)

List every doc indexed in the KB with chunk counts and tag summaries.

### `redrobin kb remove` (planned)

```
redrobin kb remove --source PATH      # remove every chunk from a given source
redrobin kb remove --doc-id ID        # remove by stable doc_id
redrobin kb remove --tag TAG          # remove everything carrying a tag
```

### `redrobin kb query` (planned)

```
redrobin kb query "how do I dump LSASS without touching disk?" [--top-k 5]
```

A debug-only retrieval probe — prints the same top-`k` chunks the assistant
would see for a given prompt, useful when tuning your tags or sanity-
checking what got indexed.
