#!/usr/bin/env bash
# Build redrobin-aggressor-http JAR. Requires JDK 11+ on PATH.
set -euo pipefail
cd "$(dirname "$0")"

VERSION="1.0.1"
JSON_JAR="lib/json-20240303.jar"
JAR_NAME="redrobin-aggressor-http-${VERSION}.jar"
JUNIT="lib/junit-platform-console-standalone-1.10.0.jar"

rm -rf build
mkdir -p build/classes build/test-classes build/jar

echo "Compiling src/..."
find src -name '*.java' -print0 | xargs -0 javac --release 11 -cp "$JSON_JAR" -d build/classes

echo "Compiling test/..."
find test -name '*.java' -print0 | xargs -0 javac --release 11 \
    -cp "build/classes:${JUNIT}:${JSON_JAR}" -d build/test-classes

echo "Running tests..."
java -jar "${JUNIT}" --class-path "build/classes:${JSON_JAR}:build/test-classes" --scan-class-path

echo "Packaging ${JAR_NAME}..."
jar --create --file "build/jar/${JAR_NAME}" -C build/classes .

PARENT_JARS_DIR="$(dirname "$PWD")/jars"
mkdir -p "${PARENT_JARS_DIR}"
cp "build/jar/${JAR_NAME}" "${PARENT_JARS_DIR}/"
cp "$JSON_JAR" "${PARENT_JARS_DIR}/"

echo "Built: ${PARENT_JARS_DIR}/${JAR_NAME}"
