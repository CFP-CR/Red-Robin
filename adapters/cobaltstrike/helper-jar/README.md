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

## Shipped classes (as of 1.0.2)

- `com.redrobin.aggressor.RedRobinHttp` — synchronous POST/GET against the
  daemon (handshake, verdict, events, heartbeat) + the schema-required
  ULID generator `ulid()` (Crockford b32; 48-bit ts + 80-bit randomness).
- `com.redrobin.aggressor.RedRobinEventStream` — WebSocket subscriber
  for the daemon's `/v1/adapters/events` endpoint (Layer B verdicts).
  Auto-reconnect with exponential backoff (1s → 60s); drop-oldest
  backpressure on a 256-entry queue Sleep polls via `pollEvent(ms)`.
- `com.redrobin.aggressor.VerdictEvent` — DTO for opsec.verdict payloads
  delivered over the WebSocket.

## Dependencies (vendored in `lib/`)

- `junit-platform-console-standalone-1.10.0.jar` — test-only (JUnit 5).
- `json-20240303.jar` — runtime (org.json). Used by `RedRobinEventStream`
  to decode WebSocket frames. Also shipped sibling to the helper JAR in
  `adapters/cobaltstrike/jars/json-20240303.jar` for the .cna's
  `URLClassLoader`.

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

Build script runs JUnit 5 tests before packaging
(`RedRobinHttpTest` + `RedRobinEventStreamTest`). To run tests only:

```powershell
# Windows
javac --release 11 -d build/classes (Get-ChildItem -Recurse src/*.java).FullName
javac --release 11 -cp "build/classes;lib/junit-platform-console-standalone-1.10.0.jar;lib/json-20240303.jar" -d build/test-classes (Get-ChildItem -Recurse test/*.java).FullName
java -jar lib/junit-platform-console-standalone-1.10.0.jar --class-path "build/classes;build/test-classes;lib/json-20240303.jar" --scan-class-path
```

Linux/Mac uses `:` path separator instead of `;` and `find ... -name '*.java'` in place of the PowerShell glob.
