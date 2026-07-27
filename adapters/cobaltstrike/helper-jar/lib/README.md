# Vendored test dependencies

## junit-platform-console-standalone-1.10.0.jar

Bundles JUnit 5 platform + Jupiter engine + console launcher. Required
for offline reproducible builds of `redrobin-aggressor-http.jar`.

**Source:** https://repo1.maven.org/maven2/org/junit/platform/junit-platform-console-standalone/1.10.0/junit-platform-console-standalone-1.10.0.jar

**SHA-256:** `8192d377bdb53969273ca3805af0eef3b864690f7818300c0d39ac7758da39a0`

Verified against Maven Central's published `.sha256` sidecar on download
(see https://repo1.maven.org/maven2/org/junit/platform/junit-platform-console-standalone/1.10.0/junit-platform-console-standalone-1.10.0.jar.sha256).

To re-verify locally:

```
sha256sum junit-platform-console-standalone-1.10.0.jar
```

**License:** Eclipse Public License 2.0

## json-20240303.jar

Compact JSON parser (`org.json`). Was used by `RedRobinEventStream` to
decode WebSocket frames; that class was deleted with the pre-flight gate, so
this dependency has no remaining source consumer. It stays vendored only
because the committed JAR has not been rebuilt yet and its bytecode still
references it — retire both together. Same library `PyCobalt` vendors
(`dcsync/pycobalt:aggressor/jars/json.jar`), so the pattern is established
in the CS Aggressor community.

**Source:** https://repo1.maven.org/maven2/org/json/json/20240303/json-20240303.jar

**SHA-256:** `3cf6cd6892e32e2b4c1c39e0f52f5248a2f5b37646fdfbb79a66b46b618414ed`

Maven Central does not publish a `.sha256` sidecar for this artifact;
authenticity verified against the published `.sha1`
(`0ebb88e8fb5122b7506d5cf1d69f1ccdb790d22a`) and `.md5`
(`99aae5de9871d158cdb970f17be4c8da`) sidecars — both matched the
downloaded bytes exactly, after which the SHA-256 above was computed
locally over those same bytes.

To re-verify locally:

```
sha256sum json-20240303.jar
```

**License:** [The JSON License](https://www.json.org/license.html) — public-use compatible with EPL/MIT/Apache stacks (CS Aggressor scripts and Red Robin are MIT).
