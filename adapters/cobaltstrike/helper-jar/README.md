# redrobin-aggressor-http

Java helper JAR loaded by `adapters/cobaltstrike/redrobin.cna` to wrap
`java.net.http.HttpClient`. Sleep cannot reflect on the JDK's internal
`jdk.internal.net.http.*` classes without `--add-opens` JVM flags, and
the prior approaches explored in the 2026-05-14 and 2026-05-17 design
specs all hit similar access-check failures (see spec §1.1 for the
full audit). This JAR sidesteps the issue by exposing a stable public
API in our own package (`com.redrobin.aggressor.*`) whose bytecode
invokes the internal classes directly — Sleep reflection only ever
targets our public surface.

See `docs/superpowers/specs/2026-05-17-cna-jar-helper-design.md` for
the full rationale.

## Sources in this tree

- `com.redrobin.aggressor.RedRobinHttp` — synchronous POST/GET against the
  daemon (handshake, capture events, heartbeat) + the schema-required
  ULID generator `ulid()` (Crockford b32; 48-bit ts + 80-bit randomness).

## Removed sources — the committed JAR is now AHEAD of this tree

`RedRobinEventStream` (WebSocket subscriber for the daemon's deleted
`/v1/adapters/events` endpoint) and `VerdictEvent` (its `opsec.verdict` DTO)
existed only to deliver pre-flight-gate verdicts. The gate was removed, so
their sources — and `RedRobinEventStreamTest` — were deleted.

**The prebuilt `../jars/redrobin-aggressor-http-1.0.2.jar` was deliberately
NOT rebuilt** (rebuilding a committed binary is separate, ticketed work). It
therefore still contains `RedRobinEventStream.class`, its `$Listener` inner
class, and `VerdictEvent.class`. Those classes are **inert** — nothing in
`redrobin.cna` loads them any more.

⚠️ **Whoever rebuilds this JAR must confirm `redrobin.cna` has no
`Class forName` call for the removed classes** (it does not today —
`redrobin_init_event_stream` was deleted in the same change). A rebuild that
drops the classes while the `.cna` still reflected on them would throw
`ClassNotFoundException` during boot, before the handshake, killing capture
entirely.

## Dependencies (vendored in `lib/`)

- `junit-platform-console-standalone-1.10.0.jar` — test-only (JUnit 5).
- `json-20240303.jar` — was runtime (org.json) for `RedRobinEventStream`'s
  frame decoding. With that class's source gone it has **no remaining source
  consumer**, but it is still vendored and still shipped sibling to the
  helper JAR at `adapters/cobaltstrike/jars/json-20240303.jar` because the
  un-rebuilt JAR's bytecode still references it. Retire it with the rebuild.

Provenance + SHA-256 for both: see `lib/README.md`.

## Build

Requires JDK 11+ on `PATH`.

Windows:
```powershell
./build.ps1
```

Linux/Mac:
```bash
./build.sh
```

Output: `../jars/redrobin-aggressor-http-1.0.2.jar` (sibling of the .cna,
co-located with `json-20240303.jar`).

## Test

Build script runs JUnit 5 tests before packaging (`RedRobinHttpTest`; the
`RedRobinEventStreamTest` companion was deleted with its subject). To run
tests only:

```powershell
# Windows
javac --release 11 -d build/classes (Get-ChildItem -Recurse src/*.java).FullName
javac --release 11 -cp "build/classes;lib/junit-platform-console-standalone-1.10.0.jar;lib/json-20240303.jar" -d build/test-classes (Get-ChildItem -Recurse test/*.java).FullName
java -jar lib/junit-platform-console-standalone-1.10.0.jar --class-path "build/classes;build/test-classes;lib/json-20240303.jar" --scan-class-path
```

Linux/Mac uses `:` path separator instead of `;` and `find ... -name '*.java'` in place of the PowerShell glob.
