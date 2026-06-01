# Build redrobin-aggressor-http JAR.
# Requires JDK 11+ on PATH.
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$VERSION = "1.0.2"
$JSON_JAR = "lib\json-20240303.jar"
$JAR_NAME = "redrobin-aggressor-http-$VERSION.jar"
$JUNIT = "lib\junit-platform-console-standalone-1.10.0.jar"

# Clean
Remove-Item -Recurse -Force build -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path build\classes,build\test-classes,build\jar | Out-Null

# Compile main
Write-Host "Compiling src/..."
$srcFiles = (Get-ChildItem -Recurse src\*.java).FullName
javac --release 11 -cp "$JSON_JAR" -d build\classes $srcFiles
if ($LASTEXITCODE -ne 0) { throw "javac src failed" }

# Compile tests
Write-Host "Compiling test/..."
$testFiles = (Get-ChildItem -Recurse test\*.java).FullName
javac --release 11 -cp "build\classes;$JUNIT;$JSON_JAR" -d build\test-classes $testFiles
if ($LASTEXITCODE -ne 0) { throw "javac test failed" }

# Run tests
Write-Host "Running tests..."
java -jar $JUNIT --class-path "build\classes;$JSON_JAR;build\test-classes" --scan-class-path
if ($LASTEXITCODE -ne 0) { throw "Tests failed" }

# Package JAR
Write-Host "Packaging $JAR_NAME..."
jar --create --file "build\jar\$JAR_NAME" -C build\classes .

# Copy to sibling jars/ for the .cna to pick up
$ParentJarsDir = Join-Path (Split-Path -Parent $ScriptDir) "jars"
New-Item -ItemType Directory -Force -Path $ParentJarsDir | Out-Null
Copy-Item "build\jar\$JAR_NAME" -Destination $ParentJarsDir -Force
# Also copy the vendored json jar — adapter loads both via URLClassLoader
Copy-Item $JSON_JAR -Destination $ParentJarsDir -Force

Write-Host "Built: $ParentJarsDir\$JAR_NAME"
