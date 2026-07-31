# Knowledge base

The knowledge base is a single per-operator vector index the assistant retrieves
from — via its `kb_retrieve` tool — when it reasons about what you just ran or
answers a question in the chat. It is content *you* curate: vault notes, prior
engagement reports, exploit write-ups, MITRE technique pages. Anything an LLM
would do better with at hand.

It lives under `~/.redrobin/shared/`, not under any engagement, so notes you
ingested last quarter are still retrievable on a new op.

`redrobin kb add` is the only subcommand wired end to end today.

## `redrobin kb add`

Reads a markdown file, chunks it, embeds each chunk via Voyage, and upserts the
chunks into the index.

```
redrobin kb add <path> [--tag TAG ...] [--chunk-size N] [--chunk-overlap N]
```

| Flag | Default | Purpose |
| --- | --- | --- |
| `<path>` | required | A single markdown file. Directory ingest is not implemented. |
| `--tag TAG` | none | Repeatable free-form metadata tag on every chunk from this file. |
| `--chunk-size` | `1200` | Max characters per chunk. |
| `--chunk-overlap` | `200` | Overlap between consecutive chunks, so cross-chunk context survives. |

Requires `VOYAGE_API_KEY`. Exits non-zero if the key is missing or the path is
not a regular file.

```
$ redrobin kb add ~/vault/Techniques/mimikatz.md --tag mimikatz --tag credential-access
[*] KB indexing /home/operator/vault/Techniques/mimikatz.md (tags=['mimikatz', 'credential-access'])
[+] Indexed 4 chunk(s) into /home/operator/.redrobin/shared/kb-index/index.npz
```

Each chunk records `source` (absolute path), `doc_id` (first 16 hex of
SHA-256 of that path, so it is stable per file), `chunk_index`, and `tags`.

## Tagging

Tags are free-form, and consistency matters more than completeness. A convention
that mirrors most operator vaults:

- **Phase** — `phase/recon`, `phase/privesc`, `phase/lateral-movement`,
  `phase/credential-access`, `phase/persistence`, `phase/exfil`
- **Tool** — `tool/cobalt-strike`, `tool/bloodhound`, `tool/rubeus`
- **MITRE** — `mitre/T1558`, `mitre/T1003.001`
- **OS** — `os/windows`, `os/linux`
- **Source** — `source/vault`, `source/blog`, `source/internal-report`

Pick the one or two the chunk is genuinely *about*. You do not need every
namespace on every file.

## The index file

`~/.redrobin/shared/kb-index/index.npz` is the whole store — a numpy-backed
cosine-similarity index. Back it up by copying that file. For a clean slate,
delete the `kb-index/` directory and re-run `kb add` over your sources.
