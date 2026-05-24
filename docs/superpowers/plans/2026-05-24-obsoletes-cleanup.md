# Plan F — Round 4 obsoletes cleanup (closes #110 + #112)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development with Opus 4.7. Atomic commits per task. This is a hybrid plan+execution: small scope, no production code touched.

**Goal:** Close two obsolete backlog items in the v0.1.x sprint. `#110` (re-enable heartbeat fork via `lambda()` closure capture) is SUPERSEDED by the established `on heartbeat_5s` pattern — its empirical disproof during T14 smoke 2026-05-23 means the fork-with-lambda path will never be re-enabled. `#112` (reconcile CS-version-to-Java-version claim across 3 sources) requires a verification pass against the 3 named sources to confirm whether a conflict remains. Pure backlog-hygiene plan; **no production code paths are touched**. If `#112` verification surfaces a genuine 3-source conflict, light docs reconciliation is in scope (T2 expands); if the 3 sources already agree, T2 collapses to a backlog close-out.

**Architecture / approach:** Plan F is a backlog-hygiene plan. Two scopes:
- **T1 (`#110`):** zero file edits other than `docs/superpowers/backlog.md`. The empirical disproof (Sleep `fork(lambda(...))` does NOT propagate captured globals into the spawned ScriptInstance) is already documented inline at `adapters/cobaltstrike/redrobin.cna:434-458` (disabled fork block) and at `:461-486` (the established `on heartbeat_5s` pattern that replaces it). The backlog edit records the closure.
- **T2 (`#112`):** verification-first. Read the three cited sources and triangulate whether the CS-version-to-Java-version claims agree. If yes → backlog close-out only (mark VERIFIED-CONSISTENT). If no → docs reconciliation against the project's empirical evidence (`pom.xml`-free stock-JDK build with `--release 11`, Fortra's published help-java-dependency page that operator-setup.md already cites) + backlog close-out.

**Tech Stack:** Markdown only. No Sleep, no Python, no Java, no JAR rebuild, no daemon restart, no schema, no tests touched.

**Verified facts driving this plan (every claim read directly from the dev-juan tree at HEAD `b1846e1` on 2026-05-24):**

1. **`#110`'s empirical disproof is already inline in `redrobin.cna`.** The disabled fork block at `adapters/cobaltstrike/redrobin.cna:434-458` (header "Heartbeat fork — DISABLED for v0.1") explains the operational impact ("zero — every `redrobin_http_post` call already catches network errors and emits 'daemon connection refused at ...' warn lines. The operator sees daemon outages at the moment they trigger an alias — not 30s later via a proactive heartbeat. Adequate for v0.1."). The block contains a commented-out `fork({ while (1) { sleep(30000); redrobin_heartbeat(); } });` skeleton on lines 453-458.

2. **The established replacement pattern is `on heartbeat_5s` at `redrobin.cna:475-486`** (the Layer B verdict consumer). Its inline comment at lines 468-473 explicitly documents the empirical finding: *"Why heartbeat_5s and not fork(): empirical testing 2026-05-23 (T14 smoke) proved that `fork() + lambda($_x => $GLOBAL)` does NOT propagate `$GLOBAL` into the spawned ScriptInstance — the bound `$_x` is null in the fork's scope. Same scope-isolation root cause as Task #110. heartbeat_5s runs on the main thread, so `$REDROBIN_EVENT_STREAM` is directly visible."*

3. **The commit that disabled the heartbeat fork is `b7c7c69`** (`fix(cna): drop fork() inside redrobin_push_event_async (closes #134)`). Confirmed via `git show b7c7c69 --stat`: 1 file changed, 20+/-8 in `adapters/cobaltstrike/redrobin.cna`. Its commit message explicitly references `#110` as a sibling resolution: *"The companion #110 (re-enable heartbeat fork) remains open but its resolution path is now empirically clear: same lesson — use heartbeat_Xs hooks, not fork(), for any Sleep code that needs access to globals or persistent Java references."* This is the close-out evidence for `#110`.

4. **The feedback memory documenting the Sleep fork async pattern exists at `C:/Users/jcspi/.claude/projects/C--Users-jcspi-Documents-spiedra-vault/memory/feedback_sleep_fork_async_pattern.md`** and is referenced in the user's vault auto-memory (see "User Preferences" in MEMORY.md: *"[Sleep fork async pattern](feedback_sleep_fork_async_pattern.md) — use `on heartbeat_Xs` not `fork(lambda(...))` for .cna async polling against globals/Java refs (fork() scope is fully isolated; lambda capture empirically doesn't propagate)"*). T1's close-out body references this memory by relative slug.

5. **`#112`'s three named sources are: `docs/operator-setup.md`, the JAR helper spec, and the original audit doc (interpreted as the legacy-revert design spec)**. Verified content of each at HEAD `b1846e1`:
   - **`docs/operator-setup.md:203-206`:** *"Red Robin's Aggressor script (`redrobin.cna`) requires **Java 11+** on the CS Client side. CS 4.12 bundles OpenJDK 17 in its standard launcher per [Fortra Java dependency docs](https://www.cobaltstrike.com/help-java-dependency), so operators on stock CS launches are automatically on a supported JVM."*
   - **`adapters/cobaltstrike/helper-jar/README.md` (entire file, 67 lines):** makes NO CS-version-to-Java-version claim. The only Java reference is `:40` *"Requires JDK 11+ on `PATH`."* (build prerequisite, not a CS-bundle claim).
   - **`docs/superpowers/specs/2026-05-17-cna-jar-helper-design.md:568-569` (the JAR helper spec, §7 Java compatibility):** *"- **Floor:** Java 11 (HttpClient API minimum). - **Tested:** Java 17 (CS 4.12 bundled JVM, per Fortra docs)."*
   - **`docs/superpowers/specs/2026-05-17-cna-http-legacy-revert-design.md:44` (the legacy-revert design spec, source of the audit table):** *"CS 4.12 officially supports OpenJDK 17 (only) | [Cobalt Strike Java dependency docs](https://www.cobaltstrike.com/help-java-dependency)"*

6. **The three sources are already consistent on the CS 4.12 → Java 17 mapping.** Triangulation:
   - operator-setup.md: CS 4.12 → OpenJDK 17 (single version, stock launcher).
   - JAR helper spec §7: CS 4.12 bundled JVM = Java 17 (tested platform).
   - Legacy-revert spec §1.3: CS 4.12 officially supports OpenJDK 17 (only).
   All three cite the same Fortra source (`cobaltstrike.com/help-java-dependency`). All three name **Java 17** (not 11, not "17+", not 21) as the CS 4.12 bundle. The minor textual inconsistency in `redrobin.cna:31` (*"CS 4.12 ships with Java 17+"* — note the `+`) is in a code comment, NOT one of the three cited sources, and is forward-tolerant rather than incorrect (an operator could swap the bundled JVM for a newer one). **No reconciliation required for the three cited sources.**

7. **Build target verification:** `adapters/cobaltstrike/helper-jar/build.ps1:13` and `build.sh:12` both set `VERSION="1.0.2"` and the compile step uses `javac --release 11` (per `build.ps1:30-33` and `build.sh:14-16`). This empirically confirms the **Java 11 floor**: every cited source agrees Red Robin's floor is Java 11 (matching `redrobin.cna:48` `if ($major < 11)` guard). The CS 4.12 bundled-JVM claim (Java 17) is the *tested* platform, not the floor — and is also consistent across all three sources.

**Conclusion on `#112`:** **VERIFIED-CONSISTENT.** All three cited sources name CS 4.12 → OpenJDK/Java 17, all cite the same Fortra primary source, and the project's Java 11+ floor is consistently stated as the build/runtime requirement. The backlog entry's "slightly different mappings" framing was incorrect at the time it was filed OR has since been resolved by the 2026-05-24 docs sprint (Plan C touched `operator-setup.md` for the 1.0.1 → 1.0.2 JAR filename bump and helper-jar/README.md for the 1.0.2 shipped classes — both touched files were verified for the version mapping during that pass). T2 is **backlog close-out only**, no docs edits.

**Risk note:** Minimal — read-only verification + two backlog-row edits + one scope-table row flip. No production code paths. No JAR rebuild. No daemon restart. No schema. No tests touched. Risk surface is exclusively in the prose of `docs/superpowers/backlog.md` — every fact cited has a file:line backing in this plan's "Verified facts" section. If a future operator disputes the VERIFIED-CONSISTENT conclusion of `#112` they can reopen with new evidence; the close-out body in T2 captures the triangulation source set.

**Starting state assumption:** `dev-juan` HEAD = `b1846e1` (`docs(smoke): add Layer B prompt-tuning smoke section T3-S1..S5 (#129)`). Working tree has a few untracked operator-local files (`TESTING.md`, `redrobin_postcommit.tmp.cna`, `TEMPinspect_*.txt`) that are NOT staged and NOT touched by this plan. The plan adds one new file (this plan itself) and modifies one existing file (`docs/superpowers/backlog.md`).

---

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `docs/superpowers/plans/2026-05-24-obsoletes-cleanup.md` | Create | This plan (committed first as a standalone atomic commit) |
| `docs/superpowers/backlog.md` | Modify (T1) | Close out `#110` entry — flip heading + body to RESOLVED with reference to `b7c7c69` and the `on heartbeat_5s` pattern |
| `docs/superpowers/backlog.md` | Modify (T2) | Close out `#112` entry — flip heading + body to VERIFIED-CONSISTENT with the 3-source triangulation |
| `docs/superpowers/backlog.md` | Modify (T3) | Flip Plan F scope-table row → DONE with Task 1 + Task 2 commit SHAs |

---

## Task 0: Commit this plan

**Files:**
- Create: `docs/superpowers/plans/2026-05-24-obsoletes-cleanup.md`

This is the plan-file commit. It must land BEFORE T1 so the close-out commits can reference the plan by its committed path.

- [ ] **Step 1: Write the plan**

Already done by the time T0 runs (this file IS the plan). The implementer should verify the file is present at `docs/superpowers/plans/2026-05-24-obsoletes-cleanup.md` and that its content matches this draft.

- [ ] **Step 2: Commit (atomic — single commit for the plan)**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git add docs/superpowers/plans/2026-05-24-obsoletes-cleanup.md
git commit -m "$(cat <<'EOF'
docs(plans): Plan F -- Round 4 obsoletes cleanup (#110 + #112)

Plan F is the final Round 1-4 sweep entry in the v0.1.x sprint. Two
backlog items closed:

  #110 -- re-enable heartbeat fork via lambda() closure capture.
  SUPERSEDED. The empirical disproof from T14 smoke 2026-05-23 (Sleep
  fork(lambda(...)) does NOT propagate captured globals into the
  spawned ScriptInstance) is already documented inline at
  redrobin.cna:434-458 (disabled fork block) and the proper
  replacement pattern (on heartbeat_5s, main-thread, full scope) is
  established at redrobin.cna:475-486 (Layer B verdict drain).
  Heartbeat fork was disabled in b7c7c69 and never needs re-enabling.
  T1: backlog edit only.

  #112 -- reconcile CS-version-to-Java-version claim across 3
  sources. VERIFIED-CONSISTENT. Triangulation of the three named
  sources (docs/operator-setup.md, JAR helper README, JAR helper
  spec) confirms all agree: CS 4.12 -> OpenJDK 17, Java 11+ floor.
  All three cite Fortra's help-java-dependency page. README does not
  make a CS-version claim at all (only states the JDK 11+ build
  prerequisite). No docs reconciliation needed. T2: backlog edit
  only.

No production code touched. ~3-4 atomic commits in this dispatch
(plan + T1 close-out + T2 close-out + T3 scope-table flip; T2 and T3
may combine if they touch the same backlog.md region atomically).

Refs: docs/superpowers/backlog.md scope-table row F.
EOF
)"
```

**Do NOT use `--amend` or `--no-verify`.** If a pre-commit hook fails, fix the issue and create a NEW commit.

---

## Task 1: Close `#110` (SUPERSEDED) in the backlog

**Files:**
- Modify: `docs/superpowers/backlog.md`

- [ ] **Step 1: Capture Task 0's commit SHA**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git log -1 --format=%H docs/superpowers/plans/2026-05-24-obsoletes-cleanup.md
```

Call this `<T0 SHA>`. It is not strictly needed in T1's commit message body — the close-out body cites `b7c7c69` (the heartbeat-fork-disabling commit) instead — but the implementer should have it on hand for T3's scope-table row.

- [ ] **Step 2: Update the `#110` entry heading and body**

In `docs/superpowers/backlog.md`, find the existing entry (currently at lines 93-96):

```markdown
### #110 — re-enable heartbeat fork (SUPERSEDED — to be closed in Plan F)
**Source:** Pre-existing — filed during T11 when fork was disabled.
**Status:** Empirically disproven 2026-05-23 during T14 smoke (#138 work) — Sleep `fork(lambda(...))` does NOT propagate captured globals into the spawned ScriptInstance. The proper pattern is `on heartbeat_5s` which runs on the main thread with full scope access. This is now the established pattern in `redrobin.cna` (Layer B verdict drain). Heartbeat fork was DISABLED in `b7c7c69` and never needs to be re-enabled — its purpose (daemon-liveness probe every 30s) is adequately covered by `redrobin_http_post` warn-on-failure inline error reporting.
**Action in Plan F:** Mark CLOSED in task tracker with reference to the superseding pattern.
```

Replace with:

```markdown
### #110 — re-enable heartbeat fork via lambda() closure capture (RESOLVED 2026-05-24 commit `<T1 SHA>`)
Plan F T1 closed as SUPERSEDED. Empirical evidence from T14 smoke 2026-05-23 (documented in operator's vault memory `feedback_sleep_fork_async_pattern.md`): Sleep `fork(lambda(...))` does NOT propagate captured globals into the spawned ScriptInstance — the bound symbol is null inside the fork's scope. The proper pattern is `on heartbeat_5s` (main script thread, full global scope access), now established at `adapters/cobaltstrike/redrobin.cna:475-486` as the Layer B verdict drain. The disabled fork block at `redrobin.cna:434-458` retains an inline rationale for future operators. Heartbeat fork was disabled in commit `b7c7c69` (the `#134` fix — which references `#110` directly in its commit message: *"its resolution path is now empirically clear: same lesson — use heartbeat_Xs hooks, not fork(), for any Sleep code that needs access to globals or persistent Java references"*) and never needs to be re-enabled — its original purpose (daemon-liveness probe every 30s) is adequately covered by `redrobin_http_post`'s inline warn-on-failure error reporting (`redrobin.cna:88-91`).
```

The `<T1 SHA>` placeholder is replaced AFTER Step 3's commit lands; see Step 4 for the corrective pass.

- [ ] **Step 3: Commit (atomic — single commit for the `#110` close-out)**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git add docs/superpowers/backlog.md
git commit -m "$(cat <<'EOF'
docs(backlog): mark #110 RESOLVED -- Plan F T1 close-out

Plan F T1 closed as SUPERSEDED. The empirical disproof from T14 smoke
2026-05-23 (Sleep fork(lambda(...)) does NOT propagate captured
globals into the spawned ScriptInstance) is already documented inline
at redrobin.cna:434-458 (disabled fork block) and the established
replacement pattern (on heartbeat_5s, main-thread, full scope) is in
redrobin.cna:475-486 (Layer B verdict drain).

Heartbeat fork was disabled in b7c7c69 (the #134 fix). Its commit
message references #110 directly: "its resolution path is now
empirically clear: same lesson -- use heartbeat_Xs hooks, not fork(),
for any Sleep code that needs access to globals or persistent Java
references."

The original purpose of the heartbeat fork (daemon-liveness probe
every 30s) is adequately covered by redrobin_http_post's inline
warn-on-failure error reporting at redrobin.cna:88-91. No
re-introduction needed for v0.1 or beyond. Tracker mention in
backlog scope-table row F flips to DONE in T3.

Refs: feedback_sleep_fork_async_pattern.md (vault operator memory),
redrobin.cna:434-458 (disabled fork block), redrobin.cna:475-486
(established heartbeat_5s pattern), commit b7c7c69.
EOF
)"
```

**Do NOT use `--amend` or `--no-verify`.**

- [ ] **Step 4: Self-correct the `<T1 SHA>` placeholder**

The close-out body embeds the T1 commit SHA. Because the commit can't reference its own SHA pre-commit, the placeholder `<T1 SHA>` is replaced in a SECOND commit:

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
T1_SHA=$(git log -1 --format=%H docs/superpowers/backlog.md)
# Replace <T1 SHA> in the heading with the actual SHA
# Use the implementer's preferred text-editing tool (Edit/sed) for atomicity
```

Use the Edit tool to replace:
```
### #110 — re-enable heartbeat fork via lambda() closure capture (RESOLVED 2026-05-24 commit `<T1 SHA>`)
```
with:
```
### #110 — re-enable heartbeat fork via lambda() closure capture (RESOLVED 2026-05-24 commit `<actual T1 SHA>`)
```

Then commit:

```bash
git add docs/superpowers/backlog.md
git commit -m "docs(backlog): embed T1 commit SHA in #110 close-out heading"
```

**Atomicity note:** Plan A/B/C/D/E close-outs accepted SHA embedding via this two-commit pattern OR via a single combined commit if the implementer prefers to commit T1+T2 together after both heading SHAs are known. **Either pattern is acceptable** — pick the one that produces clean atomic history. If combining, the commit message header is `docs(backlog): close #110 + #112 -- Plan F T1+T2 close-out` and the body describes both.

---

## Task 2: Verify `#112` and close as VERIFIED-CONSISTENT

**Files:**
- Read-only: `docs/operator-setup.md`, `adapters/cobaltstrike/helper-jar/README.md`, `docs/superpowers/specs/2026-05-17-cna-jar-helper-design.md`
- Modify: `docs/superpowers/backlog.md`

- [ ] **Step 1: Re-verify the three sources** (defensive — the implementer should independently triangulate, not trust this plan's "Verified facts" section)

Read the three named sources at HEAD `b1846e1`:

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
# Source 1: operator-setup.md — section "Cobalt Strike Client Java requirement"
grep -n "Java 11\|Java 17\|OpenJDK 17\|CS 4.12" docs/operator-setup.md

# Source 2: JAR helper README — entire file
cat adapters/cobaltstrike/helper-jar/README.md

# Source 3: JAR helper spec — §7 Java compatibility
grep -n "Java 11\|Java 17\|OpenJDK\|CS 4.12" docs/superpowers/specs/2026-05-17-cna-jar-helper-design.md
```

Expected findings (from this plan's Verified Fact 5):
- **Source 1 (operator-setup.md:203-206):** Java 11+ floor; CS 4.12 bundles OpenJDK 17 per Fortra.
- **Source 2 (helper-jar/README.md:40):** "Requires JDK 11+ on `PATH`" — build prerequisite only, no CS-bundle claim.
- **Source 3 (jar-helper-design.md:568-569):** Floor Java 11 (HttpClient API minimum); Tested Java 17 (CS 4.12 bundled JVM, per Fortra docs).

Also re-verify the build target evidence:

```bash
grep -n "\-\-release" adapters/cobaltstrike/helper-jar/build.ps1 adapters/cobaltstrike/helper-jar/build.sh
```

Expected: `--release 11` in both scripts (one occurrence each in the compile step) — confirming Java 11 floor is also encoded in the actual build pipeline.

- [ ] **Step 2: Triangulate the three claims**

Verdict matrix (the implementer should fill this in independently from Step 1's output):

| Source | Java floor claim | CS 4.12 bundle claim | Citation |
|---|---|---|---|
| operator-setup.md | Java 11+ | OpenJDK 17 | Fortra help-java-dependency |
| helper-jar/README.md | JDK 11+ (build only) | (no claim) | n/a |
| jar-helper-design.md §7 | Java 11 | Java 17 | Fortra docs |

If all three rows are consistent (floor = 11, CS 4.12 bundle = 17, README simply does not make the CS-bundle claim), **T2 collapses to backlog close-out only** (Step 3a). If any row dissents, T2 expands to docs reconciliation (Step 3b).

- [ ] **Step 3a: Backlog close-out (VERIFIED-CONSISTENT path)**

If Step 2 found all three sources consistent (the expected outcome per Verified Fact 6), update `docs/superpowers/backlog.md` `#112` entry (currently at lines 98-102):

Find:

```markdown
### #112 — reconcile CS-version-to-Java-version claim across 3 sources (VERIFY in Plan F)
**Source:** Pre-existing.
**Symptom:** Three sources claim slightly different mappings between CS version and bundled Java version (operator-setup.md, the JAR helper spec, and the original audit doc). Inconsistency confuses operators choosing their CS bundle.
**Files:** `docs/operator-setup.md`, possibly the JAR helper README + spec.
**Action in Plan F:** Verify against Fortra's actual CS release notes for v4.12, pick the correct claim, propagate to all 3 sources. If already reconciled, mark CLOSED.
```

Replace with:

```markdown
### #112 — reconcile CS-version-to-Java-version claim across 3 sources (RESOLVED 2026-05-24 commit `<T2 SHA>`)
Plan F T2 VERIFIED-CONSISTENT. Triangulation of the three named sources at HEAD `b1846e1`: (1) `docs/operator-setup.md:203-206` — Java 11+ floor; CS 4.12 bundles OpenJDK 17 per [Fortra Java dependency docs](https://www.cobaltstrike.com/help-java-dependency). (2) `adapters/cobaltstrike/helper-jar/README.md:40` — "Requires JDK 11+ on `PATH`" as a build prerequisite only; makes NO CS-version-to-Java-version claim. (3) `docs/superpowers/specs/2026-05-17-cna-jar-helper-design.md:568-569` (§7 Java compatibility) — Floor Java 11 (HttpClient API minimum); Tested Java 17 (CS 4.12 bundled JVM, per Fortra docs). All three agree: floor is Java 11, CS 4.12 ships OpenJDK 17. Same Fortra primary source cited by sources 1 and 3. The build pipeline (`adapters/cobaltstrike/helper-jar/build.ps1:30-33` + `build.sh:14-16`) uses `javac --release 11` confirming the Java 11 floor is also encoded in the actual JAR build. Backlog entry's "slightly different mappings" framing was either incorrect at the time it was filed (no specific conflict was identified in the original ticket beyond a generic concern) OR was resolved by the 2026-05-24 docs sprint that touched `operator-setup.md` (Plan C T1, JAR filename 1.0.1 → 1.0.2 bump) and `helper-jar/README.md` (Plan C T2, 1.0.2 shipped-classes rewrite) — both touched files were independently verified for the version mapping during the present (Plan F) pass. **No docs edits required.**
```

- [ ] **Step 3b: Reconciliation (alternate path, only if Step 2 found conflicts)**

If Step 2 surfaced any genuine dissent between the three sources, this step expands to a docs reconciliation:

1. Choose the canonical claim: **CS 4.12 → OpenJDK 17, Java 11+ floor** (the empirically-supported value per Fortra's help-java-dependency page + the project's `--release 11` build target).
2. Propagate the canonical claim to the dissenting source(s) via `Edit` tool replacements.
3. Stage + commit the docs edits separately as `docs(operator-setup):` or `docs(helper-jar-readme):` or `docs(specs):` per scope, **before** the backlog close-out.
4. Then proceed to Step 3a's backlog close-out with the entry body updated to say "RECONCILED 2026-05-24" instead of "VERIFIED-CONSISTENT", listing the corrective commit SHAs.

Since the implementer's actual Step 1 output is expected to confirm the consistent-no-conflict outcome (per Verified Facts 5 + 6 of this plan), Step 3b should NOT trigger. If it does, the implementer should pause and reconcile the dissenting source(s) before continuing.

- [ ] **Step 4: Commit the T2 close-out (VERIFIED-CONSISTENT path)**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git add docs/superpowers/backlog.md
git commit -m "$(cat <<'EOF'
docs(backlog): mark #112 VERIFIED-CONSISTENT -- Plan F T2 close-out

Plan F T2 closed as VERIFIED-CONSISTENT. Triangulation of the three
sources cited by #112:

  1. docs/operator-setup.md:203-206 -- Java 11+ floor; CS 4.12 bundles
     OpenJDK 17 per Fortra help-java-dependency.
  2. adapters/cobaltstrike/helper-jar/README.md:40 -- "Requires JDK
     11+ on PATH" (build prereq only; no CS-bundle claim).
  3. docs/superpowers/specs/2026-05-17-cna-jar-helper-design.md:568-569
     (Section 7) -- Floor Java 11 (HttpClient API minimum); Tested
     Java 17 (CS 4.12 bundled JVM, per Fortra docs).

All three agree: floor = Java 11, CS 4.12 bundle = OpenJDK 17. The
build pipeline (build.ps1 + build.sh) uses `javac --release 11` which
confirms the Java 11 floor is encoded in the actual JAR build. The
backlog entry's "slightly different mappings" framing was either
incorrect at the time of filing or resolved by the 2026-05-24 docs
sprint (Plan C T1 + T2). No docs edits required.

Refs: docs/superpowers/plans/2026-05-24-obsoletes-cleanup.md
(triangulation evidence + verified facts).
EOF
)"
```

**Do NOT use `--amend` or `--no-verify`.**

- [ ] **Step 5: Self-correct the `<T2 SHA>` placeholder**

Same pattern as T1 Step 4. After T2's commit lands, capture its SHA and replace `<T2 SHA>` in the close-out heading with a second commit.

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
T2_SHA=$(git log -1 --format=%H docs/superpowers/backlog.md)
# Use Edit tool to replace <T2 SHA> with the actual SHA
git add docs/superpowers/backlog.md
git commit -m "docs(backlog): embed T2 commit SHA in #112 close-out heading"
```

**Atomicity alternative:** if the implementer combines T1+T2's close-outs into a single commit (acceptable per T1 Step 4's note), then this self-correct step combines too — one combined SHA-embedding commit covers both `#110` and `#112` headings.

---

## Task 3: Scope-table row F → DONE

**Files:**
- Modify: `docs/superpowers/backlog.md`

- [ ] **Step 1: Capture T1's and T2's commit SHAs**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
# Last commit touching the #110 region:
git log -1 --format=%H -- docs/superpowers/backlog.md
# (operator can inspect git log -5 docs/superpowers/backlog.md to identify which is T1 vs T2 if needed)
```

The implementer should know which SHAs are the T1 close-out and T2 close-out from their session history.

- [ ] **Step 2: Update the scope-table row for Plan F**

In `docs/superpowers/backlog.md` find the row (at line 20):

```
| F | 4 — close obsoletes | `#110`, `#112` | verify + close, ~15 min | Pending |
```

Replace `Pending` with:

```
**DONE 2026-05-24** (commits `<T1 SHA>` + `<T2 SHA>`)
```

If T1+T2 were combined into a single commit, the row reads `(commit <combined SHA>)` instead.

- [ ] **Step 3: Commit T3 (atomic, may be combined with T2's close-out if scoped together)**

Two acceptable patterns:

**Pattern A (T3 is its own commit):**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git add docs/superpowers/backlog.md
git commit -m "$(cat <<'EOF'
docs(backlog): flip Plan F scope-table row to DONE

Round 4 obsoletes cleanup complete. Plan F closed two backlog items:

  #110 RESOLVED <T1 SHA> -- SUPERSEDED by on heartbeat_5s pattern
  #112 RESOLVED <T2 SHA> -- VERIFIED-CONSISTENT across 3 sources

Scope-table row F: Pending -> DONE 2026-05-24. The v0.1.x Round 1-4
sweep is complete (Plans A through F). Rounds 5-7 (refactors, v0.2,
infrastructure) remain deferred to post-demo.

Refs: docs/superpowers/plans/2026-05-24-obsoletes-cleanup.md
EOF
)"
```

**Pattern B (T2 + T3 combined into one commit):**

The implementer flips the scope-table row in the same edit pass as T2's `#112` body update and self-corrects both `<T2 SHA>` and the scope-table row SHA references in a single follow-on commit. This is acceptable because both changes touch only `docs/superpowers/backlog.md` and the logical change ("close `#112` and mark Plan F done") is one atomic story.

If Pattern B is used, T2's commit message should mention the scope-table flip:

```
docs(backlog): close #112 -- VERIFIED-CONSISTENT + flip Plan F scope-table row to DONE
```

**Do NOT use `--amend` or `--no-verify`.**

---

## Verification before reporting

- [ ] **Final pass: `git log` should show ~3-5 commits for Plan F, all atomic, no `--amend`, no `--no-verify`.**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git log --oneline -10
```

Expected progression (count depends on combined-vs-separate pattern choices):

```
<T3 SHA>      docs(backlog): flip Plan F scope-table row to DONE          (T3, optional separate)
<T2-SHA SHA>  docs(backlog): embed T2 commit SHA in #112 close-out heading (T2 self-correct)
<T2 SHA>      docs(backlog): mark #112 VERIFIED-CONSISTENT -- Plan F T2 close-out
<T1-SHA SHA>  docs(backlog): embed T1 commit SHA in #110 close-out heading (T1 self-correct)
<T1 SHA>      docs(backlog): mark #110 RESOLVED -- Plan F T1 close-out
<T0 SHA>      docs(plans): Plan F -- Round 4 obsoletes cleanup (#110 + #112)
b1846e1       docs(smoke): add Layer B prompt-tuning smoke section T3-S1..S5 (#129)  [BASE]
```

Minimum 3 commits (T0, T1+self-correct combined, T2+T3+self-correct combined). Maximum 6 commits (T0, T1, T1 self-correct, T2, T2 self-correct, T3 separate). All acceptable so long as each commit is one logical change.

- [ ] **Final pass: backlog scope-table row F shows DONE with at least one commit SHA.**

- [ ] **Final pass: `#110` and `#112` entry headings both contain `RESOLVED 2026-05-24 commit <SHA>`** (or `VERIFIED-CONSISTENT 2026-05-24 commit <SHA>` for `#112` — both phrasings are acceptable per Plan A/B/C/D/E precedent).

- [ ] **Final pass: NO production code paths touched.**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git diff b1846e1..HEAD --stat | grep -v '\.md'
```

Expected: empty output. The only modified path family should be `*.md`.

---

## Report shape

After all tasks land, the implementer reports:

- Plan path: `docs/superpowers/plans/2026-05-24-obsoletes-cleanup.md`
- T0 (plan) commit SHA
- T1 (#110 close-out) commit SHA + (if separate) T1 self-correct SHA
- T2 (#112 close-out) commit SHA + (if separate) T2 self-correct SHA
- T3 (scope-table flip) commit SHA — may be combined with T2
- Total commits in this dispatch
- ANY DEVIATIONS (e.g., if Step 3b of T2 fired — meaning a docs reconciliation was needed — note the file:line patches and the additional `docs(operator-setup):` or `docs(helper-jar-readme):` commits)

Terse — bullets, headers only.
