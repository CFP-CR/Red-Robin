# redrobin-aggressor-http

Java helper JAR loaded by `adapters/cobaltstrike/redrobin.cna` to wrap
`java.net.http.HttpClient`. Sleep cannot reflect on the JDK's internal
HttpClient impl classes (`jdk.internal.net.http.*`) without
`--add-opens`, so we expose a stable public API in our own package
that the JAR's bytecode invokes directly.

See `docs/superpowers/specs/2026-05-17-cna-jar-helper-design.md` for
the full rationale.

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

Output: `../jars/redrobin-aggressor-http-1.0.0.jar` (sibling of the .cna).

## Test

Build script runs JUnit 5 tests before packaging. To run tests only:

```powershell
# Windows
javac --release 11 -d build/classes (Get-ChildItem -Recurse src/*.java).FullName
javac --release 11 -cp "build/classes;lib/junit-platform-console-standalone-1.10.0.jar" -d build/test-classes (Get-ChildItem -Recurse test/*.java).FullName
java -jar lib/junit-platform-console-standalone-1.10.0.jar --class-path "build/classes;build/test-classes" --scan-class-path
```
