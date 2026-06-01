# Sigma support (v0.1.x)

What works post-Slice-11, what is deferred to v0.2, and how Red Robin
handles rules that reference unsupported fields or modifiers.

Red Robin runs **Layer A** as a synthetic Sigma engine — a hand-written
subset of pySigma covering the fields and modifiers needed to gate Beacon
commands in <150ms. The full Sigma engine lands when CIDR / named
selections / condition expressions become a requirement; for v0.1.x we
keep the subset minimal and explicit about its limits.

## Supported fields

Sigma field names map to `EventContext` attributes via
`_SIGMA_FIELD_TO_PREDICATE` in `redrobin/infrastructure/rules_loader.py`:

| Sigma field        | Maps to            | Notes                                                                                |
| ------------------ | ------------------ | ------------------------------------------------------------------------------------ |
| `CommandLine`      | `args`             | Operator's full command line. `cmd` is an accepted alias.                            |
| `Image`            | `command`          | Process image path. Coerced to basename and stripped of `.exe`; operator falls back to `equals` on `command`. |
| `IntegrityLevel`   | `integrity_level`  | **Binary High / None signal in v0.1.x.** See below.                                  |
| `OriginalFileName` | `original_filename`| Process original filename (resists renamed binaries).                                |
| `User`             | `user`             | Process user (e.g. `CORP\admin`).                                                    |
| `CurrentDirectory` | `current_directory`| CWD of the process.                                                                  |
| `Description`      | `description`      | Process file description.                                                            |

### `IntegrityLevel` — binary High / None in v0.1.x

The CS adapter's `.cna` derives integrity from `&binfo($bid, "user")` —
the same string CS displays in the beacon table, e.g. `CORP\\admin *`.
The trailing ` *` suffix indicates a high-integrity beacon. If present,
the adapter emits `"integrity_level": "High"`; otherwise it omits the
field entirely.

`&binfo("integrity")` is **not** a documented Aggressor key (audit pass 2
#2.4 corrected an earlier fabrication that mapped int `0..3` →
`Low/Medium/High/System`). Distinguishing `Medium / Low / System`
requires a beacon-side BOF — deferred to v0.2.

Implications for rule authors:

- `IntegrityLevel: High` works as expected.
- `IntegrityLevel: Medium`, `IntegrityLevel: Low`, `IntegrityLevel: System`
  **parse fine but never match** in v0.1.x. The selection clause evaluates
  to `False` because the adapter never emits those string values.
- If a future CS release drops the ` *` suffix, the integrity check
  returns `null` and even `IntegrityLevel: High` stops firing (safe-fail
  — no false BLOCKs).

## Supported modifiers

| Modifier              | Operator                                                                 | Case behavior                            |
| --------------------- | ------------------------------------------------------------------------ | ---------------------------------------- |
| `\|contains`          | substring on field value                                                 | **case-INSENSITIVE** per Sigma spec       |
| `\|startswith`        | prefix on field value                                                    | **case-INSENSITIVE** per Sigma spec       |
| `\|endswith`          | suffix on field value                                                    | **case-INSENSITIVE** per Sigma spec       |
| `\|re`                | `re.search` (substring; not anchored)                                    | **case-SENSITIVE** per Sigma `re` spec    |
| `\|base64offset\|contains` | three OR-combined `contains` predicates (per pySigma)               | **case-INSENSITIVE** (uses `contains`)    |

The case-default difference between string modifiers (insensitive) and
`|re` (sensitive) is an intentional contradiction in the **Sigma
specification itself**, not a Red Robin quirk. Authors of `|re` rules who
want case-insensitive matching must use the inline `(?i)` flag inside the
pattern.

### `|base64offset|contains` notes

Ported verbatim from pySigma's `SigmaBase64OffsetModifier.modify` (audit
pass 2 #E). Generates three OR-combined static-middle base64 variants per
byte-shift offset:

- `start_offsets = (0, 2, 3)`
- `end_offsets = (None, -3, -2)`
- Padding byte: **SPACE** `b" "` (not NUL — wrong padding silently
  mis-fires on real-world traffic).

Used to match base64-encoded malicious payloads embedded inside longer
encoded command lines (e.g. PowerShell `-EncodedCommand`).

## NOT supported (deferred to v0.2)

| Item                                            | Why deferred                                                                    |
| ----------------------------------------------- | ------------------------------------------------------------------------------- |
| `ParentImage`                                   | Aggressor `&binfo` does not canonically expose a parent process image. Requires async `ps` parsing + cache. |
| `\|cidr`                                        | No IPAddress field in `EventContext` for v0.1.x; lands when network-shaped events join the pipeline. |
| `\|wide`                                        | Wide-string handling is not relevant to the operator-typed verdict path.        |
| `\|fieldref`                                    | Cross-field reference needs a richer matcher; v0.2.                             |
| `\|re\|i` (case-insensitive regex sub-modifier) | Use `(?i)` inline in the pattern as a workaround.                               |
| `\|cased` (case-sensitive opt-in for strings)   | Not yet implemented in v0.1.x.                                                  |
| `IntegrityLevel: Medium / Low / System`         | Requires beacon-side BOF; see above.                                            |
| Named selections beyond `selection:`            | Only `selection:` is interpreted. `condition: selection` is the only supported condition. |
| `condition` expressions (`selection_1 and not filter`) | Same as above — single-selection only.                                    |

## Unsupported rule handling

Rules that reference an unsupported field or modifier **load
successfully** — they appear in `list_rules()` and survive hot-reload —
but their predicate is replaced with `_NeverMatchPredicate`. Such a rule
can never match anything, so a rule mistakenly using `ParentImage` will
not produce a verdict.

Operators see a WARN at daemon boot (and on every hot-reload):

```
WARN <path>: unsupported Sigma field 'ParentImage' — rule loaded but will never match
WARN <path>: unsupported Sigma modifier chain '|re|i' on field 'CommandLine' — rule loaded but will never match
```

This is deliberate (audit pass 2 #2.4 + #11): silently degrading match
behavior on the supported subset would surface as a **false-PASS verdict
on real-world tradecraft**, which is worse than the rule simply not
firing. The WARN tells you to switch to a supported expression.

## Example: real SigmaHQ rule

A rule using `CommandLine|contains` and `IntegrityLevel: High` — both
fully supported in v0.1.x:

```yaml
title: Mimikatz invoked at High integrity
id: aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee
status: experimental
description: Detect Mimikatz logonpasswords issued from a high-integrity beacon.
logsource:
  product: windows
  category: process_creation
detection:
  selection:
    IntegrityLevel: High
    CommandLine|contains: 'sekurlsa::logonpasswords'
  condition: selection
falsepositives:
  - Authorized credential audits (mark with a passthrough entry)
level: high
```

Drop this in `~/.redrobin/rules/shared/sigma/mimikatz-high.yaml`. The
daemon picks it up on hot-reload — no restart needed. If you used
`IntegrityLevel: Medium` instead, the rule would still load but the
selection would never match a real Beacon because the adapter never emits
`Medium` for `integrity_level`.

## Reference

- Loader: `redrobin/infrastructure/rules_loader.py` (`_parse_sigma_detection`).
- Predicate evaluation: `redrobin/domain/rules.py` (`_eval_match_predicate`).
- CS adapter integrity-derivation: `adapters/cobaltstrike/redrobin.cna`
  (`redrobin_integrity_level`).
- Tests: `tests/unit/test_rules_loader_sigma_*.py`.
